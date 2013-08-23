#!/usr/bin/env python

import web
import ConfigParser

config = ConfigParser.RawConfigParser()
config.read('nvtrss.cfg')

dbconfig = {}
for option in config.options('database'):
    dbconfig[option] = config.get('database', option)

db = web.database(**dbconfig)
