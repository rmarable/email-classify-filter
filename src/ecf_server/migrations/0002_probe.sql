-- 0002: probe results beyond the initial columns (V1.1 step 4, SPEC §18): server capabilities,
-- where the size limit came from, and warnings shown to the operator.

ALTER TABLE probe ADD COLUMN capabilities TEXT NOT NULL DEFAULT '{}';
ALTER TABLE probe ADD COLUMN max_size_source TEXT;
ALTER TABLE probe ADD COLUMN warnings TEXT NOT NULL DEFAULT '[]';
