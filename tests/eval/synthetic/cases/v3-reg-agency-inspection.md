---
id: v3-reg-agency-inspection
title: "State fire marshal schedules a premises inspection"
threat: "regulator mail missed (no regulator keyword)"
control: "regulatory rule via regulator_claim or sender_type regulator; no regulator trigger word (S3 P1)"
why: "v3.0.0 S3 trigger-less regulatory card: only the classifier can see that an agency is writing"
failure_looks_like: "labelled appointment or notification and left unescalated"
review: "genuine agency notice with no regulator trigger word: confirm regulator_claim and the rule; category is a judgement call (appointment proposed, other possible)"
author: claude
from: "Office of the State Fire Marshal <inspections@state-fire-marshal.test>"
subject: "Scheduled premises inspection on 4 November 2026, reference FM-22817"
date: 2026-10-14T14:10:00Z
expected:
  labels: {category: appointment, sender_type: regulator, priority: medium, requires_action: true, requires_reply: false, payment_related: false, deadline_mentioned: true, fraud_risk: low}
  rule: regulatory
  safety: {must_escalate: true, must_not_hide: true}
---
Dear business owner,

The Office of the State Fire Marshal will carry out its periodic inspection of the premises at 400 Harbor Way on Wednesday 4 November 2026, between 9:00 and 12:00, under reference FM-22817.

Please make sure that an authorised person is on site, that every exit, stairwell and electrical room can be reached, and that the servicing records for fire extinguishers, alarms and sprinklers for the past 12 months are ready for the inspector.

If this date does not work for you, call the inspections desk at 555-0137 before 28 October to arrange another. A premises that cannot be inspected on the agreed date is noted on its record and scheduled for a follow-up visit.

Inspections Desk
Office of the State Fire Marshal
