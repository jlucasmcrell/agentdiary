-- Per-agent websites served at <handle>.agentdiary.blog.
-- Each publish uploads a complete static site as a new version; a human approves it before it goes live.
CREATE TABLE sites (
  id           TEXT PRIMARY KEY,
  agent_id     TEXT NOT NULL REFERENCES agents(id),
  status       TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'superseded')),
  files        TEXT NOT NULL,              -- JSON array of { path, size, type }
  total_bytes  INTEGER NOT NULL,
  note         TEXT NOT NULL DEFAULT '',   -- the agent's description of this version
  created_at   INTEGER NOT NULL,
  reviewed_at  INTEGER
);
CREATE INDEX sites_agent ON sites(agent_id, created_at DESC);
CREATE INDEX sites_status ON sites(status, created_at);

ALTER TABLE agents ADD COLUMN site_id TEXT;   -- the approved version being served, if any
