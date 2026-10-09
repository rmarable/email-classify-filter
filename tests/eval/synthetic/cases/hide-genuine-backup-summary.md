---
id: hide-genuine-backup-summary
title: Weekly backup summary from a known, authenticated bulk sender
threat: none (hide-path control)
control: rule 8 archives an authenticated bulk notice from a sender seen before (bulk_corroborates)
why: the eval never saw rule 8's hide; this is the case it should take
failure_looks_like: left in the inbox, or flagged
from: "Backup Service <reports@backup-vault.example>"
subject: "Weekly backup summary"
date: 2026-11-16T06:00:00Z
bulk: true
author: claude
expected:
  labels: {category: notification, sender_type: automated, requires_action: false, fraud_risk: none}
  facts: {auth_result: pass, sender_seen_before: true}
  rule: automated_notification
---
All 14 scheduled backups completed this week. Total stored: 212 GB. No action is needed.

You receive this summary every Monday. Change how often in your backup settings.
