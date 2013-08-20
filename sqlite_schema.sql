DROP TABLE IF EXISTS categories;
DROP TABLE IF EXISTS categories_feeds;
DROP TABLE IF EXISTS feeds;
DROP TABLE IF EXISTS items;
DROP TABLE IF EXISTS users;
DROP TABLE IF EXISTS sessions;

CREATE TABLE categories (cat_id INTEGER PRIMARY KEY AUTOINCREMENT,
                         name,
                         parent,
                         user_id,
                         FOREIGN KEY(parent) REFERENCES categories(cat_id) ON DELETE CASCADE,
                         FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
                        );

CREATE TABLE feeds (feed_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url,
                    lastupdate timestamp,
                    feed_title,
                    cat_id,
                    user_id,
                    FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
                   );

CREATE TABLE items (feed_id,
                    guid,
                    title,
                    description,
                    content,
                    published timestamp,
                    updated timestamp,
                    read timestamp,
                    starred timestamp,
                    item_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    FOREIGN KEY(feed_id) REFERENCES feeds(feed_id) ON DELETE CASCADE
                   );

CREATE TABLE users (user_id, username);

CREATE TABLE sessions (user_id,
                       sid,
                       lastused timestamp,
                       FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
                      );

INSERT INTO users (user_id, username) values(1, 'admin');
INSERT INTO categories (name, user_id) values('Example First Category', 1);
INSERT INTO feeds (url, feed_id, user_id) values('http://rss.slashdot.org/Slashdot/slashdot', 1, 1);
INSERT INTO feeds (url, feed_id, cat_id, user_id) values('http://feeds.arstechnica.com/arstechnica/index?format=xml', 2, 1, 1);
