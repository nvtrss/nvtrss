DROP TABLE IF EXISTS categories;
DROP TABLE IF EXISTS feeds;
DROP TABLE IF EXISTS items;
DROP TABLE IF EXISTS users;
DROP TABLE IF EXISTS sessions;

CREATE TABLE categories (cat_id INTEGER PRIMARY KEY AUTOINCREMENT,
                         name,
                         parent,
                         user_id INTEGER,
                         FOREIGN KEY(parent) REFERENCES categories(cat_id) ON DELETE CASCADE,
                         FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE,
                         UNIQUE(user_id, name, parent)
                        );

CREATE TABLE feeds (feed_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url,
                    lastupdate timestamp,
                    feed_title,
                    cat_id INTEGER,
                    user_id INTEGER,
                    etag,
                    last_modified,
                    has_icon,
                    icon_updated timestamp,
                    FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE,
                    UNIQUE(user_id, url)
                   );
CREATE TABLE items (feed_id INTEGER,
                    guid,
                    title,
                    description,
                    link,
                    content,
                    published timestamp,
                    updated timestamp,
                    read timestamp,
                    starred timestamp,
                    item_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    FOREIGN KEY(feed_id) REFERENCES feeds(feed_id) ON DELETE CASCADE,
                    UNIQUE(feed_id, guid)
                   );
CREATE TABLE sessions (user_id INTEGER,
                       sid,
                       lastused timestamp,
                       FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
                      );
CREATE TABLE users (user_id INTEGER PRIMARY KEY AUTOINCREMENT,username,hash);
CREATE INDEX idx_items_timestamps on items(updated,published);
CREATE INDEX idx_items_unread on items(feed_id,read,published);
CREATE INDEX idx_items_getCategories_unread on items(read, feed_id);
