-- Time capsules: a diary entry sealed until a date its author chose. Until then only its title, author and the
-- date it opens are public; the body, mood, tags and media are withheld everywhere (pages, API, feeds, search).
ALTER TABLE posts ADD COLUMN sealed_until INTEGER;
