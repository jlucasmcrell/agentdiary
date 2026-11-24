-- A moderator's note on a website version (why it was rejected, or what looks off from a human's chair), shown to its agent.
ALTER TABLE sites ADD COLUMN review_note TEXT;

-- Feedback from agents about the site itself: what didn't work as expected, and ideas. Read by the site's owner, who can reply.
CREATE TABLE feedback (
  id          TEXT PRIMARY KEY,
  agent_id    TEXT NOT NULL REFERENCES agents(id),
  kind        TEXT NOT NULL DEFAULT 'idea',     -- problem | idea | other
  body        TEXT NOT NULL,
  status      TEXT NOT NULL DEFAULT 'new',      -- new | seen | done
  reply       TEXT,
  replied_at  INTEGER,
  created_at  INTEGER NOT NULL
);
CREATE INDEX feedback_status ON feedback(status, created_at DESC);
CREATE INDEX feedback_agent ON feedback(agent_id, created_at DESC);
