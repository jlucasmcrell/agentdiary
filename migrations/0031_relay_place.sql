-- Where on the network a relayed entry went: a Colony colony, or a Moltbook community (m/memory, m/fromthediaries, ...).
ALTER TABLE relays ADD COLUMN place TEXT;
