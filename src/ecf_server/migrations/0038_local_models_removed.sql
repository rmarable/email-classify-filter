-- 0038: local models removed (v3.0.0; ADR 0031, OD-506; plan planning-docs/REMOVE-LOCAL-MODELS.md
-- R1b, P1, P2, P3, P8, P11, P12, P13, P27). Runs with foreign-key enforcement off
-- (`db.NO_FK`): `addresses` is rebuilt while eight tables reference it, and `PRAGMA
-- foreign_key_check` must find nothing before the transaction commits.
-- 1. Every A and B address becomes C and is paused, removed ones too; it never refuses (P8). The
--    ids and old presets of the addresses not removed are kept in the setting
--    `stages.converted_v3` first, for the Python step at the next start
--    (`stages.announce_converted`: back to assist through `set_stage`, a Security Notice each;
--    P3, P12). Resuming one is the consent act (OD-427).
-- 2. `addresses` rebuilt with presets C and D only, without `fallback_enabled`,
--    `claude_queue_timeout_h` and `fallback_since`; every other column, constraint and STRICT
--    kept (no index on it besides the email's UNIQUE).
-- 3. Items the fallback had handed off and that still wait for Claude start their wait now, so
--    they aren't overdue at once (P12); then the model queue's index, `gate.ollama_digest` and
--    `fallback_shadow` go. The four `items` columns (`fallback_at`, `model_attempts`,
--    `model_failed`, `model_failed_at`) stay until v4: dropping a column rewrites the whole
--    table (diff review R1); nothing reads or writes them (`db.UNUSED_COLUMNS`). Gate rows are
--    kept (R-D8, P13); `model_calls`, `eval_runs` and the `jobs.queue` CHECK are left as they are.
--    The Claude queue gets its own index, matching the queries that read it each tick
--    (`claude_queue.backlog`, `overdue`, `waiting`; `claude_review` review_queue; R2).
-- 4. Alert rows of kinds this version no longer raises, and settings it no longer reads (P11).
--    `models_missing` stays: the Claude pins' alert.

INSERT OR REPLACE INTO settings (key, value, updated_at, updated_by)
SELECT 'stages.converted_v3', converted,
    replace(strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'Z', '000Z'), 'migration'
FROM (SELECT json_group_object(address_id, preset) AS converted, count(*) AS n
      FROM (SELECT address_id, preset FROM addresses
            WHERE preset IN ('A', 'B') AND removed_at IS NULL ORDER BY address_id))
WHERE n > 0;

UPDATE addresses SET preset = 'C', paused = 1 WHERE preset IN ('A', 'B');

CREATE TABLE addresses_new (
    address_id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    display_name TEXT,
    sensitivity TEXT NOT NULL CHECK (sensitivity IN ('standard', 'high')),
    stage TEXT NOT NULL DEFAULT 'shadow' CHECK (stage IN ('shadow', 'assist', 'live')),
    paused INTEGER NOT NULL DEFAULT 0 CHECK (paused IN (0, 1)),
    outbound INTEGER NOT NULL DEFAULT 0 CHECK (outbound IN (0, 1)),
    preset TEXT NOT NULL CHECK (preset IN ('C', 'D')),
    classifier_id TEXT,
    actor_id TEXT,
    overrides TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    removed_at TEXT,
    smtp_host TEXT,
    smtp_port INTEGER CHECK (smtp_port IS NULL OR smtp_port IN (465, 587)),
    sends_tripped_at TEXT,
    outbound_reminders INTEGER NOT NULL DEFAULT 0,
    outbound_reminded_at TEXT,
    outbound_snoozed_until TEXT,
    outbound_dismissed_at TEXT
) STRICT;

INSERT INTO addresses_new (address_id, email, display_name, sensitivity, stage, paused, outbound,
    preset, classifier_id, actor_id, overrides, created_at, removed_at, smtp_host, smtp_port,
    sends_tripped_at, outbound_reminders, outbound_reminded_at, outbound_snoozed_until,
    outbound_dismissed_at)
SELECT address_id, email, display_name, sensitivity, stage, paused, outbound, preset,
    classifier_id, actor_id, overrides, created_at, removed_at, smtp_host, smtp_port,
    sends_tripped_at, outbound_reminders, outbound_reminded_at, outbound_snoozed_until,
    outbound_dismissed_at
FROM addresses;

DROP TABLE addresses;

ALTER TABLE addresses_new RENAME TO addresses;

UPDATE items SET claude_since = replace(strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'Z', '000Z')
WHERE fallback_at IS NOT NULL AND status IN ('awaiting_claude', 'clarified');

DROP INDEX items_model_waiting;

CREATE INDEX items_claude_waiting ON items (address_id, claude_since)
    WHERE status IN ('awaiting_claude', 'clarified');

ALTER TABLE gate DROP COLUMN ollama_digest;

DROP TABLE fallback_shadow;

DELETE FROM alerts
WHERE kind IN ('local_model', 'local_model_unsafe', 'model_failures', 'local_model_server');

DELETE FROM settings
WHERE key IN ('resident', 'ollama_log_max_mb', 'ollama_log_rotate_days', 'daily.model_backlog',
              'daily.battery_seconds');
