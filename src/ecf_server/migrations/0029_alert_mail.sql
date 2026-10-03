-- 0029 (SPEC §13.3; OD-100, OD-316, OD-328, OD-333 to OD-336; V1.5 step 7a): alert email.
-- - alerts.email_opened_at / email_resolved_at: which open and resolved alerts have been handed to
--   alert email (marked whether or not email is a route, as the Slack marks are); reopening an
--   alert clears both.
-- - alert_outbox: each alert email, with the sending address and destination taken when it was
--   queued. `kind` is the cap bucket (the alert's fixed title). The text is the desktop
--   notification's (OD-316), never email content. States: queued (to send; `next_at` is the next
--   try), sent, fallback (not sent: went to Slack and the desktop instead), rolled_up (over a cap:
--   waits for its type's hourly roll-up), in_rollup (listed in a roll-up that was queued).
--   `rollup` 1 marks a roll-up itself; `slack_done` 1 when Slack already has the alert, so a
--   fallback needn't post it again. `message_id` is chosen before the first try and kept by
--   every retry. Rows settled more than 7 days ago are deleted.

ALTER TABLE alerts ADD COLUMN email_opened_at TEXT;

ALTER TABLE alerts ADD COLUMN email_resolved_at TEXT;

CREATE TABLE alert_outbox (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    address_id TEXT NOT NULL,
    destination TEXT NOT NULL,
    created_at TEXT NOT NULL,
    state TEXT NOT NULL
        CHECK (state IN ('queued', 'sent', 'fallback', 'rolled_up', 'in_rollup')),
    rollup INTEGER NOT NULL DEFAULT 0,
    slack_done INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_at TEXT NOT NULL,
    message_id TEXT,
    error TEXT,
    settled_at TEXT
) STRICT;

CREATE INDEX alert_outbox_due ON alert_outbox (state, next_at);

CREATE INDEX alert_outbox_time ON alert_outbox (created_at);
