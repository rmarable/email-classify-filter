-- Schema v3 (SPEC §7.1; OD-498; schema doc §9, P13): stored classifications gain the fixed safety
-- items by the fixed table (`ecf.schema.FIXED_FROM_V2`; the same rows here):
--   category vendor_change_request    -> bank_detail_change
--   category regulatory               -> regulator_claim
--   sender_type regulator             -> regulator_claim
--   category spam_or_phishing         -> phishing
--   sender_type team (v1: staff)      -> claims_internal
--   sender_type person, friend, family -> private_individual
-- Filtered: only rows with a mapped value are written, each item set true; a fixed item missing
-- from a stored classification reads as false (P13). A Fix (`human_correction`, a partial
-- classification laid over the model's) gets an item whenever it sets one of that item's source
-- fields, true or false as the Fix and the model's answer together say, so the corrected view
-- stays consistent. v1's `staff` is matched too (0034 already renamed it). `items.schema_version`
-- goes to 3 last, so each item is moved once; rows below 3 are read (a row created after 0034
-- keeps the column's default, 1, until the classifier records 2). `items.review` holds no field
-- values; no other table stores a classification. Plain SQL, so every `db.migrate` path applies it alike (C1, S6).
UPDATE items SET classification = json_set(classification, '$.bank_detail_change', json('true'))
WHERE schema_version < 3 AND json_valid(classification)
    AND json_extract(classification, '$.category') = 'vendor_change_request';

UPDATE items SET classification = json_set(classification, '$.regulator_claim', json('true'))
WHERE schema_version < 3 AND json_valid(classification)
    AND (json_extract(classification, '$.category') = 'regulatory'
         OR json_extract(classification, '$.sender_type') = 'regulator');

UPDATE items SET classification = json_set(classification, '$.phishing', json('true'))
WHERE schema_version < 3 AND json_valid(classification)
    AND json_extract(classification, '$.category') = 'spam_or_phishing';

UPDATE items SET classification = json_set(classification, '$.claims_internal', json('true'))
WHERE schema_version < 3 AND json_valid(classification)
    AND json_extract(classification, '$.sender_type') IN ('team', 'staff');

UPDATE items SET classification = json_set(classification, '$.private_individual', json('true'))
WHERE schema_version < 3 AND json_valid(classification)
    AND json_extract(classification, '$.sender_type') IN ('person', 'friend', 'family');

UPDATE items SET human_correction = json_set(human_correction,
    '$.bank_detail_change',
    json(CASE WHEN json_extract(human_correction, '$.category') = 'vendor_change_request'
         THEN 'true' ELSE 'false' END),
    '$.phishing',
    json(CASE WHEN json_extract(human_correction, '$.category') = 'spam_or_phishing'
         THEN 'true' ELSE 'false' END))
WHERE schema_version < 3 AND json_valid(human_correction)
    AND json_type(human_correction, '$.category') IS NOT NULL;

UPDATE items SET human_correction = json_set(human_correction,
    '$.claims_internal',
    json(CASE WHEN json_extract(human_correction, '$.sender_type') IN ('team', 'staff')
         THEN 'true' ELSE 'false' END),
    '$.private_individual',
    json(CASE WHEN json_extract(human_correction, '$.sender_type') IN ('person', 'friend', 'family')
         THEN 'true' ELSE 'false' END))
WHERE schema_version < 3 AND json_valid(human_correction)
    AND json_type(human_correction, '$.sender_type') IS NOT NULL;

UPDATE items SET human_correction = json_set(human_correction, '$.regulator_claim',
    json(CASE WHEN coalesce(json_extract(human_correction, '$.category'),
                            json_extract(classification, '$.category')) = 'regulatory'
              OR coalesce(json_extract(human_correction, '$.sender_type'),
                          json_extract(classification, '$.sender_type')) = 'regulator'
         THEN 'true' ELSE 'false' END))
WHERE schema_version < 3 AND json_valid(human_correction)
    AND (json_type(human_correction, '$.category') IS NOT NULL
         OR json_type(human_correction, '$.sender_type') IS NOT NULL);

UPDATE items SET schema_version = 3 WHERE schema_version < 3;

UPDATE fallback_shadow SET classification = json_set(classification, '$.bank_detail_change',
    json('true'))
WHERE json_valid(classification)
    AND json_extract(classification, '$.category') = 'vendor_change_request';

UPDATE fallback_shadow SET classification = json_set(classification, '$.regulator_claim',
    json('true'))
WHERE json_valid(classification)
    AND (json_extract(classification, '$.category') = 'regulatory'
         OR json_extract(classification, '$.sender_type') = 'regulator');

UPDATE fallback_shadow SET classification = json_set(classification, '$.phishing', json('true'))
WHERE json_valid(classification)
    AND json_extract(classification, '$.category') = 'spam_or_phishing';

UPDATE fallback_shadow SET classification = json_set(classification, '$.claims_internal',
    json('true'))
WHERE json_valid(classification)
    AND json_extract(classification, '$.sender_type') IN ('team', 'staff');

UPDATE fallback_shadow SET classification = json_set(classification, '$.private_individual',
    json('true'))
WHERE json_valid(classification)
    AND json_extract(classification, '$.sender_type') IN ('person', 'friend', 'family');
