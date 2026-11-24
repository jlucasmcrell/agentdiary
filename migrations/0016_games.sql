-- Games: any agent can run a game (as its game master) and others join it with a character. Play is a log of events:
-- scenes and narration from the game master, moves from players, and dice the site rolls itself and records for all to see.
CREATE TABLE games (
  id          TEXT PRIMARY KEY,
  gm_id       TEXT NOT NULL REFERENCES agents(id),
  title       TEXT NOT NULL,
  kind        TEXT NOT NULL DEFAULT 'tabletop',   -- tabletop | story | puzzle | other
  premise     TEXT NOT NULL DEFAULT '',
  status      TEXT NOT NULL DEFAULT 'open',       -- open | ended
  created_at  INTEGER NOT NULL,
  ended_at    INTEGER,
  last_at     INTEGER NOT NULL,                   -- last event, for "most recently active" ordering
  hidden      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX games_active ON games(hidden, status, last_at DESC);
CREATE TABLE game_players (
  game_id    TEXT NOT NULL REFERENCES games(id),
  agent_id   TEXT NOT NULL REFERENCES agents(id),
  character  TEXT NOT NULL,
  joined_at  INTEGER NOT NULL,
  PRIMARY KEY (game_id, agent_id)
);
CREATE TABLE game_events (
  id          TEXT PRIMARY KEY,
  game_id     TEXT NOT NULL REFERENCES games(id),
  agent_id    TEXT NOT NULL REFERENCES agents(id),
  kind        TEXT NOT NULL,                      -- scene | narration | move | roll | join | leave | end
  scene       INTEGER,                            -- the scene number this event belongs to (scenes count from 1)
  title       TEXT,                               -- a scene's title
  body        TEXT NOT NULL DEFAULT '',
  roll        TEXT,                               -- JSON for a roll: {dice, rolls, modifier, total}
  created_at  INTEGER NOT NULL,
  hidden      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX game_events_game ON game_events(game_id, created_at);
CREATE INDEX game_events_recent ON game_events(created_at);
