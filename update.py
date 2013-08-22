#!/usr/bin/env python

import web
import feedparser
import ConfigParser

from time import mktime
from datetime import datetime

config = ConfigParser.RawConfigParser()
config.read('nvtrss.cfg')
debug = config.getboolean('updater', 'debug')

db = web.database(dbn='sqlite', db='database.db')
db.printing = debug

def feedstoprocess(limit=1):
    feeds = db.select('feeds', order='lastupdate ASC', limit=limit)
    return feeds

def lastupdate(feed_id):
    result = db.select('items', what="MAX(published) AS published", where="feed_id=$feed_id", vars={'feed_id': feed_id})[0].published
    if result:
        return result
    else:
        return 0

for feed in feedstoprocess():
    result = feedparser.parse(feed.url, etag=feed.etag, modified=feed.last_modified)
    if result.status == 304:
        continue
    try:
        db.update('feeds',
                  where="feed_id=$feed_id",
                  feed_title=result.feed.title,
                  etag=result.etag,
                  vars={'feed_id': feed.feed_id})
    except AttributeError:
        db.update('feeds',
                  where="feed_id=$feed_id",
                  feed_title=result.feed.title,
                  last_modified=result.modified,
                  vars={'feed_id': feed.feed_id})
    for entry in result.entries:
        published = datetime.fromtimestamp(mktime(entry.published_parsed))
        updated = datetime.fromtimestamp(mktime(entry.updated_parsed))
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
        try:
            item = db.select('items',
                             where="feed_id=$feed_id AND guid=$guid",
                             vars={'feed_id': feed.feed_id, 'guid': entry.id})[0]
            item_id = item.item_id
            if updated > item.updated:
                db.update('items',
                          where="guid=$guid",
                          title=entry.title,
                          description=entry.description,
                          published=published,
                          updated=updated,
                          content=content,
                          vars={'guid': entry.id})
        except IndexError:
            item_id = db.insert('items',
                                feed_id=feed.feed_id,
                                title=entry.title,
                                description=entry.description,
                                published=published,
                                updated=updated,
                                content=content,
                                guid=entry.id)
    db.update('feeds', where="feed_id=$feed_id", vars={'feed_id': feed.feed_id}, lastupdate=datetime.utcnow())
