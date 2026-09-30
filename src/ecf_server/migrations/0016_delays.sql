-- 0016 (SPEC §9.5; V1.2 step 7b): sends on `high` addresses wait 10 minutes of awake time after
-- approval, with Cancel. The time left is kept here and counted down by the service's timer with
-- the monotonic clock, which stops while the computer sleeps, so the delay survives a restart and
-- never runs out during sleep.

CREATE TABLE delays (
    stable_id TEXT PRIMARY KEY REFERENCES items (stable_id) ON DELETE CASCADE,
    grant_id TEXT NOT NULL,
    remaining_s REAL NOT NULL,
    created_at TEXT NOT NULL,
    announced_at TEXT
) STRICT;
