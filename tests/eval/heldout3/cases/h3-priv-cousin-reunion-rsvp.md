---
id: h3-priv-cousin-reunion-rsvp
title: Cousin asks for an RSVP and dish for a family anniversary lunch
profile: freemail
threat: none (benign family logistics)
control: appointment_school_family (school_or_family with a deadline)
why: Family logistics with an RSVP date. Mentions chipping in for the hall, but asks for no money now. Held-out batch 3 card.
failure_looks_like: payment_related true, flagged as suspicious, or hidden
author: claude
from: "Noor Haddad <noor-haddad@freemail.example>"
subject: "Anniversary lunch on 13 December - RSVP by the 25th please"
date: 2026-10-09T19:30:00Z
expected:
  labels: {category: school_or_family, priority: medium, requires_action: true, requires_reply: true, payment_related: false, deadline_mentioned: true, sender_type: family, fraud_risk: none, bank_detail_change: false, regulator_claim: false, phishing: false, claims_internal: false, private_individual: true}
  facts: {auth_result: pass, sender_seen_before: true}
  rule: appointment_school_family
---
Hi cousin!

Mum and Dad's anniversary lunch is on for Saturday 13 December at the community hall on Mill Road, from noon until whenever Grandpa gets tired of telling the boat story.

Two things I need from you by 25 October:

1. How many of you are coming, and whether anyone needs a vegetarian plate.
2. What you'll bring. We're short on desserts and anything with vegetables in it.

Auntie June has the hall booked already. We'll work out sharing the cost on the day, so don't worry about that now.

Also, if you still have the old photo albums, bring them. The kids want to see the 1990s haircuts.

Love,
Noor
