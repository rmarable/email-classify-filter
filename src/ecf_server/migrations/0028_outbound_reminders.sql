-- 0028 (SPEC §9.8; V1.5 step 6): outbound reminders per address.
-- - outbound_reminders: how many have been sent (the first 5 are full reminders: day 7 after the
--   address first went live, then weekly; after that, one summary line a month).
-- - outbound_reminded_at: when the last one was sent.
-- - outbound_snoozed_until: no reminder before this (`ecf outbound snooze`).
-- - outbound_dismissed_at: no more reminders for this address (`ecf outbound dismiss`).

ALTER TABLE addresses ADD COLUMN outbound_reminders INTEGER NOT NULL DEFAULT 0;

ALTER TABLE addresses ADD COLUMN outbound_reminded_at TEXT;

ALTER TABLE addresses ADD COLUMN outbound_snoozed_until TEXT;

ALTER TABLE addresses ADD COLUMN outbound_dismissed_at TEXT;
