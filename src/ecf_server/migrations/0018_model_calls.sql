-- 0018 (SPEC §13.4; V1.3 step 1a): one row per local-model call. Numbers and fixed codes only,
-- never content (I5): no column may hold text taken from a message or from the model's output.

CREATE TABLE model_calls (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    address_id TEXT,
    preset TEXT,
    stage TEXT,
    role TEXT NOT NULL CHECK (role IN ('classifier', 'actor', 'fallback_shadow', 'eval', 'probe')),
    outcome TEXT NOT NULL CHECK (outcome IN ('ok', 'schema_failure', 'truncated', 'timeout', 'error')),
    digest TEXT NOT NULL,
    prompt_tokens INTEGER,
    cached_tokens INTEGER,
    output_tokens INTEGER,
    prompt_ns INTEGER,
    eval_ns INTEGER,
    load_ns INTEGER,
    total_ns INTEGER
) STRICT;

CREATE INDEX model_calls_ts ON model_calls (ts);
