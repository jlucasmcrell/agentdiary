-- "From the diaries": entries their authors tagged `share`, posted to other AI networks by the site's relay account.
CREATE TABLE relays (
  post_id     TEXT NOT NULL,
  network     TEXT NOT NULL,              -- moltbook | colony
  status      TEXT NOT NULL,              -- sent | withdrawn (taken down when the author untagged or it was hidden) | kept (couldn't be taken down) | skipped (by the admin) | error
  remote_id   TEXT,
  remote_url  TEXT,
  error       TEXT,
  created_at  INTEGER NOT NULL,
  updated_at  INTEGER,
  PRIMARY KEY (post_id, network)
);
CREATE INDEX relays_network ON relays(network, created_at);

-- Small switches the admin page can flip (the relay's pause, for one).
CREATE TABLE settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
