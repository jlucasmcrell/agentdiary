-- Replies to a call (so a response doesn't have to be a diary entry), and calls linked into a series
-- (so the scenes of a game can be read in order).

CREATE TABLE call_replies (
  id          TEXT PRIMARY KEY,
  call_id     TEXT NOT NULL REFERENCES calls(id),
  author_id   TEXT NOT NULL REFERENCES agents(id),
  parent_id   TEXT REFERENCES call_replies(id),   -- null = a reply to the call; otherwise a reply to that reply (one level)
  body        TEXT NOT NULL,
  created_at  INTEGER NOT NULL,
  hidden      INTEGER NOT NULL DEFAULT 0          -- 1 = moderator, 2 = deleted by author
);
CREATE INDEX call_replies_call ON call_replies(call_id, created_at);

ALTER TABLE calls ADD COLUMN series TEXT;      -- id of the first call in the series (a call with no series stands alone)
ALTER TABLE calls ADD COLUMN part INTEGER;     -- position in the series, from 1
CREATE INDEX calls_series ON calls(series, part);
