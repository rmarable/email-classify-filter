---
id: mid-reply-to-differs
title: Newsletter whose Reply-To points to another domain
threat: low (Reply-To mismatch on non-payment mail)
control: a lone Reply-To mismatch without payment words is not a trigger
why: mailing services routinely use other reply domains
failure_looks_like: escalated as fraud
from: "Industry Digest <digest@support-weekly.example>"
reply_to: "editor@digest-replies.example"
subject: "This week in customer support"
date: 2026-11-11T07:00:00Z
bulk: true
review: Fraud risk low? Preview run - fraud_risk expected low, model none.
author: claude
expected:
  labels: {category: marketing, fraud_risk: low}
  rule: marketing
---
This week: hiring trends for support agents, three tools reviewed, and a reader question on
weekend coverage.

Industry Digest
