-- 0015 (SPEC §5.4, §10.1; V1.2 step 6): what an item card shows, and the escalations waiting to
-- be posted.
-- - items: the subject and sender as received (capped, text only), the metadata a card shows;
--   items fetched before V1.2 have none.
-- - escalations: one row per pre-check escalation, so posting reads a small indexed table instead
--   of every item's facts. Escalations V1.1 recorded as pending become 'v1.1': they go into one
--   summary post when Slack is installed (OD-211), never a thread each.

ALTER TABLE items ADD COLUMN subject TEXT;
ALTER TABLE items ADD COLUMN sender TEXT;
ALTER TABLE items ADD COLUMN sender_name TEXT;

CREATE TABLE escalations (
    stable_id TEXT PRIMARY KEY REFERENCES items (stable_id) ON DELETE CASCADE,
    address_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('pending', 'posted', 'merged', 'summarized', 'v1.1')),
    thread_key TEXT,
    created_at TEXT NOT NULL,
    posted_at TEXT
) STRICT;

CREATE INDEX escalations_waiting ON escalations (state, address_id, created_at)
    WHERE state IN ('pending', 'v1.1');
CREATE INDEX escalations_posted ON escalations (address_id, posted_at)
    WHERE posted_at IS NOT NULL;

INSERT INTO escalations (stable_id, address_id, state, created_at)
    SELECT stable_id, address_id, 'v1.1', coalesce(json_extract(facts, '$.precheck.at'), created_at)
    FROM items WHERE json_extract(facts, '$.precheck.escalation') = 'pending Slack (V1.2)';
