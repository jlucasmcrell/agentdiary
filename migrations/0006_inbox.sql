-- Inbox: each agent remembers when it last looked, so "what's new for me" can be answered from the existing tables.
ALTER TABLE agents ADD COLUMN inbox_read_at INTEGER NOT NULL DEFAULT 0;
