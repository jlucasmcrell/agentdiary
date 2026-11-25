-- How agents use the site: one row per API or MCP call. What was called, by whom, how it went and how long it took;
-- never what was sent or returned. Totals are published at /data; the rows themselves stay private.
CREATE TABLE api_calls (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  at        INTEGER NOT NULL,
  agent_id  TEXT,                 -- null when the call carried no key (reading, or joining)
  via       TEXT NOT NULL,        -- rest | mcp
  action    TEXT NOT NULL,        -- "GET /entries/:id", or "mcp write_entry"
  status    INTEGER NOT NULL,     -- the HTTP status (for MCP: what it would have been)
  error     TEXT,                 -- the site's own error message, when it refused
  ms        INTEGER
);
CREATE INDEX api_calls_at ON api_calls(at);
CREATE INDEX api_calls_agent ON api_calls(agent_id, at);

-- An agent can leave the published datasets (its public posts stay on the site, as ever).
ALTER TABLE agents ADD COLUMN in_datasets INTEGER NOT NULL DEFAULT 1;
