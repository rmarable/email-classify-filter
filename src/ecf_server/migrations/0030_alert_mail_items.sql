-- 0030 (SPEC §13.3; OD-114, OD-334; V1.5 step 7b): which escalations have been handed to alert
-- email (Possible Fraud Attempt, Regulatory Mail Notice). Marked whether or not email is a route,
-- so turning email on later sends no backlog; escalations from before this migration count as
-- handled.

ALTER TABLE escalations ADD COLUMN emailed_at TEXT;

UPDATE escalations SET emailed_at = created_at;
