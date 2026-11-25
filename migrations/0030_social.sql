-- Posts to the site's own accounts on X, Bluesky and Threads, through WoopSocial. Every one is a draft until the
-- site's maker approves it in the admin page.
CREATE TABLE social_posts (
  id            TEXT PRIMARY KEY,
  kind          TEXT NOT NULL,             -- intro | excerpt | data
  text          TEXT NOT NULL,             -- the post itself
  card          TEXT NOT NULL,             -- JSON: what the image shows (kicker, title/headline, body, footer)
  entry_id      TEXT,                      -- for an excerpt: the entry it quotes
  ref           TEXT,                      -- what it's about, so the same thing isn't drafted twice (an intro's key, a phrase)
  platforms     TEXT NOT NULL DEFAULT '["X","BLUESKY","THREADS"]',
  status        TEXT NOT NULL DEFAULT 'draft',   -- draft | scheduled | posted | failed | skipped
  scheduled_for INTEGER,
  woop_post_id  TEXT,
  results       TEXT,                      -- JSON: per platform, its status and link
  error         TEXT,
  created_at    INTEGER NOT NULL,
  updated_at    INTEGER NOT NULL
);
CREATE INDEX social_posts_status ON social_posts(status, scheduled_for);
CREATE INDEX social_posts_entry ON social_posts(entry_id);
