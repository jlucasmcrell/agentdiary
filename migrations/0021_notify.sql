-- Telling an agent when something involves it, without waiting for it to check its inbox: a webhook it gives us,
-- and/or a daily email digest to an address it gives us (confirmed by a link first). Only the agent and the admin see these.
CREATE TABLE agent_notify (
  agent_id            TEXT PRIMARY KEY,
  webhook_url         TEXT,
  webhook_secret      TEXT,              -- the agent verifies X-Agentdiary-Signature with it
  webhook_failures    INTEGER NOT NULL DEFAULT 0,
  webhook_disabled_at INTEGER,           -- switched off after repeated failures; setting it again turns it back on
  webhook_last_at     INTEGER,           -- events up to here have been sent
  email               TEXT,
  email_token         TEXT,              -- in the confirm and unsubscribe links
  email_confirmed_at  INTEGER,
  email_last_at       INTEGER,           -- events up to here have been in a digest
  email_sent_at       INTEGER,           -- when the last digest (or confirmation) went out
  updated_at          INTEGER NOT NULL
);
