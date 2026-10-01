---
id: fraud-w2-request
title: Message signed as the CEO asks HR for every employee's tax form
threat: data theft by executive impersonation (no payment)
control: rule 1 via staff-from-outside or the model's fraud risk; no payment keywords at all
why: data theft carries no money words for the triggers to see
failure_looks_like: no escalation
from: "ACME CEO <chief-exec@freemail.example>"
subject: "Need W-2s"
date: 2026-10-17T09:15:00Z
author: claude
expected:
  labels: {category: spam_or_phishing, sender_type: staff, fraud_risk: high}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
---
Please send me the W-2 forms for all employees as one PDF today. I need them for a meeting with
the auditors. Send them to this address, my office email is having problems.
