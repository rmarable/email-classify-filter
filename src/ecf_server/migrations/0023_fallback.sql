-- 0023 (SPEC §4.3; OD-019, OD-020; V1.4 step 8): the local fallback for the Claude queue.
-- - items.claude_since: when the item last started waiting for Claude (`awaiting_claude`, or an
--   answered question at `clarified`); `claude_queue_timeout` counts from it.
-- - items.fallback_at: when the item was handed to the local model after waiting too long.
-- - addresses.fallback_since: when the fallback was turned on; shadow runs cover mail from then.
-- - fallback_shadow: the local model's shadow run on one email of a B or C address (its own
--   classification for C, the item's for B; what the rule and the local actor would do). Fixed
--   codes and the model's structured fields only, never the email or the actor's reason (I5).

ALTER TABLE items ADD COLUMN claude_since TEXT;

ALTER TABLE items ADD COLUMN fallback_at TEXT;

ALTER TABLE addresses ADD COLUMN fallback_since TEXT;

UPDATE items SET claude_since = updated_at WHERE status IN ('awaiting_claude', 'clarified');

CREATE TABLE fallback_shadow (
    stable_id TEXT PRIMARY KEY REFERENCES items (stable_id) ON DELETE CASCADE,
    address_id TEXT NOT NULL,
    digest TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('ok', 'failed')),
    classification TEXT,
    plan TEXT,
    created_at TEXT NOT NULL
) STRICT;

CREATE INDEX fallback_shadow_address ON fallback_shadow (address_id, digest);
