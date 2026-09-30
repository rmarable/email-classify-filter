-- 0012: a `slack_in` job queue for clicks and form submissions (SPEC §10.1; V1.2 step 3b), so a
-- click acknowledged just before a crash is still handled. SQLite can't change a CHECK
-- constraint in place, so the table is rebuilt with the same columns, rows and indexes.

CREATE TABLE jobs_new (
    job_id TEXT PRIMARY KEY,
    queue TEXT NOT NULL CHECK (queue IN ('actions', 'slack_out', 'slack_in', 'fetch', 'model')),
    address_id TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 5,
    timeout_s INTEGER NOT NULL,
    visible_at TEXT NOT NULL,
    claimed_by TEXT,
    claim_expires TEXT,
    state TEXT NOT NULL DEFAULT 'queued' CHECK (state IN ('queued', 'claimed', 'done', 'dead')),
    last_error TEXT,
    created_at TEXT NOT NULL
) STRICT;

INSERT INTO jobs_new SELECT * FROM jobs;
DROP TABLE jobs;
ALTER TABLE jobs_new RENAME TO jobs;
CREATE INDEX jobs_ready ON jobs (queue, state, visible_at);
CREATE INDEX jobs_claimed ON jobs (queue, address_id) WHERE state = 'claimed';
