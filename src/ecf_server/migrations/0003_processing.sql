-- 0003: crash-safe processing (SPEC §5.1): a marker is written before each message is read and
-- removed after; a marker that survives two crashes quarantines the message.

CREATE TABLE processing (
    address_id TEXT NOT NULL REFERENCES addresses (address_id) ON DELETE CASCADE,
    uidvalidity INTEGER NOT NULL,
    uid INTEGER NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    PRIMARY KEY (address_id, uidvalidity, uid)
) STRICT;
