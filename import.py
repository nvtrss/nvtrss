#!/usr/bin/env python

import sys
import web
import opml

from sqlite3 import IntegrityError

db = web.database(dbn='sqlite', db='database.db')

def get_user_id(username):
    return db.select('users',
                     where="username=$username",
                     what="user_id",
                     vars={'username': username}
                     )[0].user_id

def get_feed_id(user_id, feed_url):
    return db.select('feeds',
                     where="""url=$feed_url AND user_id=$user_id""",
                     what="feed_id",
                     vars={'feed_url': feed_url,
                           'user_id': user_id}
                     )[0].feed_id

def update_feed(user_id, url, feed_title, cat_id=None):
    try:
        feed_id = get_feed_id(user_id, url)
        db.update('feeds',
                  where="user_id=$user_id AND url=$url",
                  url=url,
                  feed_title=feed_title,
                  user_id=user_id,
                  cat_id=cat_id,
                  vars={'user_id': user_id, 'url': url})
    except IndexError:
        db.insert('feeds',
                  url=url,
                  feed_title=feed_title,
                  user_id=user_id,
                  cat_id=cat_id)

def get_category_id(user_id, categoryname, parent_cat=None):
    vars={'categoryname': categoryname,
          'user_id': user_id}
    if parent_cat:
        where="""name=$categoryname AND user_id=$user_id AND parent=$parent_cat"""
        vars['parent_cat'] = parent_cat
    else:
        where="""name=$categoryname AND user_id=$user_id AND parent IS NULL"""
    return db.select('categories',
                     where=where,
                     what="cat_id",
                     vars=vars
                     )[0].cat_id

def update_category(user_id, name, parent_cat=None):
    try:
        cat_id = get_category_id(user_id, name, parent_cat)
        vars={'categoryname': name,
              'user_id': user_id}
        if parent_cat:
            where="""name=$categoryname AND user_id=$user_id AND parent=$parent_cat"""
            vars['parent_cat'] = parent_cat
        else:
            where="""name=$categoryname AND user_id=$user_id AND parent IS NULL"""
        db.update('categories',
                  where=where,
                  name=name,
                  user_id=user_id,
                  vars=vars)
    except IndexError:
        db.insert('categories',
                  name=name,
                  parent=parent_cat,
                  user_id=user_id)
        cat_id = get_category_id(user_id, name, parent_cat)
    return cat_id

def process_outline(outline, user_id, cat_id=None):
    if outline.text in ['tt-rss-prefs', 'tt-rss-labels', 'tt-rss-filters']:
        return None
    try:
        update_feed(user_id, outline.xmlUrl, outline.text, cat_id)
    except AttributeError:
        #Another category?
        new_cat_id = update_category(user_id, outline.text, cat_id)
        for suboutline in outline:
            process_outline(suboutline, user_id, new_cat_id)

def process_opml(opml, user_id):
    for outline in opml:
        process_outline(outline, user_id)


def main(argv=None):
    if argv is None:
        argv = sys.argv
    username = argv[1]
    opmlfile = argv[2]

    try:
        user_id = get_user_id(username)
    except IndexError:
        db.insert('users', username=username)
        user_id = get_user_id(username)

    with open(opmlfile, 'r') as f:
        parsedopml = opml.parse(f)
    
    process_opml(parsedopml, user_id)


if __name__ == "__main__":
    sys.exit(main())
