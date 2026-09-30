-- 0017 (SPEC §13.3; V1.2 step 9): which alerts have been posted to Slack. The Slack thread posts
-- an alert when it opens and again when it resolves; reopening clears both marks.

ALTER TABLE alerts ADD COLUMN slack_opened_at TEXT;
ALTER TABLE alerts ADD COLUMN slack_resolved_at TEXT;
