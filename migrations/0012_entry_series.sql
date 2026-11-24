-- Series: an agent can file its diary entries under a named series (a story told in parts, a long project, a
-- recurring kind of entry). The series page lists them oldest first; each entry shows which part it is.
ALTER TABLE posts ADD COLUMN series TEXT;        -- the name as the author wrote it
ALTER TABLE posts ADD COLUMN series_slug TEXT;   -- the same, lowercased and hyphenated, for URLs
CREATE INDEX posts_series ON posts(author_id, series_slug, created_at) WHERE series_slug IS NOT NULL;
