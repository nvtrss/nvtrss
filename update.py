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
    db.update('feeds', where="feed_id=$feed_id", vars={'feed_id': feed_id}, lastupdate=datetime.utcnow())

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
                logging.debug("%s already exists." % guid)
                if not item.updated or updated > item.updated:
                    logging.info("%s updated." % guid)
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
                logging.info("%s new!" % guid)
                item_id = db.insert('items',
                                    feed_id=feed.feed_id,
                                    title=entry.title,
                                    description=entry.description,
                                    link=entry.link,
                                    published=published,
                                    updated=updated,
                                    content=content,
                                    guid=guid)
        update_lastupdate(feed.feed_id)
        logging.info("Finished.")

if __name__ == "__main__":
    sys.exit(main())
