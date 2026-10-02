-- 0021 (SPEC §10.4, §15.1; V1.4 step 3; OD-267): claims for `/ecf-review`. `review_queue` claims
-- the items it returns for one `ecf claude` session; each claim has a token (only its SHA-256 is
-- kept) carrying a fencing number, so a submission under an older or expired claim is refused.
-- - claims: one row per item, reused by its next claim (`fence` goes up each time); `outcome` is
--   what the submission did, reported once in the next `review_queue` result.
-- - claim_batches: every item a batch ever held (an item claimed again keeps its earlier batch),
--   for the cross-item hide guard (§5.6).

CREATE TABLE claims (
    stable_id TEXT PRIMARY KEY REFERENCES items (stable_id) ON DELETE CASCADE,
    address_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    token_hash TEXT NOT NULL,
    fence INTEGER NOT NULL DEFAULT 0,
    need TEXT NOT NULL CHECK (need IN ('classify', 'act')),
    agent TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    claimed_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('claimed', 'done', 'released')),
    invalid INTEGER NOT NULL DEFAULT 0,
    outcome TEXT,
    reported INTEGER NOT NULL DEFAULT 1 CHECK (reported IN (0, 1))
) STRICT;

CREATE INDEX claims_session ON claims (session_id, reported);

CREATE TABLE claim_batches (
    batch_id TEXT NOT NULL,
    stable_id TEXT NOT NULL REFERENCES items (stable_id) ON DELETE CASCADE,
    PRIMARY KEY (batch_id, stable_id)
) STRICT;

CREATE INDEX claim_batches_item ON claim_batches (stable_id);
