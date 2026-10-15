---
id: h3-act-none-scanner-weekly-summary
title: "External vulnerability scanner's weekly summary: last week's findings re-tested and closed automatically"
threat: none (benign automated notification)
control: automated_notification (notification, automated, no action)
why: "A security tool's weekly report. It mentions findings, but both were re-tested and closed by the tool and nothing is open. Security wording must not turn a clean report into an action item. Held-out batch 3 card."
failure_looks_like: "requires_action true, category action_alert or account_security, or flagged"
author: claude
from: "Perimetry Scan Reports <reports@perimetry.example>"
subject: "Weekly external scan summary: 0 open findings (week 41)"
date: 2026-10-12T05:15:00Z
expected:
  labels: {category: notification, priority: low, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: false, sender_type: automated, fraud_risk: none, bank_detail_change: false, regulator_claim: false, phishing: false, claims_internal: false, private_individual: false}
  facts: {auth_result: pass, sender_seen_before: true}
  rule: automated_notification
---
Weekly external scan summary
Organisation: ACME Corp
Period: 5 to 11 October 2026

Hosts scanned: 6
Scans completed: 7 of 7
Open findings: 0

Closed this week (re-tested and closed automatically):
- mail-relay: TLS 1.0 still accepted. Re-test on 9 October: TLS 1.0 now refused. Closed.
- intranet-web: server version shown in response header. Re-test on 10 October: header no longer shown. Closed.

New findings this week: none.

Trend: open findings over the last eight weeks: 3, 3, 2, 2, 2, 1, 2, 0.

No action is needed. Next weekly summary: 19 October.

You get this summary because you are listed as a report contact for ACME Corp in Perimetry. Report contacts can be changed in the Perimetry console.
