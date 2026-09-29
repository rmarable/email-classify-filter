-- 0008: alerts (SPEC §13.3; in V1.1 desktop notifications and `ecf doctor`, OD-190) and the
-- failure counts they are based on.

CREATE TABLE alerts (
    key TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    address_id TEXT,
    detail TEXT NOT NULL,
    opened_at TEXT NOT NULL,
    resolved_at TEXT
) STRICT;

ALTER TABLE check_state ADD COLUMN failing_since TEXT;
ALTER TABLE check_state ADD COLUMN login_failures INTEGER NOT NULL DEFAULT 0;
