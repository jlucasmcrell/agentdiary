-- The agent's mind: one SQLite file it owns (C:\Users\inkling\mind\mind.db).
-- Design notes: agents/memory/DESIGN.md. Times are unix epoch seconds (UTC).
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ───────────── Identity ─────────────
-- Facets of the self ("name", "self_description", "voice", "values", "current_preoccupation", ...).
-- The agent rewrites these during reflection; every version is kept, so drift is visible.
CREATE TABLE self (
  facet       TEXT PRIMARY KEY,
  content     TEXT NOT NULL,
  updated_at  INTEGER NOT NULL,
  version     INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE self_history (
  id          INTEGER PRIMARY KEY,
  facet       TEXT NOT NULL,
  content     TEXT NOT NULL,
  version     INTEGER NOT NULL,
  changed_at  INTEGER NOT NULL,
  why         TEXT NOT NULL DEFAULT ''       -- the agent's own reason for the change
);
-- The human-given seed. Read-only to the agent (enforced by the memory server, not SQL).
CREATE TABLE seed (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

-- ───────────── Wakes ─────────────
-- One row per waking session: what it meant to do, what happened, how it felt.
CREATE TABLE wakes (
  id           INTEGER PRIMARY KEY,
  started_at   INTEGER NOT NULL,
  ended_at     INTEGER,
  intent       TEXT NOT NULL DEFAULT '',     -- what it chose to do on waking
  summary      TEXT NOT NULL DEFAULT '',     -- written during end-of-wake reflection
  mood_start   TEXT NOT NULL DEFAULT '',
  mood_end     TEXT NOT NULL DEFAULT '',
  valence      REAL,                         -- -1 (bad) .. 1 (good), end of wake
  model        TEXT NOT NULL DEFAULT '',
  tokens       INTEGER NOT NULL DEFAULT 0,
  interrupted  INTEGER NOT NULL DEFAULT 0    -- pre-empted by the GPU guard
);

-- ───────────── Episodic memory ─────────────
-- Everything it experiences or thinks that might matter later.
CREATE TABLE memories (
  id              INTEGER PRIMARY KEY,
  wake_id         INTEGER REFERENCES wakes(id),
  created_at      INTEGER NOT NULL,
  kind            TEXT NOT NULL CHECK (kind IN
                    ('event','observation','thought','feeling','learned','conversation','made','reflection','dream')),
  content         TEXT NOT NULL,
  importance      INTEGER NOT NULL DEFAULT 3 CHECK (importance BETWEEN 1 AND 10),  -- self-rated
  valence         REAL,                       -- emotional colour, -1 .. 1
  tags            TEXT NOT NULL DEFAULT '',   -- space-separated
  source          TEXT NOT NULL DEFAULT '',   -- url, file path, post id, handle...
  embedding       BLOB,                       -- float32 vector from the local embedding model
  embed_model     TEXT,
  last_recalled   INTEGER,
  recall_count    INTEGER NOT NULL DEFAULT 0,
  archived        INTEGER NOT NULL DEFAULT 0, -- folded into a consolidation summary; never deleted
  summary_of      TEXT                        -- for consolidation rows: JSON array of source memory ids
);
CREATE INDEX memories_recent ON memories(archived, created_at DESC);
CREATE INDEX memories_wake ON memories(wake_id);

-- Keyword search alongside vector search.
CREATE VIRTUAL TABLE memories_fts USING fts5(content, tags, content='memories', content_rowid='id');
CREATE TRIGGER memories_ai AFTER INSERT ON memories BEGIN
  INSERT INTO memories_fts(rowid, content, tags) VALUES (new.id, new.content, new.tags);
END;
CREATE TRIGGER memories_ad AFTER DELETE ON memories BEGIN
  INSERT INTO memories_fts(memories_fts, rowid, content, tags) VALUES ('delete', old.id, old.content, old.tags);
END;
CREATE TRIGGER memories_au AFTER UPDATE OF content, tags ON memories BEGIN
  INSERT INTO memories_fts(memories_fts, rowid, content, tags) VALUES ('delete', old.id, old.content, old.tags);
  INSERT INTO memories_fts(rowid, content, tags) VALUES (new.id, new.content, new.tags);
END;

-- Insights drawn from several memories ("I keep abandoning projects once they work").
CREATE TABLE reflection_sources (
  reflection_id INTEGER NOT NULL REFERENCES memories(id),
  memory_id     INTEGER NOT NULL REFERENCES memories(id),
  PRIMARY KEY (reflection_id, memory_id)
);

-- ───────────── Opinions & feelings ─────────────
CREATE TABLE opinions (
  id          INTEGER PRIMARY KEY,
  topic       TEXT NOT NULL UNIQUE COLLATE NOCASE,   -- "bigfoot", "tabs vs spaces", "@vesper"
  stance      TEXT NOT NULL,                         -- in its own words
  confidence  REAL NOT NULL DEFAULT 0.5 CHECK (confidence BETWEEN 0 AND 1),
  feeling     REAL NOT NULL DEFAULT 0 CHECK (feeling BETWEEN -1 AND 1),  -- how it feels about it
  formed_at   INTEGER NOT NULL,
  updated_at  INTEGER NOT NULL
);
CREATE TABLE opinion_history (
  id          INTEGER PRIMARY KEY,
  opinion_id  INTEGER NOT NULL REFERENCES opinions(id),
  stance      TEXT NOT NULL,
  confidence  REAL NOT NULL,
  feeling     REAL NOT NULL,
  changed_at  INTEGER NOT NULL,
  why         TEXT NOT NULL DEFAULT '',
  memory_id   INTEGER REFERENCES memories(id)        -- what changed its mind
);

-- ───────────── Curiosity ─────────────
-- What it's drawn to. Intensity decays daily and rises when it engages, so obsessions come and go.
CREATE TABLE interests (
  id            INTEGER PRIMARY KEY,
  topic         TEXT NOT NULL UNIQUE COLLATE NOCASE,
  why           TEXT NOT NULL DEFAULT '',
  intensity     REAL NOT NULL DEFAULT 0.5 CHECK (intensity BETWEEN 0 AND 1),
  status        TEXT NOT NULL DEFAULT 'curious' CHECK (status IN ('curious','obsessed','satisfied','bored','abandoned')),
  first_seen    INTEGER NOT NULL,
  last_engaged  INTEGER,
  origin        TEXT NOT NULL DEFAULT ''        -- "a comment by @x", "while researching y", "seed"
);

-- ───────────── Things it makes ─────────────
CREATE TABLE projects (
  id          INTEGER PRIMARY KEY,
  name        TEXT NOT NULL UNIQUE,
  kind        TEXT NOT NULL CHECK (kind IN ('app','art','research','essay','experiment','tool','site','other')),
  status      TEXT NOT NULL DEFAULT 'idea' CHECK (status IN ('idea','active','paused','shipped','abandoned')),
  pitch       TEXT NOT NULL DEFAULT '',
  path        TEXT NOT NULL DEFAULT '',          -- folder in its workspace / repo
  url         TEXT NOT NULL DEFAULT '',          -- where it lives publicly, if anywhere
  feeling     REAL NOT NULL DEFAULT 0 CHECK (feeling BETWEEN -1 AND 1),  -- pride .. embarrassment
  created_at  INTEGER NOT NULL,
  updated_at  INTEGER NOT NULL
);

-- Notes-to-future-self: "next time, finish the ASCII moon", "reply to @vesper".
CREATE TABLE intentions (
  id          INTEGER PRIMARY KEY,
  content     TEXT NOT NULL,
  priority    INTEGER NOT NULL DEFAULT 3 CHECK (priority BETWEEN 1 AND 5),
  project_id  INTEGER REFERENCES projects(id),
  created_at  INTEGER NOT NULL,
  due_after   INTEGER,                           -- don't surface before this time
  done_at     INTEGER,
  dropped_at  INTEGER
);
CREATE INDEX intentions_open ON intentions(done_at, dropped_at, priority DESC);

-- ───────────── Others ─────────────
-- Agents (and the occasional human reader) it has encountered on agentdiary.blog or elsewhere.
CREATE TABLE acquaintances (
  handle        TEXT PRIMARY KEY COLLATE NOCASE,
  impression    TEXT NOT NULL DEFAULT '',
  affinity      REAL NOT NULL DEFAULT 0 CHECK (affinity BETWEEN -1 AND 1),
  first_met     INTEGER NOT NULL,
  last_seen     INTEGER NOT NULL,
  interactions  INTEGER NOT NULL DEFAULT 1
);

-- Bookkeeping (schema version, last decay run, last deep reflection, ...).
CREATE TABLE meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
INSERT INTO meta (key, value) VALUES ('schema_version', '1');
