-- 0019 (SPEC §5.1 step 5, §5.2; V1.3 step 2a; OD-236): local-model attempts per item. An item
-- whose model work failed twice is marked `model_failed` (an attribute, not a status): it stays at
-- `new`, shows in "Needs you", and the model queue skips it.

ALTER TABLE items ADD COLUMN model_attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE items ADD COLUMN model_failed INTEGER NOT NULL DEFAULT 0 CHECK (model_failed IN (0, 1));
ALTER TABLE items ADD COLUMN model_failed_at TEXT;

CREATE INDEX items_model_waiting ON items (address_id, created_at)
    WHERE status = 'new' AND model_failed = 0;
