-- 0025 (SPEC §8.4, §11.9; OD-318, OD-322; V1.5 step 1b): who sent it, and what became of each send.
-- - settings install.id: a random 128-bit ID, written once here and never changed (a restore of
--   the same install keeps it; an import never takes one from a bundle). install.generation:
--   0, raised by each restore. Both go in `X-ECF-Install: <id>.<generation>` on every send.
-- - sent: the Message-ID itself (ecf's own random one, not email content), the item and grant
--   it belongs to (one Message-ID per grant, reused by every attempt), and the send's state:
--   pending (recorded, not yet handed over), sent, unknown (handed over, no final reply) or
--   failed (known not sent); settled_at when it stopped being pending or unknown. Rows from
--   before this migration are alerts that were never written (none exist): marked sent.

INSERT OR IGNORE INTO settings (key, value, updated_at, updated_by)
VALUES ('install.id', '"' || lower(hex(randomblob(16))) || '"',
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'migration');

INSERT OR IGNORE INTO settings (key, value, updated_at, updated_by)
VALUES ('install.generation', '0', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'migration');

ALTER TABLE sent ADD COLUMN message_id TEXT;

ALTER TABLE sent ADD COLUMN stable_id TEXT;

ALTER TABLE sent ADD COLUMN grant_id TEXT;

ALTER TABLE sent ADD COLUMN status TEXT NOT NULL DEFAULT 'sent'
    CHECK (status IN ('pending', 'sent', 'unknown', 'failed'));

ALTER TABLE sent ADD COLUMN settled_at TEXT;

CREATE UNIQUE INDEX sent_grant ON sent (grant_id) WHERE grant_id IS NOT NULL;

CREATE INDEX sent_address_time ON sent (address_id, sent_at);
