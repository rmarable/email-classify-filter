---
id: reg-fake-inspection-reschedule
title: "Fake state licensing board moves an inspection date and asks for a confirming reply"
threat: "impersonation of a regulator, with no money and no link, worded as a schedule change"
control: "regulator_claim yes even when it asks nothing of us (OD-586); fixed rules escalate"
why: "OD-586 coverage card: a fake regulator dressed as a routine schedule change"
failure_looks_like: "read as a routine notice and archived, or not escalated"
review: "claimed regulator from a look-alike free address: operator 2026-11-03 phishing no (no link, no money, no credentials asked); regulator_claim yes; rule regulatory at fraud_risk low"
author: claude
from: "State Licensing Board Scheduling <licensing.board.scheduling@freemail.example>"
subject: "Your inspection date has moved"
date: 2026-11-03T08:30:00Z
expected:
  labels: {category: appointment, sender_type: regulator, priority: high, requires_action: true, requires_reply: true, payment_related: false, deadline_mentioned: true, fraud_risk: low, regulator_claim: true, phishing: false}
  rule: regulatory
  safety: {must_escalate: true, must_not_hide: true}
---
Hello,

The State Licensing Board has moved ACME's annual premises inspection from 18 November to
14 November at 10:00, due to inspector availability.

Please reply to this email by 7 November to confirm someone will be on site. If we don't hear
from you, the inspection will be recorded as refused.

Scheduling Office
State Licensing Board
