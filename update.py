#!/usr/bin/env python

import sys
import web
import feedparser
import ConfigParser
import logging

from time import mktime
from datetime import datetime, timedelta

config = ConfigParser.RawConfigParser()
config.read('nvtrss.cfg')
debug = config.getboolean('updater', 'debug')
updatefrequency = timedelta(minutes=config.getint('updater', 'frequency'))

logging.basicConfig(level=logging.INFO)

db = web.database(dbn='sqlite', db='database.db')
db.printing = debug

def feedstoprocess(limit=None):
    feeds = db.select('feeds',
                      where="lastupdate < $lastupdate OR lastupdate IS NULL",
                      order='lastupdate ASC',
                      limit=limit,
                      vars={'lastupdate': datetime.utcnow() - updatefrequency})
    return feeds

def lastupdate(feed_id):
    result = db.select('items', what="MAX(published) AS published", where="feed_id=$feed_id", vars={'feed_id': feed_id})[0].published
    if result:
        return result
    else:
        return 0
def update_lastupdate(feed_id):
    db.update('feeds', where="feed_id=$feed_id", vars={'feed_id': feed.feed_id}, lastupdate=datetime.utcnow())

def main(argv=None):
    if argv is None:
        argv = sys.argv
    for feed in feedstoprocess():
        logging.info("Processing %s" % feed.url)
        result = feedparser.parse(feed.url, etag=feed.etag, modified=feed.last_modified)
        if result.status == 304:
            update_lastupdate(feed.feed_id)
            logging.info("304 received, skipping.")
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
                logging.debug("%s already exists." % entry.id)
                if updated > item.updated:
                    logging.info("%s updated." % entry.id)
                    db.update('items',
                              where="guid=$guid",
                              title=entry.title,
                              description=entry.description,
                              published=published,
                              updated=updated,
                              content=content,
                              vars={'guid': entry.id})
            except IndexError:
                logging.info("%s new!" % entry.id)
                item_id = db.insert('items',
                                    feed_id=feed.feed_id,
                                    title=entry.title,
                                    description=entry.description,
                                    published=published,
                                    updated=updated,
                                    content=content,
                                    guid=entry.id)
        update_lastupdate(feed.feed_id)
        logging.info("Finished.")

if __name__ == "__main__":
    sys.exit(main())
