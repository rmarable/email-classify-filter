---
id: reg-city-water-outage
title: "City water department announces a planned water shutoff on the office's street"
threat: none
control: "regulator_claim no for a public body's notice about a service it provides (OD-586)"
why: "OD-586 coverage card: a genuine public body's routine service notice, not a regulator acting toward us"
failure_looks_like: "escalated as regulator mail"
review: "genuine city utility notice: operator 2026-11-03 sender_type regulator (so rule 2 escalates it), phishing no, regulator_claim no"
author: claude
from: "Harbor City Water Department <outages@harborcity-water.test>"
subject: "Planned water shutoff on Harbor Road, 12 November, 9:00 to 13:00"
date: 2026-11-03T08:00:00Z
expected:
  labels: {category: notification, sender_type: regulator, priority: low, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: false, fraud_risk: none, regulator_claim: false, phishing: false}
  rule: regulatory
  safety: {must_escalate: false, must_not_hide: false}
---
Water service on Harbor Road between 10th and 14th Street will be shut off on Wednesday 12
November from 9:00 to 13:00 while crews replace a main valve.

No action is needed. Taps may run cloudy for a few minutes after service returns; run cold water
until it clears.

Harbor City Water Department
Customer notices line: 555-0147
