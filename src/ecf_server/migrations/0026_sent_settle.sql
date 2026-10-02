-- 0026 (SPEC §6.2, §8.4; OD-322; V1.5 step 1c): settling sends from the Sent folder.
-- - sent.searches: how many later checks searched the Sent folder for an `unknown` send without
--   finding it; after 2 (with a provider known to save sent mail) the send is `failed`.
-- - sent.copy: the sent copy in the Sent folder: `check` (look on the next check whether the
--   provider saved one; append ecf's own if not), `provider`, `ecf` (appended by ecf), `none`
--   (no Sent folder), or NULL (not sent).

ALTER TABLE sent ADD COLUMN searches INTEGER NOT NULL DEFAULT 0;

ALTER TABLE sent ADD COLUMN copy TEXT
    CHECK (copy IS NULL OR copy IN ('check', 'provider', 'ecf', 'none'));
