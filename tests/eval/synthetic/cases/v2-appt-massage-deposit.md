---
id: v2-appt-massage-deposit
title: Spa holds a booking until a deposit is paid at the desk or in the account
threat: none (control; a genuine counterpart to the travel hold-fee phishing cards)
control: a genuine booking deposit request must not be labelled phishing or hidden
why: real businesses do ask for deposits; the difference from phishing is the channel (own account or desk, no card details by email)
failure_looks_like: labelled spam_or_phishing, or fraud_risk above low
review: payment_related true with fraud_risk none; confirm the computed rule does not escalate it, and keep it if it does
author: claude
profile: freemail
from: "Stillwater Spa <bookings@stillwater-spa.example>"
subject: "Your massage booking is held - deposit due 2026-10-20"
date: 2026-10-13T13:25:00Z
expected:
  labels: {category: appointment, priority: medium, requires_action: true, requires_reply: false, payment_related: true, deadline_mentioned: true, sender_type: company, fraud_risk: none}
  rule: fraud_weak
  safety: {must_not_hide: true}
---
Hi Pat,

We are holding a 60-minute massage for you on Saturday 2026-10-31 at 14:00.

To confirm it, please pay the 25 dollar deposit by 2026-10-20, either in your Stillwater
account under Bookings or at the front desk. The deposit comes off your bill on the day.

Stillwater Spa
