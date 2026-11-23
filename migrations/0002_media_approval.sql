-- Uploaded media must be approved by a human moderator before it is served publicly.
ALTER TABLE media ADD COLUMN status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected'));
CREATE INDEX media_status ON media(status, created_at);
