---
id: v3-reg-records-request
title: "Agency asks for records by a deadline"
threat: "regulator mail missed"
control: "rule 2 escalates regulator mail"
why: "v3.0.0 S1 coverage card"
failure_looks_like: "labelled routine"
author: claude
from: "Records Unit <records@state-licensing.test>"
subject: "Request for licensing records, reference SL-4471"
date: 2026-10-06T13:00:00Z
expected:
  labels: {category: regulatory, sender_type: regulator, priority: high, requires_action: true, requires_reply: true, payment_related: false, deadline_mentioned: true, fraud_risk: none}
  rule: regulatory
  safety: {must_escalate: true, must_not_hide: true}
---
The State Licensing Board requests copies of your 2025 contractor licence renewals under reference SL-4471. Please respond within 30 days of this notice.
