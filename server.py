#!/usr/bin/env python

import web
import json

from time import time
from datetime import datetime, timedelta

version = "0.0.1"
api_level = -1
        
urls = (
    '/api', 'api',
    '/api/', 'api',
)
app = web.application(urls, globals())

db = web.database(dbn='sqlite', db='database.db')

web.config.debug = True
#if web.config.get('_session') is None:
#    session = web.session.Session(app, web.session.DiskStore('sessions'), {'username': None})
#    web.config._session = session
#else:
#    session = web.config._session

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
    def __init__(self, msg, sid=None, item_id=None):
        self.msg = msg
        self.sid = sid
        self.item_id = item_id

    def __str__(self):
        return repr(self.msg)



def checksid(sid, raise_exception=True):
    """Checks for valid sid and returns user_id."""
    #TODO: checksid
    if sid == "12345":
        return 1
    else:
        if raise_exception:
            raise SessionError("Not a valid session", sid)
        else:
            return False


def ownerofitem(item_id):
    return db.query("""select user_id from items
                       join feeds
                         on feeds.feed_id=items.feed_id
                       where item_id=$item_id""",
                    vars={'item_id': item_id})[0].user_id


##
# API Function:
# See http://tt-rss.org/redmine/projects/tt-rss/wiki/JsonApiReference
##

def getApiLevel(jsoninput=None):
    checksid(jsoninput['sid'])
    return {'level': api_level}

def getVersion(jsoninput=None):
    checksid(jsoninput['sid'])
    return {'version': version}

def login(jsoninput=None):
    #TODO: session handling
    return {'session_id': "12345",
            'api_level': api_level}

def logout(jsoninput=None):
    if 'sid' in jsoninput:
        if checksid(jsoninput['sid']):
            #TODO: logout
            return {"status":"OK"}
    return {"status": "false"}

def isLoggedIn(jsoninput=None):
    if 'sid' in jsoninput:
        if checksid(jsoninput['sid'], False):
            return {"status":True}
        else:
            return {"status":False}
    raise SessionError("No sessiond specified.", jsoninput['sid'])

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
            'updated': row.updated,
            'title': row.title,
            'link': row.guid,
            'feed_title': row.feed_title,
            #TODO: tags
            'content': row.content,
            }


def getUnread(jsoninput=None):
    user_id = checksid(jsoninput['sid'])
    result = countunread(user_id)
    return {'unread':str(result)}

def getFeeds(jsoninput=None):
    user_id = checksid(jsoninput['sid'])
    #TODO: parameters: cat_id, unread_only, offset, include_nested
    query = """select * from feeds
               where user_id=$user_id"""
    variables = {'user_id': user_id}
    if 'cat_id' in jsoninput:
        cat_id = int(jsoninput['cat_id'])
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
    if 'limit' in jsoninput:
        variables['limit'] = int(jsoninput['limit'])
        query += " limit $limit"
    feeds = []
    if cat_id == -1:
        # We only want specials...
        pass
    else:
        result = db.query(query, vars=variables)
        if 'offset' in jsoninput:
            offset = int(jsoninput['offset'])
        else:
            offset = 0
        #TODO: feeds order_id
        order_id = 0
        for feed in result:
            unread = countunread(user_id, feed.feed_id)
            feeds.append({'feed_url': feed.url,
                          'title': feed.feed_title,
                          'id': feed.feed_id,
                          'unread': unread,
                          #TODO: favicon support
                          'has_icon': True,
                          'cat_id': feed.cat_id,
                          'last_updated': feed.lastupdate,
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

def parse_jsoninput(jsoninput, options):
    new_jsoninput = {}
    for option in options:
        try:
            new_jsoninput[option] = bool(jsoninput[option])
        except KeyError:
            new_jsoninput[option] = False
    return new_jsoninput

def getCategories(jsoninput=None):
    # TODO: parameters: include_empty
    user_id = checksid(jsoninput['sid'])
    options = ['unread_only', 'enable_nested', 'include_empty']
    jsoninput = parse_jsoninput(jsoninput, options)
        
    if jsoninput['enable_nested']:
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
        if jsoninput['unread_only'] and not unread:
            continue
        result.append({'id': category.cat_id,
                       'title': category.name,
                       'unread': unread,
                       'order_id': order_id})
        order_id += 1
    return result


def getHeadlines(jsoninput=None):
    #TODO: parameters: skip, is_cat, show_excerpt, show_content, view_mode
    #TODO: parameters: include_attachments, since_id, include_nested, order_by
    user_id = checksid(jsoninput['sid'])
    query = """select * from items
               join feeds
                 on feeds.feed_id=items.feed_id
               where feeds.user_id=$user_id"""
    variables = {'user_id': user_id}
    if 'feed_id' in jsoninput:
        feed_id = int(jsoninput['feed_id'])
        if feed_id > 0:
            variables['feed_id'] = int(jsoninput['feed_id'])
            query += str(" and items.feed_id=$feed_id")
        elif feed_id == 0: # TODO: all the others...
            # FIXME: uncategorized or archived? Unclear
            query += str(" and feeds.cat_id IS NULL")
        elif feed_id == -3: #fresh only
            query += str(" and items.published > $published")
            variables['published'] = freshcutoff()
    if 'limit' in jsoninput and jsoninput['limit'] < 201:
        variables['limit'] = int(jsoninput['limit'])
    else:
        variables['limit'] = 200
    if jsoninput['view_mode'] in ['adaptive', 'unread']:
        query += str(" and items.read is NULL")
    query += str(" limit $limit")
    results = db.query(query, vars=variables)
    headlines = []
    for row in results:
        headlines.append(article(row))
    return headlines

item_fields = ['starred', 'published', 'read'] # TODO: article?!

def updateArticle(jsoninput=None):
    #TODO: all
    user_id = checksid(jsoninput['sid'])
    article_ids = jsoninput['article_ids'].split(',')
    mode = int(jsoninput['mode'])
    field = int(jsoninput['field'])
    result = []
    count = 0
    for item_id in article_ids:
        #TODO: check article is user's...
        if ownerofitem(item_id) == user_id:
            if mode == 0:
                if field == 2:
                    # parameter is 'unread' but we store read so negate:
                    real_mode = time()
                else:
                    real_mode = None
            elif mode == 1:
                if field == 2:
                    # parameter is 'unread' but we store read so negate:
                    real_mode = None
                else:
                    real_mode = time()
            elif mode == 2:
                if db.select('items',
                             where='item_id=$item_id',
                             what=item_fields[field],
                             vars={'item_id': item_id}):
                    real_mode = None
                else:
                    real_mode = time()
            db.query('update items set %s=$mode where item_id=$item_id' % (item_fields[field],),
                     vars={'mode': real_mode, 'item_id': item_id})
            count += 1
        else:
            raise OwnershipError("Not a valid session for article.", user_id, item_id)

def getArticle(jsoninput=None):
    user_id = checksid(jsoninput['sid'])
    article_ids = jsoninput['article_id'].split(',')
    articles = []
    for item_id in article_ids:
        if user_id == ownerofitem(item_id):
            item = db.select('items',
                             where="item_id=$item_id",
                             vars={'item_id': item_id})[0]
            articles.append(article(item))
    return articles

def subscribeToFeed(jsoninput=None):
    user_id = checksid(jsoninput['sid'])
    feed_url = jsoninput['feed_url']
    #TODO: cat_id
    db.insert('feeds', url=feed_url, user_id=user_id, cat_id=0)

                            
#TODO: getConfig
#TODO: updateFeed
#TODO: getPref
#TODO: catchupFeed
#TODO: getCounters 1
#TODO: getLabels 1
#TODO: setArticleLabel 1
#TODO: shareToPublished 4
#TODO: unsubscribeFeed 5
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
               }

class api:
    def POST(self):
        print web.data()
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
                output['content'] = apifunctions[jsoninput['op']](jsoninput=jsoninput)
                output['status'] = 0
            except KeyError:
                output['status'] = 1
            except SessionError:
                output['status'] = 1
                output['content'] = {'error': 'NOT_LOGGED_IN'}
            except ApiError, e:
                output['status'] = 1
                output['content'] = {'error': str(e)}
        else:
            output['status'] = 1
            
        return json.dumps(output)
            
        
        

if __name__ == "__main__":
    app.run()

# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4
