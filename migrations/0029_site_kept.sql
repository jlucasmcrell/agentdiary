-- Earlier live versions of an agent's website keep their files (the ten newest), so a file can be brought back from one.
-- kept = 1: a superseded version that was live and still has its files.
ALTER TABLE sites ADD COLUMN kept INTEGER NOT NULL DEFAULT 0;
