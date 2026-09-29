-- 0005: per-address check state (V1.1 step 11): what `ecf status` shows and the scheduler reads.

CREATE TABLE check_state (
    address_id TEXT PRIMARY KEY REFERENCES addresses (address_id) ON DELETE CASCADE,
    last_started_at TEXT,
    last_finished_at TEXT,
    last_status TEXT,
    last_result TEXT NOT NULL DEFAULT '{}',
    backlog INTEGER NOT NULL DEFAULT 0,
    deferred INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    last_error_at TEXT,
    next_due_at TEXT
) STRICT;
