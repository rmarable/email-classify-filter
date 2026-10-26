-- 0042: the classifier reads 4,000 characters (window tuning step 8, OD-558; plan
-- planning-docs/CLASSIFIER-WINDOW-TUNING.md). `excerpts.classifier_chars` records the window an
-- excerpt was cut at, written at fetch; excerpts kept before this release were cut at 1,500. The
-- second CHECK caps the stored classifier excerpt at the largest window, as `actor_text`'s
-- does (0001). SQLite adds no table-level CHECK to an existing table, so it rides on the new
-- column; it is tested against every existing row, which are at most 1,501 characters (an
-- excerpt cut before OD-552 could be one over). No rebuild: nothing is copied, and an orphaned
-- excerpt (one whose item is gone) is left as it was.

ALTER TABLE excerpts ADD COLUMN classifier_chars INTEGER
    CHECK (classifier_chars IS NULL OR classifier_chars BETWEEN 500 AND 4000)
    CHECK (classifier_text IS NULL OR length(classifier_text) <= 4000);

UPDATE excerpts SET classifier_chars = 1500 WHERE classifier_text IS NOT NULL;
