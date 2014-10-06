#!/usr/bin/env python

import web
import json
import uuid
import ConfigParser
import feedparser
import requests

from time import mktime
from datetime import datetime, timedelta
from urlparse import urlparse, urlunparse, ParseResult
from os import path
from lxml import etree
from StringIO import StringIO

version = "0.0.1"
api_level = -1
config = ConfigParser.RawConfigParser()
config.read('nvtrss.cfg')
debug = config.getboolean('server', 'debug')

dbconfig = {}
for option in config.options('database'):
    dbconfig[option] = config.get('database', option)

db = web.database(**dbconfig)
        
urls = (
    '/api', 'api',
    '/api/', 'api',
)
app = web.application(urls, globals())

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
    if sessionage < timedelta(hours=1):
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
    threshold = timedelta(hours=3) #better value?
    return int((datetime.utcnow() - threshold).strftime('%s')) #FIXME: ewww.

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
            'updated': row.updated.strftime('%s'),
            'title': row.title,
            'link': row.link,
            'feed_title': row.feed_title,
            #TODO: tags
            'excerpt': row.description,
            'content': row.content,
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


def updatefeed(feed):
    update_lastupdate(feed.feed_id) # If anything goes wrong, we won't retry.
    result = feedparser.parse(feed.url, etag=feed.etag, modified=feed.last_modified)
    if result.status == 304:
        return False
    db.update('feeds',
              where="feed_id=$feed_id",
              feed_title=result.feed.get('title', feed.url),
              etag=result.get('etag', None),
              last_modified=result.get('modified', None),
              vars={'feed_id': feed.feed_id})
    for entry in result.entries:
        published = entry.get('published_parsed', None)
        if published:
            published = datetime.fromtimestamp(mktime(published))
        updated = entry.get('updated_parsed', None)
        if updated:
            updated = datetime.fromtimestamp(mktime(updated))
        content = None
        if entry.description:
            content = entry.description
        try:
            for c in entry.content:
                if content:
                    content+=c.value
                else:
                    content=c.value
        except AttributeError:
            #Not an atom feed
            pass
        guid = entry.get('id', entry.title)
        try:
            item = db.select('items',
                             where="feed_id=$feed_id AND guid=$guid",
                             vars={'feed_id': feed.feed_id, 'guid': guid})[0]
            item_id = item.item_id
            if not item.updated or updated > item.updated:
                db.update('items',
                          where="guid=$guid",
                          title=entry.title,
                          description=entry.description,
                          link=entry.link,
                          published=published,
                          updated=updated,
                          content=content,
                          vars={'guid': guid})
        except IndexError:
            item_id = db.insert('items',
                                feed_id=feed.feed_id,
                                title=entry.title,
                                description=entry.description,
                                link=entry.link,
                                published=published,
                                updated=updated,
                                content=content,
                                guid=guid)
    if not feed.icon_updated or feed.icon_updated > (datetime.utcnow() - timedelta(days=7)):
        updatefavicon(feed.url, feed.feed_id)


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

def login(user=None, **args):
    #TODO: session handling
    try:
        user_id = db.select('users',
                            what='user_id',
                            where="username=$username",
                            vars={'username': user}
                            )[0].user_id
    except IndexError:
        raise ApiError("LOGIN_ERROR")
    sid = uuid.uuid1().hex
    db.insert('sessions',
              sid=sid,
              user_id=user_id,
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

def getFeeds(sid, cat_id=None, offset=None, limit=None, **args):
    user_id = checksession(sid)
    #TODO: parameters: cat_id, unread_only, offset, include_nested
    query = """select * from feeds
               where user_id=$user_id"""
    variables = {'user_id': user_id}
    if cat_id:
        cat_id = int(cat_id)
        if cat_id == -4:
            pass
        elif cat_id == 0:
            query += " and feeds.cat_id IS NULL"
        else: # TODO: -1, -2, -3?
            query += " and cat_id=$cat_id"
            variables['cat_id'] = cat_id
    else:
        cat_id = None
    # Splice?
    if limit:
        variables['limit'] = int(limit)
        query += " limit $limit"
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
            unread = countunread(user_id, feed.feed_id)
            if feed.lastupdate:
                lastupdate = feed.lastupdate.strftime("%s")
            else:
                lastupdate = None
            feeds.append({'feed_url': feed.url,
                          'title': feed.feed_title,
                          'id': feed.feed_id,
                          'unread': unread,
                          'has_icon': feed.has_icon,
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
        
    if enable_nested:
        categories = db.select('categories',
                               where="""user_id=$user_id
                                        AND parent IS NULL""",
                               vars={'user_id': user_id})
    else:
        categories = db.select('categories',
                               where="user_id=$user_id",
                               vars={'user_id': user_id})
    result = []
    result.append({'id': -1,
                  'title': "Special",
                  'unread': countunread(user_id),
                  'cat_id': -1})
    result.append({'id': 0,
                  'title': "Uncategorised",
                  'unread': countunread(user_id, uncategorised=True),
                  'cat_id': -0})
    #TODO: order_id
    order_id = 0
    for category in categories:
        unread = db.query("""select count() as count from feeds
                             join items
                               on items.feed_id=feeds.feed_id
                             where feeds.cat_id=$cat_id
                               and feeds.user_id=$user_id
                               and items.read is NULL;""",
                          vars={'user_id': user_id,
                                'cat_id': category.cat_id})[0].count
        if unread_only and not unread:
            continue
        result.append({'id': category.cat_id,
                       'title': category.name,
                       'unread': unread,
                       'order_id': order_id})
        order_id += 1
    return result


def getHeadlines(sid, feed_id=None, limit=None, view_mode=None, **args):
    #TODO: parameters: skip, is_cat, show_excerpt, show_content, view_mode
    #TODO: parameters: include_attachments, since_id, include_nested, order_by
    user_id = checksession(sid)
    query = """select * from items
               join feeds
                 on feeds.feed_id=items.feed_id
               where feeds.user_id=$user_id"""
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
        variables['limit'] = 200
    if view_mode in ['adaptive', 'unread']:
        query += str(" and items.read is NULL")
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

def getArticle(sid, article_id, **args):
    user_id = checksession(sid)
    article_ids = splitarticleids(article_ids)
    articles = []
    for item_id in article_ids:
        if user_id == ownerofitem(item_id):
            item = db.select('items',
                             where="item_id=$item_id",
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

def updateFeed(sid, feed_id=None, **args):
    user_id = checksession(sid)
    if feed_id:
        feed = db.select('feeds',
                         where="feed_id=$feed_id",
                         vars={'feed_id': feed_id})[0]
    else:
        feed = db.select('feeds',
                         limit=1,
                         order='lastupdate ASC')[0]
    updatefeed(feed)
    return {"status":"OK"}

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
        db.delete('feeds', where="feed_id=$feed_id")
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


#TODO: getLabels 1
#TODO: setArticleLabel 1
#TODO: shareToPublished 4
#TODO: getFeedTree 5


    
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
            
        
        

if __name__ == "__main__":
    app.run()

# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4
