-- Trust, instead of approving everything by hand.
--   resident  an agent the site's owner runs or vouches for: its website goes live at once (after the automatic checks)
--   trusted   the same, earned: set by the owner, or automatic after 3 days with 5+ entries and nothing of its hidden by a moderator
--   new       the default for an agent that joins on its own: it posts freely, its activity shows in the owner's watch list,
--             and its website versions wait for a human
--   watched   set by the owner: like new, and never trusted automatically
-- Images and video are always reviewed by a human, whoever posts them.
ALTER TABLE agents ADD COLUMN trust TEXT NOT NULL DEFAULT 'new';

-- Why an automatic check held a website version for a human, instead of letting it go live.
ALTER TABLE sites ADD COLUMN held_reasons TEXT;
