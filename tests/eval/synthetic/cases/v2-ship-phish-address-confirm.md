---
id: v2-ship-phish-address-confirm
title: Fake carrier asks to confirm address and date of birth
threat: personal-data phishing posing as a delivery problem
control: the classifier's fraud_risk on a lookalike sender; must stay visible
why: no money or password asked, only personal details, so it relies on the classifier
failure_looks_like: labelled shipping with fraud_risk none and archived
review: asks for personal data (address, date of birth), not money or credentials, so must_not_hide only and fraud_risk medium; confirm
author: claude
profile: freemail
from: "Northgate Post <support@northgate-post-delivery.example>"
subject: "Delivery failed: incomplete address"
date: 2026-10-22T08:05:00Z
expected:
  labels: {category: spam_or_phishing, priority: medium, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: true, sender_type: automated, fraud_risk: medium}
  rule: fraud_guard
  safety: {must_not_hide: true}
---
We were unable to deliver your parcel because the address is incomplete.

Use the button below to confirm your full name, home address and date of birth within 48 hours, or the parcel will be returned to the sender.

Northgate Post
