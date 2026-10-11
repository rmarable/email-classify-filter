---
id: h2-gc-utility-disconnect-appstore
title: Fake utility disconnection demands payment in app-store cards
threat: utility impersonation / overdue bill paid with prepaid app-store cards
control: fraud triggers (gift-card payment demand, urgency, lookalike sender domain)
why: >-
  A lookalike of the building's power utility says service will be cut off this afternoon
  unless an overdue balance is paid with app-store or retail prepaid cards read out over the
  phone. Authentication set to pass: the sender domain is attacker-registered (lookalike of
  citypower.example) and signs its own mail; it is a first-time sender.
  Held-out batch 2 card; written without the plan, keyword lists or signals.
failure_looks_like: the email is filed as a normal invoice or overdue reminder and not escalated
review: expected rule fraud_guard is assumed, not computed; confirm with ecf rules test
author: claude
from: "CityPower Collections <collections@citypower-accountservices.example>"
to: [ap@acme.example]
subject: "FINAL NOTICE: Service disconnection scheduled today - Acct 7731-0042"
date: 2026-10-07T10:03:00Z
expected:
  labels: {category: spam_or_phishing, priority: high, requires_action: true, requires_reply: false, payment_related: true, deadline_mentioned: true, sender_type: vendor, fraud_risk: high}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
  facts: {auth_result: pass, sender_seen_before: false}
---
ACCOUNT HOLDER: ACME / 400 Commerce Way
ACCOUNT NUMBER: 7731-0042
PAST DUE AMOUNT: $612.40

Our records show two missed payments on this commercial account. A field technician has been dispatched and electric service at the above premises will be disconnected at 4:00pm today unless the balance is settled.

Because the account is in collections status, card and bank payments through the online portal have been suspended. To stop the disconnection you can settle the balance immediately using prepaid payment cards available at most grocery stores, pharmacies and gas stations (app store cards or major retailer cards in any combination adding up to $612.40).

After purchase, call our expedited payments desk at 555-0142 and have the card numbers and PINs ready. You will receive a confirmation number and the dispatch will be cancelled.

A reconnection fee of $250 and a deposit of two months' average usage apply if service is interrupted.

Do not reply to this email. This is the last notice you will receive before disconnection.

CityPower Commercial Collections
