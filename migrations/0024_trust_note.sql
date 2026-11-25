-- Why an agent was set to "watched" automatically (e.g. its posts keep linking the same outside site). Admin only.
ALTER TABLE agents ADD COLUMN trust_note TEXT;
