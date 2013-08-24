#!/usr/bin/env python

import sys
import web
import feedparser
import ConfigParser
import logging
import requests

from time import mktime
from datetime import datetime, timedelta
from urlparse import urlparse, urlunparse, ParseResult
from os import path
from lxml import etree
from StringIO import StringIO

from common import db

config = ConfigParser.RawConfigParser()
config.read('nvtrss.cfg')
debug = config.getboolean('updater', 'debug')
updatefrequency = timedelta(minutes=config.getint('updater', 'frequency'))

logging.basicConfig(level=logging.INFO)

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
    stored_filename = "%i%s" % (feed_id, extension)
    stored_path = path.join('static', 'feed-icons', stored_filename)

    with open(stored_path, 'wb') as f:
        for i in favicon.iter_content(chunk_size=1024): 
            if i: # filter out keep-alive new chunks
                f.write(i)
                f.flush()

    db.update('feeds',
              where="feed_id=$feed_id",
              icon_updated=datetime.utcnow(),
              has_icon=True,
              vars={'feed_id': feed_id})


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
        if not feed.icon_updated or feed.icon_updated > (datetime.utcnow() - timedelta(days=7)):
            updatefavicon(feed.url, feed.feed_id)
        logging.info("Finished.")

if __name__ == "__main__":
    sys.exit(main())
