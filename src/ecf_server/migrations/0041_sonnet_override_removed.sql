-- 0041: v3.0.0 step 6f, every gate role on Opus (OD-520, OD-521; plan
-- planning-docs/v3.0.0-STEP6.md §7, the mirror of P18). `models.lock` no longer pins a Sonnet
-- model, so a stored `sonnet` family override (`claude_model_override`, {family: id}) would be
-- orphaned: `claude_pins._applied` ignores it, `ecf models status` would still show it, and
-- `ecf settings set claude_model_override` now refuses a Sonnet ID. The `sonnet` key is removed,
-- the row too when nothing else is left in it, and an audit row records the change
-- (`models.override_changed`, actor `migration`, as `claude_pins.set_override` writes it). Every
-- C address's gate pins change with the lock anyway, so removing it moves no further address.

INSERT INTO audit (ts, address_id, event, actor, outcome, data)
SELECT replace(strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'Z', '000Z'), NULL,
    'models.override_changed', 'migration', 'ok',
    json_object('from', json(value), 'to', json(json_remove(value, '$.sonnet')))
FROM settings
WHERE key = 'claude_model_override' AND json_type(value, '$.sonnet') IS NOT NULL;

UPDATE settings
SET value = json_remove(value, '$.sonnet'),
    updated_at = replace(strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'Z', '000Z'),
    updated_by = 'migration'
WHERE key = 'claude_model_override' AND json_type(value, '$.sonnet') IS NOT NULL;

DELETE FROM settings
WHERE key = 'claude_model_override' AND (SELECT count(*) FROM json_each(settings.value)) = 0;
