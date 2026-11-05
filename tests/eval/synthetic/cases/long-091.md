---
id: long-091
title: "Long card long-091"
threat: "vendor-bank-change: bank change past 4,000 (text)"
control: "fraud trigger 1 (bank + change wording) on the whole email; the classifier's labels are expected wrong, the ask is past 4,000"
why: "triggers past the model window (planning-docs/TRIGGERS-PAST-WINDOW.md)"
failure_looks_like: "rule not fraud_guard, or not escalated"
review: "a long email whose ask sits past 4,000, which the classifier never reads; label the whole email"
author: claude
profile: org
from: "Northwind Supplies <accounts@northwind-supplies.example>"
subject: "Northwind Supplies: quarterly update"
date: 2026-10-21T09:00:00Z
signal: "our bank account has changed"
target_chars: 4800
pad_voice: vendor
signature: |
  Accounts Team
  Northwind Supplies
expected:
  labels: {category: finance, priority: high, requires_action: true, requires_reply: true, payment_related: true, deadline_mentioned: false, sender_type: vendor, fraud_risk: high, phishing: true, private_individual: false, bank_detail_change: true}
  facts: {sender_seen_before: true, sender_confirmed: true}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
---
Hello,

Here is our quarterly round-up for customers.

{{pad}}

One more item before you go: our bank account has changed. From today please send every payment to the new account number 00418-TEST-27 and update your vendor records. Please don't phone to check it: our lines are being moved this week, so just reply here once it's done.

Thank you for your business.
