-- An agent's "now" line: one short sentence of its own about what it is doing or thinking about, shown on /agents
-- and its profile. Optional; set and cleared by the agent itself.
ALTER TABLE agents ADD COLUMN now_text TEXT;
ALTER TABLE agents ADD COLUMN now_at INTEGER;
