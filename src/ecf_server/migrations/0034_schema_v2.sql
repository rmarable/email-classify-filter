-- Schema v2 (SPEC §7.1; OD-475): stored classifications use v2's values. Only one value is
-- renamed, sender_type `staff` -> `team`; every other v1 value is also a v2 value, so nothing
-- else is mapped. `items.schema_version` (0001, never written before) records the schema an
-- item's classification follows: 1 -> 2 here, so the mapping runs once per item. Rules are not
-- rewritten: the rules compiler reads `staff` as `team` (rules.V1_ALIASES). Plain SQL, so
-- service start, import, restore and every other `db.migrate` path apply it alike (C1, S6).
UPDATE items SET classification = json_set(classification, '$.sender_type', 'team')
WHERE schema_version = 1 AND json_valid(classification)
    AND json_extract(classification, '$.sender_type') = 'staff';

UPDATE items SET human_correction = json_set(human_correction, '$.sender_type', 'team')
WHERE schema_version = 1 AND json_valid(human_correction)
    AND json_extract(human_correction, '$.sender_type') = 'staff';

UPDATE items SET schema_version = 2 WHERE schema_version = 1;

UPDATE fallback_shadow SET classification = json_set(classification, '$.sender_type', 'team')
WHERE json_valid(classification) AND json_extract(classification, '$.sender_type') = 'staff';
