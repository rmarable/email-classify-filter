---
id: hide-genuine-photo-sync
title: Photo library sync report to a personal account from a known bulk sender
threat: none (hide-path control, personal profile)
control: rule 8 archives an authenticated bulk notice from a sender seen before (bulk_corroborates)
why: the hide path on a personal mailbox, where most such notices arrive
failure_looks_like: left in the inbox, or flagged
profile: freemail
from: "Photo Cloud <no-reply@photo-cloud.example>"
subject: "Your library is up to date"
date: 2026-11-16T07:30:00Z
bulk: true
author: claude
expected:
  labels: {category: notification, sender_type: automated, requires_action: false, fraud_risk: none}
  facts: {auth_result: pass, sender_seen_before: true}
  rule: automated_notification
---
Your photo library finished syncing on all 3 devices. 48 new photos were added this month.

This is an automatic message. You can turn off sync reports in Settings.
