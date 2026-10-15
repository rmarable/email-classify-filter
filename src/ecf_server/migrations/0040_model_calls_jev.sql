-- 0040: v3.0.0 step 4, preset D (OD-509; plan planning-docs/v3.0.0-STEP4.md §5, P7).
-- `model_calls` gains nullable columns for TypeSafe Jev's calls, one row per call: `backend`
-- (`jev`; NULL on the local model's rows from before v3.0.0), `model` (the pinned version),
-- `fault` (the failure's class: `provider`, `request_error`, `unmapped`, `auth`, `pin`,
-- `long_wait`), `ms` (the reply's HTTP time) and `tries`. `prompt_tokens` holds Jev's billed input
-- tokens; `role` is `classifier` or `probe` and `outcome` `ok`, `error` or `timeout`, which 0018's
-- CHECKs already allow. Numbers and fixed codes only (I5); TypeSafe's request ID goes to the
-- service log only (JEV-STEP2 D7). No rebuild: older bundles import unchanged.

ALTER TABLE model_calls ADD COLUMN backend TEXT;
ALTER TABLE model_calls ADD COLUMN model TEXT;
ALTER TABLE model_calls ADD COLUMN fault TEXT;
ALTER TABLE model_calls ADD COLUMN ms INTEGER;
ALTER TABLE model_calls ADD COLUMN tries INTEGER;
