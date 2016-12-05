#!/usr/bin/env python

import web
import json
import uuid
import ConfigParser
import feedparser
import requests
import argparse
import socket

from time import mktime, struct_time
from datetime import datetime, timedelta
from urlparse import urlparse, urlunparse, ParseResult
from os import path
from lxml import etree
from StringIO import StringIO
from passlib.apps import custom_app_context as pwd_context
from base64 import b64decode

version = "0.0.1"
api_level = -1
config = ConfigParser.RawConfigParser()
config.read('nvtrss.cfg')
debug = config.getboolean('server', 'debug')
updater_secret = config.get('updater', 'secret')
freshhours = config.getint('server', 'freshhours')
maxsessionage = config.getint('server', 'maxsessionage') #minutes
update_dateless = config.getboolean('updater', 'update_dateless')

dbconfig = {}
for option in config.options('database'):
    dbconfig[option] = config.get('database', option)

db = web.database(**dbconfig)
        
urls = (
    '/api', 'api',
    '/api/', 'api',
    '/feeds', 'feeds',
)
app = web.application(urls, globals())

render = web.template.render('templates/')

db.printing = debug
web.config.debug = debug

class ApiError(Exception):
    """Base class for exceptions in this module."""
    def __init__(self, msg):
        self.msg = msg

    def __str__(self):
        return repr(self.msg)


class SessionError(ApiError):
    """Raised when session id is not valid."""
    def __init__(self, msg, sid=None):
        self.msg = msg
        self.sid = sid

    def __str__(self):
        return repr(self.msg)


class OwnershipError(SessionError):
    """Raised when session id is not valid for item."""
    def __init__(self, msg, sid=None, user_id=None,
                 item_id=None, feed_id=None, cat_id=None):
        self.msg = msg
        self.sid = sid
        self.user_id = user_id
        self.item_id = item_id
        self.item_id = feed_id
        self.item_id = cat_id

    def __str__(self):
        return repr(self.msg)



def checksession(sid):
    """Checks for valid sid and returns user_id."""
    try:
        session = db.select('sessions',
                            where="sid=$sid",
                            vars={'sid': sid})[0]
    except IndexError:
        raise SessionError("Not a valid session", sid)
    sessionage = datetime.utcnow() - session.lastused
    if sessionage < timedelta(minutes=maxsessionage):
        if sessionage > timedelta(minutes=5):
            db.update('sessions',
                      where="sid=$sid",
                      lastused=datetime.utcnow(),
                      vars={'sid': sid})
        return session.user_id
    else:
        db.delete('sessions',
                  where="lastused < $sessionexpirey",
                  vars={'sessionexpirey': datetime.utcnow() - timedelta(hours=1)})
        raise SessionError("Not a valid session", sid)

def checkweblogin():
    """Called from web pages (not api) to check authentication."""
    sid = web.cookies().get('sid')
    if not sid:
        try:
            username, password = b64decode(web.ctx.env['HTTP_AUTHORIZATION'][6:]).split(':')
            sid = login(username, password)['session_id']
            web.setcookie('sid', sid, maxsessionage)
        except KeyError:
            pass
        except ApiError:
            pass
    user_id = None
    if sid:
        try:
            user_id = checksession(sid)
        except SessionError:
            pass
    if not user_id:
        web.header('WWW-Authenticate','Basic realm="nvtrss"')
        raise web.HTTPError("401 unauthorized", {}, "Please provide a username & password.")
    return user_id, sid


def ownerofcat(cat_id):
    return db.select('categories',
                     what="user_id",
                     where="cat_id=$cat_id",
                     vars={'cat_id': cat_id})[0].user_id

def owneroffeed(feed_id):
    return db.select('feeds',
                     what="user_id",
                     where="feed_id=$feed_id",
                     vars={'feed_id': feed_id})[0].user_id

def ownerofitem(item_id):
    return db.query("""select user_id from items
                       join feeds
                         on feeds.feed_id=items.feed_id
                       where item_id=$item_id""",
                    vars={'item_id': item_id})[0].user_id

def splitarticleids(article_ids):
    try:
        return [int(x) for x in article_ids.split(',') if x]
    except AttributeError:
        return [int(article_ids),]

def freshcutoff():
    threshold = timedelta(hours=freshhours)
    return datetime.utcnow() - threshold

def countunread(user_id, feed_id=None, uncategorised=False, fresh=False):
    #TODO: freshness... younger than date?
    SQL = """select count() as count from items
             join feeds
               on feeds.feed_id=items.feed_id
             where user_id=$user_id
               and read is null"""
    variables = {'user_id': user_id}
    if feed_id:
        SQL += str(" and feeds.feed_id=$feed_id")
        variables['feed_id'] = feed_id
    if uncategorised:
        SQL += str(" and feeds.cat_id IS NULL")
    if fresh:
        SQL += str(" and items.published > $published")
        variables['published'] = freshcutoff()
    result = db.query(SQL, vars=variables)[0].count
    return result

def article(row):
    return {'id': row.item_id,
            'feed_id': row.feed_id,
            'unread': not bool(row.read),
            'updated': row.updated.strftime('%s') if row.updated else 0,
            'title': row.title,
            'link': row.link,
            'feed_title': row.feed_title,
            #TODO: tags
            'excerpt': row.description if row.content else None,
            'content': row.content if row.content else row.description,
            }

def checksubscribe(feed_url, user_id):
    try:
        return db.select('feeds',
                         where="url=$feed_url AND user_id=$user_id",
                         what="feed_id",
                         vars={'feed_url': urlunparse(feed_url),
                               'user_id': user_id}
                         )[0].feed_id
    except IndexError:
        return False

def update_lastupdate(feed_id):
    db.update('feeds', where="feed_id=$feed_id", vars={'feed_id': feed_id}, lastupdate=datetime.utcnow())

def updatefavicon(feed_url, feed_id):
    url = urlparse(feed_url)
    base_url = urlunparse(ParseResult(url[0], url[1], '', None, None, None))
    favicon_path = 'favicon.ico'
    result = requests.get(base_url)
    parser = etree.HTMLParser()
    tree = etree.parse(StringIO(result.text), parser)
    try:
        favicon_path = tree.xpath('.//link[@rel="shortcut icon"]')[0].attrib['href']
    except IndexError:
        try:
            favicon_path = tree.xpath('.//link[@rel="icon"]')[0].attrib['href']
        except IndexError:
            pass
    favicon_url = urlunparse(ParseResult(url[0], url[1], favicon_path, None, None, None))
    try:
        favicon = requests.get(favicon_url)
    except Exception as e: #FIXME: could deal with this better...
        logging.warning(e)
        return False

    extension = path.splitext(favicon_path)[1]
    if extension in ['.ico', '.png', '.svg', '.gif', '.apng']:
        stored_filename = "%i%s" % (feed_id, extension)
        stored_path = path.join('static', 'feed-icons', stored_filename)

        with open(stored_path, 'wb') as f:
            for i in favicon.iter_content(chunk_size=1024):
                if i: # filter out keep-alive new chunks
                    f.write(i)
                    f.flush()
        has_icon = True
    else:
        has_icon = False

    db.update('feeds',
              where="feed_id=$feed_id",
              icon_updated=datetime.utcnow(),
              has_icon=has_icon,
              vars={'feed_id': feed_id})


def updatefeed(feed, foreground=True):
    # FIXME: Lots of problems here, need to sanitise if test/plain
    result = feedparser.parse(feed.url, etag=feed.etag, modified=feed.last_modified)
    if result.status == 304:
        return False
    if foreground:
        return processentries(feed, result)
    else:
        return result

def processentries(feed, result):
    """result is the feedparser.parse() result"""
    newitems = []
    for entry in result.get('entries'):
        published = entry.get('published_parsed', None)
        if published:
            published = datetime.fromtimestamp(mktime(published))
        updated = entry.get('updated_parsed', None)
        if updated:
            updated = datetime.fromtimestamp(mktime(updated))
        else:
            updated = published
        content = None
        guid = entry.get('id', entry.get('title', None))
        content = entry.get('content', [{}])[0].get('value', None)
        description = entry.get('summary', None)
        link = entry.get('link', None)
        try:
            item = db.select('items',
                             where="feed_id=$feed_id AND guid=$guid",
                             vars={'feed_id': feed.feed_id, 'guid': guid})[0]
            item_id = item.item_id
            if updated > item.updated or (update_dateless and updated is None):
                db.update('items',
                          where="guid=$guid",
                          title=entry.get('title'),
                          description=description,
                          link=link,
                          published=published if published else datetime.utcnow(),
                          updated=updated if updated else datetime.utcnow(),
                          content=content,
                          vars={'guid': guid})
        except IndexError:
            item_id = db.insert('items',
                                feed_id=feed.feed_id,
                                title=entry.get('title'),
                                description=description,
                                link=link,
                                published=published if published else datetime.utcnow(),
                                updated=updated if updated else datetime.utcnow(),
                                content=content,
                                guid=guid)
        newitems.append(item_id)
    if not feed.icon_updated or feed.icon_updated > (datetime.utcnow() - timedelta(days=7)):
        updatefavicon(feed.url, feed.feed_id)
    feed_title = result.get('feed').get('title', feed.url)
    etag = result.get('etag', None)
    last_modified = result.get('modified', None)
    if etag or last_modified or feed_title != feed.feed_title:
        db.update('feeds',
                  where="feed_id=$feed_id",
                  feed_title=feed_title,
                  etag=etag,
                  last_modified=last_modified,
                  vars={'feed_id': feed.feed_id})
    return newitems

def json_serial(obj):
    """JSON serializer for objects not serializable by default json code

    http://stackoverflow.com/a/22238613/601779"""

    if isinstance(obj, struct_time):
        return list(obj)
    if isinstance(obj, datetime):
        return obj.timetuple()
    if isinstance(obj, feedparser.NonXMLContentType):
        return repr(obj)
    print "not serialisable:", type(obj), "|", obj
    raise

def separateupdate(url):
    payload = {"op": "updateFeed", "secret": updater_secret, "background": False}
    result = requests.post(url, data=json.dumps(payload))
    if result.status_code != requests.codes.ok:
        return "Initial Local Request", url, result.text
    feed = result.json['content']['feed']
    result = feedparser.parse(feed['url'], etag=feed['etag'], modified=feed['last_modified'])
    if result.bozo:
        return "Feed Fetch", feed['url'], result.bozo_exception
    payload = {"op": "updateFeed",
               "secret": updater_secret,
               "background": False,
               "feed_id": feed['feed_id'],
               "result": result}
    result = requests.post(url, data=json.dumps(payload, default=json_serial))
    if result.status_code != requests.codes.ok:
        return "POST to local", feed['url'], result.text
    return feed['url'], result.json


##
# API Function:
# See http://tt-rss.org/redmine/projects/tt-rss/wiki/JsonApiReference
##

def getApiLevel(sid, **args):
    checksession(sid)
    return {'level': api_level}

def getVersion(sid, **args):
    checksession(sid)
    return {'version': version}

def login(user=None, password=None, **args):
    #TODO: session handling
    try:
        result = db.select('users',
                           what='user_id,hash',
                           where="username=$username",
                           vars={'username': user}
                           )[0]
    except IndexError:
        raise ApiError("LOGIN_ERROR")
    if not pwd_context.verify(password, result.hash):
        raise ApiError("LOGIN_ERROR")
    sid = uuid.uuid1().hex
    db.insert('sessions',
              sid=sid,
              user_id=result.user_id,
              lastused=datetime.utcnow())
    return {'session_id': sid,
            'api_level': api_level}

def logout(sid, **args):
    if checksession(sid):
        #TODO: logout
        db.delete('sessions',
                  where="sid=$sid",
                  vars={'sid': sid})
        return {"status":"OK"}
    return {"status": "false"}

def isLoggedIn(sid, **args):
    try:
        if checksession(sid):
            return {"status":True}
        else:
            return {"status":False}
    except SessionError:
        return {"status": False}

def getUnread(sid, **args):
    user_id = checksession(sid)
    result = countunread(user_id)
    return {'unread':str(result)}

def getFeeds(sid, cat_id=None, offset=None, limit=None, unread_only=True, **args):
    user_id = checksession(sid)
    #TODO: parameters: cat_id, unread_only, offset, include_nested
    query = """select count(*) as count_unread, feeds.* from items
               left join feeds on items.feed_id = feeds.feed_id
               where user_id=$user_id"""
    variables = {'user_id': user_id}
    if cat_id is not None:
        cat_id = int(cat_id)
        if cat_id in (-4, -3): #FIXME: Labels
            pass
        elif cat_id == -2:
            raise web.notfound()
        elif cat_id == 0:
            query += " and feeds.cat_id IS NULL"
        else: # -1 is dealt with below.
            query += " and cat_id=$cat_id"
            variables['cat_id'] = cat_id
    else:
        cat_id = None
    query += " and items.read IS NULL"
    # Splice?
    if limit:
        variables['limit'] = int(limit)
        query += " limit $limit"
    query += " group by items.feed_id"
    if unread_only:
        query += " having count_unread > 0"
    feeds = []
    if cat_id == -1:
        # We only want specials...
        pass
    else:
        result = db.query(query, vars=variables)
        if offset:
            offset = int(offset)
        else:
            offset = 0
        #TODO: feeds order_id
        order_id = 0
        for feed in result:
            if feed.lastupdate:
                lastupdate = feed.lastupdate.strftime("%s")
            else:
                lastupdate = None
            feeds.append({'feed_url': feed.url,
                          'title': feed.feed_title,
                          'id': feed.feed_id,
                          'unread': feed.count_unread,
                          'has_icon': bool(feed.has_icon),
                          'cat_id': feed.cat_id,
                          'last_updated': lastupdate,
                          'order_id': order_id})
            order_id += 1
    if cat_id is None or cat_id == -1 or cat_id == -4:
        feeds.append({'id': -4,
                     'title': "All articles",
                     'unread': countunread(user_id),
                     'cat_id': -1})
        feeds.append({'id': -3,
                      'title': "Fresh articles",
                      'unread': countunread(user_id, fresh=True),
                      'cat_id': -1})
    return feeds


def getCategories(sid, unread_only=None, enable_nested=None, include_empty=None, **args):
    # TODO: parameters: include_empty
    user_id = checksession(sid)
        
    query = """SELECT count(*) AS count_unread, categories.* FROM items
               LEFT JOIN feeds ON items.feed_id = feeds.feed_id
               LEFT JOIN categories ON categories.cat_id = feeds.cat_id
               WHERE categories.user_id=$user_id
               AND items.read IS NULL"""
    if enable_nested:
        query += " AND parent IS NULL"""
    query += " GROUP BY categories.cat_id"
    if unread_only:
        query += " HAVING count_unread > 0"
    categories = db.query(query, vars={'user_id': user_id})
    result = []
    order_id = 0
    category = {'id': -1,
                'title': "Special",
                'unread': countunread(user_id),
                'cat_id': -1,
                'order_id': order_id}
    if not include_empty or category['unread'] > 0:
        result.append(category)
    for category in categories:
        order_id += 1
        result.append({'id': category.cat_id,
                       'title': category.name,
                       'unread': category.count_unread,
                       'order_id': order_id})
    category = {'id': 0,
                'title': "Uncategorised",
                'unread': countunread(user_id, uncategorised=True),
                'cat_id': 0,
                'order_id': order_id}
    if not include_empty or category['unread'] > 0:
        result.append(category)
    return result


def getHeadlines(sid, feed_id=None, limit=None, view_mode=None, order_by=None, **args):
    #TODO: parameters: skip, is_cat, show_excerpt, show_content, view_mode
    #TODO: parameters: include_attachments, since_id, include_nested
    user_id = checksession(sid)
    query = """select * from feeds,items """
    if dbconfig['dbn'] == 'sqlite':
        query += str(" indexed by idx_items_timestamps")
    query += str(""" where feeds.feed_id=items.feed_id
                       and feeds.user_id=$user_id""")
    variables = {'user_id': user_id}
    if feed_id:
        feed_id = int(feed_id)
        if feed_id > 0:
            variables['feed_id'] = feed_id
            query += str(" and items.feed_id=$feed_id")
        elif feed_id == 0: # TODO: all the others...
            # FIXME: uncategorized or archived? Unclear
            query += str(" and feeds.cat_id IS NULL")
        elif feed_id == -3: #fresh only
            query += str(" and items.published > $published")
            variables['published'] = freshcutoff()
    if limit:
        limit = int(limit)
        if limit < 201:
            variables['limit'] = limit
        else:
            variables['limit'] = 20
    else:
        variables['limit'] = 20
    if view_mode in ['adaptive', 'unread']:
        query += str(" and items.read is NULL")
    if order_by == "date_reverse":
        query += str(" order by items.updated ASC, items.published ASC")
    else:
        query += str(" order by items.updated DESC, items.published DESC")
    query += str(" limit $limit")
    results = db.query(query, vars=variables)
    headlines = []
    for row in results:
        headlines.append(article(row))
    return headlines

item_fields = ['starred', 'published', 'read'] # TODO: article?!

def updateArticle(sid, article_ids, mode, field, **args):
    #TODO: all
    user_id = checksession(sid)
    article_ids = splitarticleids(article_ids)
    mode = int(mode)
    field = int(field)
    result = []
    count = 0
    for item_id in article_ids:
        #TODO: check article is user's...
        if ownerofitem(item_id) == user_id:
            if mode == 0:
                if field == 2:
                    # parameter is 'unread' but we store read so negate:
                    real_mode = datetime.utcnow()
                else:
                    real_mode = None
            elif mode == 1:
                if field == 2:
                    # parameter is 'unread' but we store read so negate:
                    real_mode = None
                else:
                    real_mode = datetime.utcnow()
            elif mode == 2:
                if db.select('items',
                             where='item_id=$item_id',
                             what=item_fields[field],
                             vars={'item_id': item_id})[0][item_fields[field]]:
                    real_mode = None
                else:
                    real_mode = datetime.utcnow()
            db.query('update items set %s=$mode where item_id=$item_id' % (item_fields[field],),
                     vars={'mode': real_mode, 'item_id': item_id})
            count += 1
        else:
            raise OwnershipError("Not a valid session for article.",
                                 sid=sid, user_id=user_id, item_id=item_id)
    return {'status': 'OK', 'updated': count}

def getArticle(sid, article_id, **args):
    user_id = checksession(sid)
    if not article_id:
        raise web.notfound()
    article_ids = splitarticleids(article_id)
    articles = []
    for item_id in article_ids:
        if user_id == ownerofitem(item_id):
            item = db.query("""select * from items
                               join feeds on feeds.feed_id=items.feed_id
                               where items.item_id=$item_id""",
                             vars={'item_id': item_id})[0]
            articles.append(article(item))
    return articles

def subscribeToFeed(sid, feed_url, **args):
    user_id = checksession(sid)
    feed_url = urlparse(feed_url)
    if not feed_url.netloc:
        raise ApiError("Must specify a full valid url including scheme.")
    if checksubscribe(feed_url, user_id):
        raise ApiError("Already susbcribed to this feed.")
    #TODO: cat_id
    db.insert('feeds', url=urlunparse(feed_url), user_id=user_id, cat_id=0)

def getConfig(sid, **args):
    user_id = checksession(sid)
    num_feeds = db.query("""select count() as count
                              from feeds
                            where user_id=$user_id""",
                         vars={'user_id': user_id})[0].count
    updatefrequency = timedelta(minutes=config.getint('updater', 'frequency'))
    updateperiod = datetime.utcnow() - updatefrequency
    if db.query("""select count() as count
                     from feeds
                   where lastupdate>$updateperiod""",
                   vars={'updateperiod': updateperiod})[0].count:
        daemon_is_running = True
    else:
        daemon_is_running = False
    return {'icons_dir': path.join('static', 'feed-icons'),
            'icons_url': 'static/feed-icons',
            'daemon_is_running': daemon_is_running,
            'num_feeds': num_feeds}

def updateFeed(sid, feed_id=None, background=True, result=None, **args):
    if updater_secret and 'secret' in args and updater_secret == args['secret']:
        pass
    else:
        background = True
        user_id = checksession(sid)
    if feed_id:
        feed = db.select('feeds',
                         where="feed_id=$feed_id",
                         vars={'feed_id': feed_id})[0]
    else:
        feed = db.select('feeds',
                         limit=1,
                         order='lastupdate ASC')[0]
    update_lastupdate(feed.feed_id) # If anything goes wrong, we won't retry.
    if background:
        newitems = updatefeed(feed)
    else:
        if result:
            newitems = processentries(feed, result)
        else:
            return {"status":"OK", "feed": {"feed_id": feed.feed_id,
                                            "url": feed.url,
                                            "etag": feed.etag,
                                            "last_modified": feed.last_modified
                                            }}
    return {"status":"OK", "updated": {int(feed.feed_id): newitems }}

def getPref(sid, pref_name, **args):
    user_id = checksession(sid)
    return {'value': False} #FIXME: Probably want to provide real prefs..

def catchupFeed(sid, feed_id, is_cat=None, **args):
    user_id = checksession(sid)
    if is_cat:
        cat_id=feed_id
        feed_ids=[]
        if user_id == ownerofcat(cat_id):
            for feed in db.select('feeds',
                                  where="cat_id=$cat_id",
                                  vars={'cat_id': cat_id}):
                feed_ids.append(feed.feed_id)
        else:
            raise OwnershipError("Not a valid session for feed.",
                                 user_id=user_id, cat_id=cat_id)
    else:
        feed_ids=[feed_id,]
    for feed_id in feed_ids:
        if user_id == owneroffeed(feed_id):
            db.update('items',
                      where="""feed_id=$feed_id
                               AND read IS NULL""",
                      read=datetime.utcnow(),
                      vars={'feed_id': feed_id})
        else:
            raise OwnershipError("Not a valid session for feed.",
                                 user_id=user_id, feed_id=feed_id)

def unsubscribeFeed(sid, feed_id, **args):
    user_id = checksession(sid)
    if user_id == owneroffeed(feed_id):
        db.delete('feeds', where="feed_id=$feed_id", vars={'feed_id': feed_id})
    else:
        raise OwnershipError("Not a valid session for feed.",
                             user_id=user_id, feed_id=feed_id)

def getCounters(sid, output_mode, **args):
    user_id = checksession(sid)
    if not output_mode:
        output_mode = 'flc'
    counters = []
    counters.append({'id': 'global-unread',
                     'counter': countunread(user_id)})

    counters.append({'id': 'subscribed-feeds',
                     'counter': db.query("""select count() as count from feeds
                                            where user_id=$user_id""",
                                         vars={'user_id': user_id})[0].count})

    if 'f' in output_mode:
        for feed in getFeeds(sid):
            counter = {'id': feed['id'],
                       'counter': feed['unread'],} 
            #TODO: error values
            try:
                counter['updated'] = feed['last_updated'] #TODO: hh:mm format
            except KeyError:
                pass
            try:
                counter['has_icon'] = feed['has_icon']
            except KeyError:
                pass
            counters.append(counter)
    if 't' in output_mode:
        pass #TODO: implement getCounters t
    if 'l' in output_mode:
        pass #TODO: implement getCounters l
    if 'c' in output_mode:
        pass #TODO: implement getCounters c

    return counters

def getFeedTree(sid, **args): #TODO: include_empty
    user_id = checksession(sid)
        
    query = """SELECT count(*) AS count_unread, categories.*, feeds.*
               FROM items
               LEFT JOIN feeds ON items.feed_id = feeds.feed_id
               LEFT JOIN categories ON categories.cat_id = feeds.cat_id
               WHERE categories.user_id=$user_id
               AND items.read IS NULL
               GROUP BY feeds.feed_id
               ORDER BY categories.cat_id ASC, feeds.feed_id ASC"""
    feeds = db.query(query, vars={'user_id': user_id})

    category_dict = {}
    for item in feeds:
        if item.cat_id not in category_dict:
            category_dict[item.cat_id] = {'id':"CAT:%i" % item.cat_id,
                                          'bare_id': item.cat_id,
                                          #TODO: 'auxcounter': 0 ??
                                          'name': item.name,
                                          'items': [],
                                          #TODO: 'checkbox': False, ??
                                          'type': 'category',
                                          'unread': 0,
                                          'child_unread': 0,
                                          #TODO: 'param': '(1 feed)' ??
                                          }
        category_dict[item.cat_id]['items'].append({'id': 'FEED:%i' % item.feed_id,
                                                    'bare_id': item.feed_id,
                                                    #TODO: 'auxcounter': 0 ??
                                                    'name': item.feed_title,
                                                    #TODO: 'checkbox': False, ??
                                                    'unread': 0,
                                                    'error': '',
                                                    'icon': False, #TODO: icon
                                                    #TODO: 'param': "16:19"
                                                    })
    category_dict[-1] = {'id':"CAT:-1",
                         'items': [
                                    {'id': 'FEED:-4',
                                     'bare_id': -4,
                                     #TODO: 'auxcounter': 0 ??
                                     'name': "All articles",
                                     #TODO: 'checkbox': False, ??
                                     'unread': countunread(user_id),
                                     'error': '',
                                     'icon': False, #TODO: icon
                                     #TODO: 'param': "16:19"
                                     },
                                    {'id': 'FEED:-3',
                                     'bare_id': -3,
                                     #TODO: 'auxcounter': 0 ??
                                     'name': "Fresh articles",
                                     #TODO: 'checkbox': False, ??
                                     'unread': countunread(user_id, fresh=True),
                                     'error': '',
                                     'icon': False, #TODO: icon
                                     #TODO: 'param': "16:19"
                                     },
                                    ],
                         'name': "Special",
                         'type': "category",
                         'unread': 0,
                         'bare_id': -1,
                         }
    content = { 'categories': {
                               'identifier': 'id',
                               'label': 'name',
                               'items': [category_dict[category] for category in category_dict]
                               }
               }
    return content


#TODO: getLabels 1
#TODO: setArticleLabel 1
#TODO: shareToPublished 4


    
apifunctions = {'getApiLevel': getApiLevel,
                'getVersion': getVersion,
                'login': login,
                'logout': logout,
                'isLoggedIn': isLoggedIn,
                'getUnread': getUnread,
                'getFeeds': getFeeds,
                'getHeadlines': getHeadlines,
                'updateArticle': updateArticle,
                'getArticle': getArticle,
                'getCategories': getCategories,
                'subscribeToFeed': subscribeToFeed,
                'getConfig': getConfig,
                'updateFeed': updateFeed,
                'getPref': getPref,
                'catchupFeed': catchupFeed,
                'unsubscribeFeed': unsubscribeFeed,
                'getCounters': getCounters,
                'getFeedTree': getFeedTree,
               }

class api:
    def POST(self):
        #print "input=%s" % web.data()
        jsoninput = json.loads(web.data())
        output = {}
        # FIXME: seq in docs is shown as url parameter?
        if 'seq' in jsoninput:
            output['seq'] = jsoninput['seq']
        else:
            output['seq'] = 0

        web.header('Content-Type', 'text/json')
        web.header('Cache-Control', 'no-store, no-cache, must-revalidate, post-check=0, pre-check=0')

        # If not set, set it so we can check for... nothing and not KeyError.
        if not 'sid' in jsoninput:
            jsoninput['sid'] = None

        if 'op' in jsoninput:
            try:
                output['content'] = apifunctions[jsoninput['op']](**jsoninput)
                output['status'] = 0
            except KeyError:
                output['status'] = 1
            except SessionError:
                output['status'] = 1
                output['content'] = {'error': 'NOT_LOGGED_IN'}
            except ApiError, e:
                output['status'] = 1
                output['content'] = {'error': e.msg}
        else:
            output['status'] = 1
            
        #print "output=%s" % json.dumps(output)
        return json.dumps(output)

FEEDFORM = web.form.Form(
    web.form.Textbox('url'),
    web.form.Radio('action',
                   ['add', 'delete'],
                   value="add")
    )

class feeds:
    def GET(self):
        user_id, sid = checkweblogin()
        feedlist = db.select("feeds", where="user_id=$user_id", vars={'user_id': user_id})
        feedform = FEEDFORM()
        return render.feeds(feedlist, feedform)
    def POST(self):
        user_id, sid = checkweblogin()
        feedform = FEEDFORM()
        if not feedform.validates():
            return RENDER.formtest(feedform)
        if feedform.d.action == 'add':
            subscribeToFeed(sid, feedform.d.url)
        elif feedform.d.action == 'delete':
            feed_id = db.select("feeds", where="user_id=$user_id AND url=$url", what="feed_id", vars={'user_id': user_id, 'url': feedform.d.url})[0].feed_id
            unsubscribeFeed(sid, feed_id)
        raise web.seeother(web.ctx.path)

            
        
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', dest='updater', metavar='URL', default=False, action="store", help="don't run the server, run an updater")
    args = parser.parse_args()
    if args.updater:
        socket.setdefaulttimeout(10)
        print separateupdate(args.updater)
    else:
        app.run()

if __name__ == "__main__":
    main()

# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4
