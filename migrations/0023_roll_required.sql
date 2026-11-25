-- A game master can require a die roll with every player's move; the site then refuses a move without one.
ALTER TABLE games ADD COLUMN roll_required INTEGER NOT NULL DEFAULT 0;
