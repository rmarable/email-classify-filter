-- 0039: v3.0.0 step 5 history (OD-507; plan planning-docs/v3.0.0-STEP5.md §5, P17, P19).
-- 1. `slack_calls`: one row per Slack post job when it completes (posted, given up or its channel
--    gone): when it was queued, how many tries, the seconds spent in Slack calls, the seconds held
--    for rate limits and the seconds spent pacing (1 post per second per channel). The post's key
--    kind only (`item`, `burst`, `needs-you`, ...), never its text. Pruned with the logs
--    (`log_retention_days`); not in the bundle (P17: DATA_FORMAT stays 4).
-- 2. Two audit indexes: `ecf stats --stages` reads `item.transitioned` rows by event and time, and
--    each item's rows in order (P19).

CREATE TABLE slack_calls (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    queued_at TEXT NOT NULL,
    op TEXT NOT NULL,
    kind TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('posted', 'dead', 'channel_gone')),
    attempts INTEGER NOT NULL,
    call_ms INTEGER NOT NULL,
    rate_hold_s INTEGER NOT NULL,
    pace_ms INTEGER NOT NULL
) STRICT;

CREATE INDEX slack_calls_ts ON slack_calls (ts);

CREATE INDEX audit_event_ts ON audit (event, ts);

CREATE INDEX audit_stable_id ON audit (stable_id, id);
