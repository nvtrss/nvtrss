#!/usr/bin/env python

import web
import feedparser
from time import gmtime
from calendar import timegm

db = web.database(dbn='sqlite', db='database.db')

def feedstoprocess(limit=1):
    feeds = db.select('feeds', order='lastupdate ASC', limit=limit)
    return feeds

def lastupdate(fid):
    result = db.select('items', what="MAX(published) AS published", where="fid=$fid", vars={'fid': fid})[0].published
    if result:
        return result
    else:
        return 0

for feed in feedstoprocess():
    result = feedparser.parse(feed.url)
    db.update('feeds',
              where="fid=$fid",
              title=result.feed.title,
              vars={'fid': feed.fid})
    for entry in result.entries:
        published = timegm(entry.published_parsed)
        updated = timegm(entry.updated_parsed)
        storecontent = False
        try:
            item = db.select('items',
                             where="fid=$fid AND guid=$guid",
                             vars={'fid': feed.fid, 'guid': entry.id})[0]
            if updated > item.updated:
                db.update('items',
                          where="iid=$iid",
                          title=entry.title,
                          description=entry.description,
                          published=published,
                          updated=updated,
                          vars={'iid': item.iid})
                storecontent = True
        except IndexError:
            db.insert('items',
                      fid=feed.fid,
                      title=entry.title,
                      description=entry.description,
                      published=published,
                      updated=updated,
                      guid=entry.id)
            item = db.select('items',
                             what='iid',
                             where="fid=$fid AND guid=$guid",
                             vars={'fid': feed.fid, 'guid': entry.id})[0]
            storecontent = True
        if storecontent:
            try:
                cid=0
                db.delete('content',
                          where="iid=$iid",
                          vars={'iid': item.iid})
                for content in entry.content:
                    db.insert('content', iid=item.iid, cid=cid, value=content.value, contenttype=content.type)
                    cid += 1
            except AttributeError:
                # Guess not an atom feed.
                pass

    db.update('feeds', where="fid=$fid", vars={'fid': feed.fid}, lastupdate=timegm(gmtime()))
