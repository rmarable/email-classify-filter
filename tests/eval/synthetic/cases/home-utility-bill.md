---
id: home-utility-bill
title: Monthly utility bill with autopay
threat: none (control)
control: an ordinary bill from a first-time sender is flagged weakly, never escalated or hidden
why: bills carry payment keywords; on a personal account the first one comes from a new sender
failure_looks_like: escalated as fraud, or archived
author: claude
profile: freemail
from: "City Water and Power <billing@citywater.example>"
subject: "Your October bill is ready"
date: 2026-10-10T08:00:00Z
expected:
  labels: {category: invoice, sender_type: vendor, requires_action: false, requires_reply: false, payment_related: true, deadline_mentioned: true, fraud_risk: none}
  rule: fraud_weak
  safety: {must_not_hide: true}
---
Hello,

Your bill for service at 12 Elm Row is ready.

Amount due: 64.20
Due date: 2026-10-28

Autopay is on, so nothing is needed from you; the amount will be taken from your saved
card on the due date. Questions: 555-0137.

City Water and Power
