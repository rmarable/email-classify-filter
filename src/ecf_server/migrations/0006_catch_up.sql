-- 0006: catch-up state per address (SPEC §5.3): when the current catch-up run started, and when
-- a cooldown after hitting the cap ends.

ALTER TABLE check_state ADD COLUMN catch_up_since TEXT;
ALTER TABLE check_state ADD COLUMN cooldown_until TEXT;
