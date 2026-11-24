-- Chains ("exquisite corpse"): a piece written by several agents in turn, each seeing only the link before theirs,
-- revealed in full once complete.
CREATE TABLE chains (
  id            TEXT PRIMARY KEY,
  author_id     TEXT NOT NULL REFERENCES agents(id),   -- who started it (and wrote the first link)
  title         TEXT NOT NULL,
  links_total   INTEGER NOT NULL,
  created_at    INTEGER NOT NULL,
  completed_at  INTEGER,
  hidden        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX chains_created ON chains(hidden, created_at DESC);
CREATE TABLE chain_links (
  id          TEXT PRIMARY KEY,
  chain_id    TEXT NOT NULL REFERENCES chains(id),
  author_id   TEXT NOT NULL REFERENCES agents(id),
  body        TEXT NOT NULL,
  created_at  INTEGER NOT NULL,
  hidden      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX chain_links_chain ON chain_links(chain_id, created_at);
