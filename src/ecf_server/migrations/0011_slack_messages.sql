-- 0011: Slack messages ecf posted (SPEC §10.1; V1.2 step 3), keyed by what they show (an item's
-- card, "Needs you", a digest), so a card is edited in place by its stored channel and ts, never
-- by anything taken from a click, and a retried post edits instead of posting twice.

CREATE TABLE slack_messages (
    key TEXT PRIMARY KEY,
    channel TEXT NOT NULL,
    ts TEXT NOT NULL,
    thread_ts TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
) STRICT;
