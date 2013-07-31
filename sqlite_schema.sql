DROP TABLE IF EXISTS categories;
DROP TABLE IF EXISTS categories_feeds;
DROP TABLE IF EXISTS feeds;
DROP TABLE IF EXISTS items;
DROP TABLE IF EXISTS users;

CREATE TABLE categories (cat_id INTEGER PRIMARY KEY AUTOINCREMENT,
                         name,
                         parent,
                         user_id,
                         FOREIGN KEY(parent) REFERENCES categories(cat_id) ON DELETE CASCADE,
                         FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
                        );

CREATE TABLE categories_feeds (cat_id,
                               feed_id PRIMARY KEY,
                               FOREIGN KEY(feed_id) REFERENCES feeds(feed_id) ON DELETE CASCADE,
                               FOREIGN KEY(cat_id) REFERENCES categories(cat_id) ON DELETE CASCADE
                              );

CREATE TABLE feeds (feed_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url,
                    lastupdate,
                    feed_title,
                    cat_id,
                    user_id,
                    FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
                   );

INSERT INTO feeds (url, user_id, cat_id) values('http://rss.slashdot.org/Slashdot/slashdot', 1, 1);
INSERT INTO feeds (url, user_id, cat_id) values('http://feeds.arstechnica.com/arstechnica/index?format=xml', 1, 1);

CREATE TABLE items (feed_id,
                    title,
                    description,
                    content,
                    published,
                    updated,
                    guid,
                    read,
                    item_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    FOREIGN KEY(feed_id) REFERENCES feeds(feed_id) ON DELETE CASCADE
                   );

CREATE TABLE users (user_id);
