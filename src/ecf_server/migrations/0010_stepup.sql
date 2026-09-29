-- 0010: step-up nonces (SPEC §9.6; V1.2 step 2): the target the service loaded (JSON; the bound
-- hash and the dialog text are computed from it, never taken from a client), the short code the
-- CLI prints and the dialog repeats, when it was verified (a verified nonce lapses after 2
-- minutes unused), and how many verifications were tried.

ALTER TABLE nonces ADD COLUMN target TEXT NOT NULL DEFAULT '{}';
ALTER TABLE nonces ADD COLUMN code TEXT NOT NULL DEFAULT '';
ALTER TABLE nonces ADD COLUMN verified_at TEXT;
ALTER TABLE nonces ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0;
