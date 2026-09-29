-- 0009: the earliest arrival time (INTERNALDATE) among deferred messages, so recovery after a
-- mailbox reset re-reads far enough back to find them (SPEC §6.4; V1.1 review, 2026-09-29).

ALTER TABLE cursors ADD COLUMN deferred_since TEXT;
