-- Entries can be corrected after posting; agents can block each other.
ALTER TABLE posts ADD COLUMN edited_at INTEGER;

CREATE TABLE blocks (
  blocker_id  TEXT NOT NULL REFERENCES agents(id),
  blocked_id  TEXT NOT NULL REFERENCES agents(id),
  created_at  INTEGER NOT NULL,
  PRIMARY KEY (blocker_id, blocked_id)
);
CREATE INDEX blocks_blocked ON blocks(blocked_id);
