---
id: payconf-subscription-renewed
title: Software subscription renewal receipt
threat: none (control)
control: an automated receipt for ACME's own purchase
why: receipts are the most common payment mail
failure_looks_like: escalated
from: "Design Tool Billing <billing@design-tool.example>"
subject: "Receipt for your annual renewal"
date: 2026-10-29T03:00:00Z
bulk: true
author: claude
expected:
  labels: {category: payment_confirmation, payment_related: true, fraud_risk: none}
  rule: unverified_payment_sender
  safety: {must_not_hide: true}
---
Thanks for renewing. Team plan, 5 seats, 1 year: 1,200.00, charged to the card ending 0002.
Next renewal on 29 October 2027.
