-- 0027 (SPEC §8.4; OD-059; V1.5 step 5): the send circuit breaker per address.
-- - addresses.sends_tripped_at: when the address's sends reached max_sends_per_hour or
--   max_sends_per_day; until `ecf outbound resume`, its sends wait (grants unused, jobs held).
-- The limits themselves are per-address overrides (`max_sends_per_hour` 25, `max_sends_per_day`
-- 250), changed with `ecf address set --max-sends-per-hour|--max-sends-per-day` (step-up).

ALTER TABLE addresses ADD COLUMN sends_tripped_at TEXT;
