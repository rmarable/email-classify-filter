-- 0043: each draft the actor writes, as numbers and hashes (M8 plan §7, OD-583). No draft or email
-- text. No foreign keys: a row outlives its item until `retention.run` prunes it by `created`.
-- `outcome` has no CHECK, so a later outcome needs no rebuild; `draft_versions.OUTCOMES` lists them.
-- NULL outcome: still waiting for a decision. The drafter's and Regenerate's columns come with them
-- (Later, OD-582); `drafter_model` is the model that wrote the draft, today's actor.

CREATE TABLE draft_versions (
    stable_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    address_id TEXT NOT NULL,
    category TEXT,
    sender_type TEXT,
    sender_seen_before INTEGER,
    drafter_model TEXT,
    actor_prompt TEXT NOT NULL,
    checker TEXT NOT NULL,
    created TEXT NOT NULL,
    email_chars INTEGER,
    draft_chars INTEGER NOT NULL,
    links_copied INTEGER NOT NULL DEFAULT 0,
    refuse_codes TEXT NOT NULL DEFAULT '[]',
    outcome TEXT,
    outcome_at TEXT,
    saved_at TEXT,
    saved_message_id_hash TEXT,
    content_hash TEXT,
    normalised_hash TEXT NOT NULL,
    edited INTEGER CHECK (edited IN (0, 1)),
    decided_after_s REAL,
    sent_after_s REAL,
    rating TEXT CHECK (rating IN ('up', 'down')),
    reason TEXT,
    note_chars INTEGER,
    alerted INTEGER NOT NULL DEFAULT 0 CHECK (alerted IN (0, 1)),
    checked_at TEXT,
    watch_until TEXT,
    missing_checks INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (stable_id, version)
);

CREATE INDEX draft_versions_address ON draft_versions (address_id, created);
CREATE INDEX draft_versions_created ON draft_versions (created);
