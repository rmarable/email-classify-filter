-- 0007: UIDVALIDITY recovery (SPEC §6.4): UIDs up to this one are a re-fetch after a mailbox
-- reset, so messages already known are re-pointed, not recorded as repeat deliveries.

ALTER TABLE cursors ADD COLUMN recovering_until_uid INTEGER NOT NULL DEFAULT 0;
