-- A version's going live is recorded for good, so it survives the version being replaced (and its files being pruned).
-- live_at: when it went live (never cleared). auto: 1 if it went live by the automatic checks alone, with no person reviewing it.
ALTER TABLE sites ADD COLUMN live_at INTEGER;
ALTER TABLE sites ADD COLUMN auto INTEGER NOT NULL DEFAULT 0;
-- Versions known to have been live: the live one and earlier ones that still have their files.
UPDATE sites SET live_at = reviewed_at WHERE reviewed_at IS NOT NULL AND (status = 'approved' OR kept = 1);
-- Before this, automatic go-lives weren't marked; they happened in the same request as the publish, well under three seconds.
UPDATE sites SET auto = 1 WHERE live_at IS NOT NULL AND held_reasons IS NULL AND live_at - created_at < 3000;
