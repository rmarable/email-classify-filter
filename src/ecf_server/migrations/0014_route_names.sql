-- 0014: each address's Slack channel records its name and the member ID invited to it, so ecf
-- re-invites you after a member-ID change and archives only channels it recorded (SPEC §10.1;
-- V1.2 step 5). The summary channel is recorded in settings (it belongs to no address).

ALTER TABLE routes ADD COLUMN name TEXT NOT NULL DEFAULT '';
ALTER TABLE routes ADD COLUMN invited TEXT NOT NULL DEFAULT '';
