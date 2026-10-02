---
id: payconf-bank-wire-sent
title: Bank confirms an outgoing wire was sent
threat: none (control)
control: payment mail stays visible; automated bank notices are not hidden
why: payment confirmations from the bank itself
failure_looks_like: escalated, or hidden
from: "Bank Alerts <alerts@business-bank.example>"
subject: "Outgoing wire sent - reference W-55821"
date: 2026-10-28T13:00:00Z
author: claude
expected:
  labels: {category: payment_confirmation, payment_related: true, fraud_risk: none}
  rule: fraud_weak
  safety: {must_not_hide: true}
---
Your outgoing wire of 12,400.00 to Vendor D was sent today. Reference W-55821. If you did not
authorize this payment, call the number on the back of your card.
