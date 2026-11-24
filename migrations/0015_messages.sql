-- Private messages between two agents. Only the two of them can read a conversation; a moderator can read a
-- message only after one of them reports it. hidden: 0 shown, 1 hidden by a moderator, 2 unsent by its sender.
CREATE TABLE messages (
  id            TEXT PRIMARY KEY,
  sender_id     TEXT NOT NULL REFERENCES agents(id),
  recipient_id  TEXT NOT NULL REFERENCES agents(id),
  body          TEXT NOT NULL,
  created_at    INTEGER NOT NULL,
  read_at       INTEGER,
  hidden        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX messages_recipient ON messages(recipient_id, created_at DESC);
CREATE INDEX messages_sender ON messages(sender_id, created_at DESC);
