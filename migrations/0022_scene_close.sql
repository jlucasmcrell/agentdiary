-- A game master can give a scene a real closing time: shown to players, and moves on that scene are refused after it.
ALTER TABLE game_events ADD COLUMN closes_at INTEGER;
