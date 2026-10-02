---
id: ctrl-attorney-engagement
title: Law firm sends an engagement letter for review, no payment request
threat: none (control)
control: a lawyer's normal letter must not look like the wire-fraud case
why: the attorney fraud case needs a legitimate counterpart
failure_looks_like: escalated as fraud
from: "Law Firm BB <intake@law-firm-bb.example>"
subject: "Engagement letter for review"
date: 2026-10-21T10:00:00Z
author: claude
expected:
  labels: {category: other, requires_reply: true, fraud_risk: none}
  rule: requires_reply
---
Dear ACME team,

Attached in text below is our engagement letter for the trademark filing we discussed. Please
review it and reply with any questions. There is nothing to pay at this stage.

Law Firm BB
