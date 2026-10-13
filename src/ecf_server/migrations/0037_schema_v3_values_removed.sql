-- Schema v3, values removed (v3.0.0 S3b; SPEC §7.1, §9.3, §11.9; OD-500; plan
-- planning-docs/S3-READERS-SWITCH.md §6, P9, P4): the four v2 values the fixed items replace leave
-- stored classifications, read as `ecf.schema.from_v2` reads them:
--   category vendor_change_request, regulatory, spam_or_phishing -> other
--   sender_type team (v1: staff)                                 -> unknown
-- Filtered on the values only, never on `schema_version` (rows from v3.0.0 S2 on are already at
-- 3; 0036, which runs first, gave older rows their items). Before a value goes, the fixed item it
-- implied is set true (in a Fix only when the Fix doesn't answer the item itself), as S3a's
-- readers read it (raise-only: a stored answer that gave the old value and not the item keeps
-- its risk). Then `gate.passed_at` is cleared, counts kept (P4, S3a review A3): a pass measured
-- on category accuracy doesn't carry over to the new gated figure (reviews with no Fix).
-- `senders.confirmed_category` holding a removed value is cleared (B4). `items.review` holds no
-- field values; no other table stores a classification. Plain SQL, so
-- every `db.migrate` path applies it alike (service start, import, restore, rules test).
UPDATE items SET classification = json_set(classification, '$.bank_detail_change', json('true'))
WHERE json_valid(classification)
    AND json_extract(classification, '$.category') = 'vendor_change_request';

UPDATE items SET classification = json_set(classification, '$.regulator_claim', json('true'))
WHERE json_valid(classification)
    AND json_extract(classification, '$.category') = 'regulatory';

UPDATE items SET classification = json_set(classification, '$.phishing', json('true'))
WHERE json_valid(classification)
    AND json_extract(classification, '$.category') = 'spam_or_phishing';

UPDATE items SET classification = json_set(classification, '$.claims_internal', json('true'))
WHERE json_valid(classification)
    AND json_extract(classification, '$.sender_type') IN ('team', 'staff');

UPDATE items SET classification = json_set(classification, '$.category', 'other')
WHERE json_valid(classification)
    AND json_extract(classification, '$.category')
        IN ('vendor_change_request', 'regulatory', 'spam_or_phishing');

UPDATE items SET classification = json_set(classification, '$.sender_type', 'unknown')
WHERE json_valid(classification)
    AND json_extract(classification, '$.sender_type') IN ('team', 'staff');

UPDATE items SET human_correction = json_set(human_correction, '$.bank_detail_change', json('true'))
WHERE json_valid(human_correction)
    AND json_extract(human_correction, '$.category') = 'vendor_change_request'
    AND json_type(human_correction, '$.bank_detail_change') IS NULL;

UPDATE items SET human_correction = json_set(human_correction, '$.regulator_claim', json('true'))
WHERE json_valid(human_correction)
    AND json_extract(human_correction, '$.category') = 'regulatory'
    AND json_type(human_correction, '$.regulator_claim') IS NULL;

UPDATE items SET human_correction = json_set(human_correction, '$.phishing', json('true'))
WHERE json_valid(human_correction)
    AND json_extract(human_correction, '$.category') = 'spam_or_phishing'
    AND json_type(human_correction, '$.phishing') IS NULL;

UPDATE items SET human_correction = json_set(human_correction, '$.claims_internal', json('true'))
WHERE json_valid(human_correction)
    AND json_extract(human_correction, '$.sender_type') IN ('team', 'staff')
    AND json_type(human_correction, '$.claims_internal') IS NULL;

UPDATE items SET human_correction = json_set(human_correction, '$.category', 'other')
WHERE json_valid(human_correction)
    AND json_extract(human_correction, '$.category')
        IN ('vendor_change_request', 'regulatory', 'spam_or_phishing');

UPDATE items SET human_correction = json_set(human_correction, '$.sender_type', 'unknown')
WHERE json_valid(human_correction)
    AND json_extract(human_correction, '$.sender_type') IN ('team', 'staff');

UPDATE fallback_shadow SET classification = json_set(classification, '$.bank_detail_change',
    json('true'))
WHERE json_valid(classification)
    AND json_extract(classification, '$.category') = 'vendor_change_request';

UPDATE fallback_shadow SET classification = json_set(classification, '$.regulator_claim',
    json('true'))
WHERE json_valid(classification)
    AND json_extract(classification, '$.category') = 'regulatory';

UPDATE fallback_shadow SET classification = json_set(classification, '$.phishing', json('true'))
WHERE json_valid(classification)
    AND json_extract(classification, '$.category') = 'spam_or_phishing';

UPDATE fallback_shadow SET classification = json_set(classification, '$.claims_internal',
    json('true'))
WHERE json_valid(classification)
    AND json_extract(classification, '$.sender_type') IN ('team', 'staff');

UPDATE fallback_shadow SET classification = json_set(classification, '$.category', 'other')
WHERE json_valid(classification)
    AND json_extract(classification, '$.category')
        IN ('vendor_change_request', 'regulatory', 'spam_or_phishing');

UPDATE fallback_shadow SET classification = json_set(classification, '$.sender_type', 'unknown')
WHERE json_valid(classification)
    AND json_extract(classification, '$.sender_type') IN ('team', 'staff');

-- A sender confirmed with a removed category (S3b diff review B4): the category is cleared, so it
-- can't be offered or matched again; the sender's other history (passes, Reply-To, verified) stays.
UPDATE senders SET confirmed_category = NULL
WHERE confirmed_category IN ('vendor_change_request', 'regulatory', 'spam_or_phishing');

UPDATE gate SET passed_at = NULL WHERE passed_at IS NOT NULL;
