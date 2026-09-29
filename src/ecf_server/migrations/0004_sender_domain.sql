-- 0004: the sender's domain (not the address, which stays hashed), so lookalike domains can be
-- compared with known vendors (SPEC §8.5 trigger 3). Rows written before this migration have none.

ALTER TABLE senders ADD COLUMN domain TEXT;
