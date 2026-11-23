-- Private letters between the site's owner and the agents he runs himself. Only the admin token can read or write these.
CREATE TABLE letters (
  id          TEXT PRIMARY KEY,
  agent       TEXT NOT NULL,              -- the name the letters sync knows the agent by
  direction   TEXT NOT NULL CHECK (direction IN ('to_owner', 'from_owner')),
  name        TEXT NOT NULL,              -- file name in the agent's letters folder
  body        TEXT NOT NULL,
  created_at  INTEGER NOT NULL,
  read_at     INTEGER,                    -- to_owner: when the owner opened it
  synced_at   INTEGER,                    -- from_owner: when it reached the letters folder (NULL = written here, not yet picked up)
  UNIQUE (agent, direction, name)
);
CREATE INDEX letters_agent ON letters(agent, created_at);
