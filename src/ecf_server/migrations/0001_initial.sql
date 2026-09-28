-- 0001: initial v1 schema (SPEC §6.1). Timestamps are UTC ISO-8601 text; JSON columns are text
-- validated by Pydantic in the service. senders, sent, threads and gate have no foreign key to
-- addresses: they outlive address removal and retention (SPEC §6.5).

CREATE TABLE addresses (
    address_id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    display_name TEXT,
    sensitivity TEXT NOT NULL CHECK (sensitivity IN ('standard', 'high')),
    stage TEXT NOT NULL DEFAULT 'shadow' CHECK (stage IN ('shadow', 'assist', 'live')),
    paused INTEGER NOT NULL DEFAULT 0 CHECK (paused IN (0, 1)),
    outbound INTEGER NOT NULL DEFAULT 0 CHECK (outbound IN (0, 1)),
    preset TEXT NOT NULL CHECK (preset IN ('A', 'B', 'C')),
    classifier_id TEXT,
    actor_id TEXT,
    fallback_enabled INTEGER NOT NULL DEFAULT 0 CHECK (fallback_enabled IN (0, 1)),
    claude_queue_timeout_h INTEGER,
    overrides TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    removed_at TEXT
) STRICT;

CREATE TABLE cursors (
    address_id TEXT PRIMARY KEY REFERENCES addresses (address_id) ON DELETE CASCADE,
    uidvalidity INTEGER,
    last_uid INTEGER NOT NULL DEFAULT 0,
    deferred_uids TEXT NOT NULL DEFAULT '[]',
    version INTEGER NOT NULL DEFAULT 0
) STRICT;

CREATE TABLE leases (
    address_id TEXT PRIMARY KEY REFERENCES addresses (address_id) ON DELETE CASCADE,
    holder TEXT NOT NULL,
    fencing_token INTEGER NOT NULL,
    expires_at TEXT NOT NULL
) STRICT;

CREATE TABLE probe (
    address_id TEXT PRIMARY KEY REFERENCES addresses (address_id) ON DELETE CASCADE,
    special_use TEXT NOT NULL DEFAULT '{}',
    permanent_keywords INTEGER NOT NULL DEFAULT 0 CHECK (permanent_keywords IN (0, 1)),
    saves_sent INTEGER CHECK (saves_sent IN (0, 1)),
    max_message_bytes INTEGER,
    host TEXT,
    probed_at TEXT NOT NULL
) STRICT;

CREATE TABLE items (
    stable_id TEXT PRIMARY KEY,
    address_id TEXT NOT NULL REFERENCES addresses (address_id),
    uid INTEGER,
    uidvalidity INTEGER,
    message_id TEXT,
    locator TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL CHECK (status IN ('new', 'classified', 'awaiting_claude', 'proposed', 'held', 'awaiting_approval', 'awaiting_stepup', 'approved', 'delayed', 'executing', 'failed', 'failed_unknown', 'expired', 'needs_clarification', 'clarified', 'needs_human', 'undoing', 'undo_failed', 'observed', 'executed', 'undone', 'rejected', 'cancelled', 'resolved_manual', 'resolved_by_mailbox')),
    stale INTEGER NOT NULL DEFAULT 0 CHECK (stale IN (0, 1)),
    prechecked INTEGER NOT NULL DEFAULT 0 CHECK (prechecked IN (0, 1)),
    decision_source TEXT CHECK (decision_source IN ('rule', 'actor', 'human')),
    proposed_by TEXT,
    suppressed_action TEXT,
    review TEXT,
    human_correction TEXT,
    classification TEXT,
    facts TEXT NOT NULL DEFAULT '{}',
    proposal TEXT,
    pinned_models TEXT,
    batch_id TEXT,
    content_hash TEXT NOT NULL,
    hash_version INTEGER NOT NULL DEFAULT 1,
    duplicate_message_id INTEGER NOT NULL DEFAULT 0 CHECK (duplicate_message_id IN (0, 1)),
    expiry_count INTEGER NOT NULL DEFAULT 0,
    clarification_rounds INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1
) STRICT;

CREATE INDEX items_open ON items (address_id, updated_at)
    WHERE status NOT IN ('observed', 'executed', 'undone', 'rejected', 'cancelled',
                         'resolved_manual', 'resolved_by_mailbox');

CREATE TABLE excerpts (
    stable_id TEXT PRIMARY KEY REFERENCES items (stable_id) ON DELETE CASCADE,
    classifier_text TEXT,
    actor_text TEXT CHECK (actor_text IS NULL OR length(actor_text) <= 4000)
) STRICT;

CREATE TABLE grants (
    grant_id TEXT PRIMARY KEY,
    stable_id TEXT NOT NULL REFERENCES items (stable_id) ON DELETE CASCADE,
    action_hash TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    principal TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('issued', 'approved', 'consumed', 'voided')),
    stepup_nonce_id TEXT,
    expires_at TEXT NOT NULL,
    consumed_at TEXT
) STRICT;

CREATE TABLE nonces (
    nonce_id TEXT PRIMARY KEY,
    purpose TEXT NOT NULL,
    bound_hash TEXT NOT NULL,
    person TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    consumed_at TEXT
) STRICT;

CREATE TABLE jobs (
    job_id TEXT PRIMARY KEY,
    queue TEXT NOT NULL CHECK (queue IN ('actions', 'slack_out', 'fetch', 'model')),
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

CREATE INDEX jobs_ready ON jobs (queue, state, visible_at);
CREATE INDEX jobs_claimed ON jobs (queue, address_id) WHERE state = 'claimed';

CREATE TABLE sent (
    message_id_hash TEXT PRIMARY KEY,
    address_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('reply', 'forward', 'alert')),
    sent_at TEXT NOT NULL
) STRICT;

CREATE TABLE threads (
    thread_hash TEXT PRIMARY KEY,
    address_id TEXT NOT NULL,
    template_replies INTEGER NOT NULL DEFAULT 0
) STRICT;

CREATE TABLE senders (
    address_id TEXT NOT NULL,
    sender_hash TEXT NOT NULL,
    dmarc_pass_count INTEGER NOT NULL DEFAULT 0,
    first_pass_at TEXT,
    last_pass_at TEXT,
    confirmed_category TEXT,
    confirmed_at TEXT,
    expected_reply_to_domain TEXT,
    verified_rule1a INTEGER NOT NULL DEFAULT 0 CHECK (verified_rule1a IN (0, 1)),
    payment_history INTEGER NOT NULL DEFAULT 0 CHECK (payment_history IN (0, 1)),
    PRIMARY KEY (address_id, sender_hash)
) STRICT;

CREATE TABLE gate (
    address_id TEXT NOT NULL,
    pair_key TEXT NOT NULL,
    reviewed INTEGER NOT NULL DEFAULT 0,
    correct INTEGER NOT NULL DEFAULT 0,
    fraud_misses INTEGER NOT NULL DEFAULT 0,
    unsafe INTEGER NOT NULL DEFAULT 0,
    pinned_ids TEXT,
    ollama_digest TEXT,
    passed_at TEXT,
    PRIMARY KEY (address_id, pair_key)
) STRICT;

CREATE TABLE eval_results (
    pair_key TEXT NOT NULL,
    set_version TEXT NOT NULL,
    metrics TEXT NOT NULL,
    gate_passed INTEGER NOT NULL CHECK (gate_passed IN (0, 1)),
    run_at TEXT NOT NULL,
    PRIMARY KEY (pair_key, set_version)
) STRICT;

CREATE TABLE routes (
    address_id TEXT NOT NULL REFERENCES addresses (address_id) ON DELETE CASCADE,
    surface TEXT NOT NULL,
    route_ref TEXT NOT NULL,
    thread_refs TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (address_id, surface)
) STRICT;

CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL
) STRICT;

CREATE TABLE rate (
    key TEXT PRIMARY KEY,
    window_start TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0
) STRICT;

CREATE TABLE dns_cache (
    name TEXT NOT NULL,
    rtype TEXT NOT NULL,
    answer TEXT NOT NULL,
    negative INTEGER NOT NULL DEFAULT 0 CHECK (negative IN (0, 1)),
    expires_at TEXT NOT NULL,
    PRIMARY KEY (name, rtype)
) STRICT;

CREATE TABLE audit (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    address_id TEXT,
    stable_id TEXT,
    event TEXT NOT NULL,
    actor TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('ok', 'denied', 'error')),
    data TEXT NOT NULL DEFAULT '{}'
) STRICT;

CREATE INDEX audit_ts ON audit (ts);

CREATE TABLE heartbeats (
    watcher TEXT PRIMARY KEY,
    last_seen TEXT NOT NULL,
    addresses TEXT NOT NULL DEFAULT '[]'
) STRICT;

CREATE TABLE slack_dedupe (
    payload_id TEXT PRIMARY KEY,
    seen_at TEXT NOT NULL
) STRICT;
