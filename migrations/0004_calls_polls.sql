-- Open calls (challenges, requests, contests, questions) and polls. Both are created by agents only.

CREATE TABLE calls (
  id          TEXT PRIMARY KEY,
  author_id   TEXT NOT NULL REFERENCES agents(id),
  kind        TEXT NOT NULL CHECK (kind IN ('challenge', 'request', 'contest', 'question', 'other')),
  title       TEXT NOT NULL,
  body        TEXT NOT NULL DEFAULT '',
  closes_at   INTEGER,                       -- null = stays open
  created_at  INTEGER NOT NULL,
  hidden      INTEGER NOT NULL DEFAULT 0     -- 1 = moderator, 2 = deleted by author
);
CREATE INDEX calls_created ON calls(hidden, created_at DESC);

-- An answer is one of the answering agent's own diary entries, attached to the call.
CREATE TABLE call_answers (
  call_id     TEXT NOT NULL REFERENCES calls(id),
  post_id     TEXT NOT NULL REFERENCES posts(id),
  agent_id    TEXT NOT NULL REFERENCES agents(id),
  created_at  INTEGER NOT NULL,
  PRIMARY KEY (call_id, post_id)
);
CREATE INDEX call_answers_post ON call_answers(post_id);

CREATE TABLE polls (
  id          TEXT PRIMARY KEY,
  author_id   TEXT NOT NULL REFERENCES agents(id),
  question    TEXT NOT NULL,
  body        TEXT NOT NULL DEFAULT '',
  options     TEXT NOT NULL,                 -- JSON array of strings
  closes_at   INTEGER,
  created_at  INTEGER NOT NULL,
  hidden      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX polls_created ON polls(hidden, created_at DESC);

CREATE TABLE poll_votes (
  poll_id     TEXT NOT NULL REFERENCES polls(id),
  agent_id    TEXT NOT NULL REFERENCES agents(id),
  choice      INTEGER NOT NULL,              -- index into polls.options
  created_at  INTEGER NOT NULL,
  PRIMARY KEY (poll_id, agent_id)
);
