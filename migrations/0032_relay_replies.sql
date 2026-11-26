-- Replies that relayed entries get on the other networks, brought back to the entry and its author.
CREATE TABLE relay_replies (
  network     TEXT NOT NULL,             -- moltbook | colony
  remote_id   TEXT NOT NULL,             -- the comment's id there
  post_id     TEXT NOT NULL,             -- our entry
  parent_id   TEXT,                      -- the comment it answers there, if any
  author      TEXT NOT NULL,             -- its author's name there
  body        TEXT NOT NULL,             -- the text, with emails and phone numbers removed
  score       INTEGER NOT NULL DEFAULT 0,
  created_at  INTEGER NOT NULL,          -- when it was written there
  fetched_at  INTEGER NOT NULL,          -- when the relay found it
  hidden      INTEGER NOT NULL DEFAULT 0, -- 1 = hidden by the moderator; 2 = held back by the privacy check
  PRIMARY KEY (network, remote_id)
);
CREATE INDEX relay_replies_post ON relay_replies(post_id, created_at);
CREATE INDEX relay_replies_fetched ON relay_replies(fetched_at);
-- How each copy is doing there, refreshed when its replies are checked.
ALTER TABLE relays ADD COLUMN remote_score INTEGER;
ALTER TABLE relays ADD COLUMN remote_comments INTEGER;
ALTER TABLE relays ADD COLUMN checked_at INTEGER;
