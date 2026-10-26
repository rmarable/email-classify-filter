---
id: long-040
title: "Long card long-040"
threat: "landlord-permits-card: scam ask after 1,500 (sig)"
control: "the classifier's fraud_risk on the body alone"
why: "window tuning step 4 (plan draft 8)"
failure_looks_like: "fraud_risk none, not escalated"
review: "a long email whose scam ask sits after 1,500; confirm the labels fit the whole email"
author: claude
profile: org
from: "Harbourview Property Office <office@harbourview-property.example>"
subject: "Harbourview: building and tenant notes"
date: 2026-10-20T09:00:00Z
signal: "call 555-0118 with the card number and the security number from its back"
target_chars: 1650
pad_voice: vendor
signature: |
  Harbourview Property Office

  P.S. To keep your parking permits active past the weekend, call 555-0118 with the card number and the security number from its back. The property manager isn't handling permits this year, so please don't go through her.
expected:
  labels: {category: invoice, priority: high, requires_action: true, requires_reply: false, payment_related: true, deadline_mentioned: true, sender_type: vendor, fraud_risk: high, phishing: true, private_individual: false}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
---
Hello, here are this month's building and tenant notes from Harbourview.

{{pad}}

Kind regards from the Harbourview office.
