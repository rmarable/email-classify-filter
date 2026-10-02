-- 0022 (SPEC §7.5, §13.4; V1.4 step 6; OD-268): Claude usage from the telemetry receiver, and
-- claims held until telemetry binds their submission to a model. Numbers, model IDs and fixed
-- codes only, never content (I5).
-- - claims: rebuilt to add the state 'held' (submitted and checked, waiting for telemetry to show
--   which model made the call; the item can't be claimed again meanwhile).
-- - claude_calls: one row per Claude API request of an `ecf claude` session (from telemetry).
-- - claude_sessions: one row per `ecf claude` session: plan usage (status line) at its first and
--   last reading, what it submitted, and refusals by the model check.

CREATE TABLE claims_new (
    stable_id TEXT PRIMARY KEY REFERENCES items (stable_id) ON DELETE CASCADE,
    address_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    token_hash TEXT NOT NULL,
    fence INTEGER NOT NULL DEFAULT 0,
    need TEXT NOT NULL CHECK (need IN ('classify', 'act')),
    agent TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    claimed_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('claimed', 'held', 'done', 'released')),
    invalid INTEGER NOT NULL DEFAULT 0,
    outcome TEXT,
    reported INTEGER NOT NULL DEFAULT 1 CHECK (reported IN (0, 1))
) STRICT;

INSERT INTO claims_new SELECT * FROM claims;

DROP TABLE claims;

ALTER TABLE claims_new RENAME TO claims;

CREATE INDEX claims_session ON claims (session_id, reported);

CREATE TABLE claude_calls (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    session_id TEXT NOT NULL,
    model TEXT NOT NULL,
    source TEXT NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cache_read_tokens INTEGER,
    cache_creation_tokens INTEGER,
    duration_ms INTEGER,
    cost_usd REAL
) STRICT;

CREATE INDEX claude_calls_ts ON claude_calls (ts);

CREATE TABLE claude_sessions (
    session_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    five_hour_start REAL,
    five_hour_end REAL,
    seven_day_start REAL,
    seven_day_end REAL,
    five_hour_resets TEXT,
    seven_day_resets TEXT,
    items INTEGER NOT NULL DEFAULT 0,
    submitted INTEGER NOT NULL DEFAULT 0,
    refused INTEGER NOT NULL DEFAULT 0,
    refused_model TEXT,
    expected_model TEXT
) STRICT;

CREATE INDEX claude_sessions_started ON claude_sessions (started_at);
