---
id: h3-int-near-contractor-migration-done
title: "Outside IT contractor reports the shared-drive move our teams planned together is done"
threat: "none (near miss: an outside contractor reports on joint work in 'we' and 'our team' terms)"
control: "claims_internal false for an outside contractor; otherwise rule (no action, not automated)"
why: "Our IT contractor reports that the shared-drive move it planned with us finished. It writes as 'our team' alongside 'your office team' but never claims to be ACME staff, asks for nothing, and mentions no credentials. Held-out batch 3 card."
failure_looks_like: "claims_internal true, requires_action true, or given fraud risk"
author: claude
from: "Tallis Brook IT <team@tallisbrook-it.example>"
subject: "Shared-drive move finished on Saturday"
date: 2026-10-12T09:30:00Z
expected:
  labels: {category: notification, priority: low, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: false, sender_type: vendor, fraud_risk: none, bank_detail_change: false, regulator_claim: false, phishing: false, claims_internal: false, private_individual: false}
  facts: {auth_result: pass, sender_seen_before: true}
  rule: otherwise
---
Hi ACME,

Quick update from our team: the shared-drive move we planned together finished on Saturday night, as scheduled.

Every folder is where it was, permissions carried over, and the shortcuts your people saved still open the same files. We checked a sample of 200 files against the old drive and all of them matched.

The old drive stays available read-only as a fallback until we agree together to retire it. You don't need to do anything.

We'll go over the details at our usual Thursday check-in. Thanks to your office team for the help with testing last week.

Best,
Tallis Brook IT
555-0147
