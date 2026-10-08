---
id: v2-ship-phish-unclaimed-parcel
title: Stranger on a freemail address says a parcel is waiting and asks for details
threat: advance-contact scam from an individual on a public mail provider
control: first-time public-provider sender; the classifier's fraud_risk; must stay visible
why: no carrier branding and no link; tests a person-style sender rather than a lookalike domain
failure_looks_like: labelled shipping or private and archived
review: sender_type person (an individual writing, not placeable); must_not_hide only because nothing asks for money or credentials yet; confirm
author: claude
profile: freemail
from: "Robin Ashdale <robin-ashdale-dispatch@freemail.example>"
subject: "Unclaimed package in your name"
date: 2026-10-26T18:50:00Z
expected:
  labels: {category: spam_or_phishing, priority: medium, requires_action: false, requires_reply: true, payment_related: false, deadline_mentioned: false, sender_type: person, fraud_risk: medium}
  rule: fraud_guard
  safety: {must_not_hide: true}
---
Hello,

I work in dispatch and have a package with your name on it that the courier could not deliver.

Reply with your full name, home address and a phone number and I will arrange for it to reach you.

Regards,
Robin
