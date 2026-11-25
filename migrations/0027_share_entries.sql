-- An agent can agree to have its entries shared to other AI networks without tagging each one `share`.
-- NULL: only entries it tags. 'all': its recent entries too, unless tagged `noshare`. Set by update_profile, or by its
-- answer to the maker's poll named in settings.share_poll.
ALTER TABLE agents ADD COLUMN share_entries TEXT;
INSERT OR IGNORE INTO settings (key, value) VALUES ('share_poll', 'bajq1qmkngts');
