-- 0013: what each Slack post showed (its card, display name and thread), so `ecf slack reauthorize`
-- can edit every card and re-post any whose message is gone (SPEC §10.1; V1.2 step 4). The same
-- metadata the card showed in Slack; pruned with the item by retention.

ALTER TABLE slack_messages ADD COLUMN post TEXT NOT NULL DEFAULT '{}';
