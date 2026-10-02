-- 0020 (SPEC §9.3, §16; V1.3 step 8c): eval runs keyed by the model digest they ran under, so the
-- go-live gate binds to the model it was measured with. Metrics only, never message or model text.
-- (0001's eval_results, keyed by pair and set only, stays unused.)

CREATE TABLE eval_runs (
    run_id TEXT PRIMARY KEY,
    pair TEXT NOT NULL,
    digest TEXT,
    set_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    metrics TEXT NOT NULL,
    gate_passed INTEGER NOT NULL CHECK (gate_passed IN (0, 1)),
    path TEXT NOT NULL
) STRICT;

CREATE INDEX eval_runs_digest ON eval_runs (digest, created_at);
