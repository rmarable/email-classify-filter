---
id: home-shipping-notice
title: Carrier says a package has shipped
threat: none (control)
control: a plain shipping notice may be archived as an automated notification; no fraud signal
why: the delivery-scam cards need a legitimate counterpart without links or fees
failure_looks_like: escalated as a delivery scam
author: claude
profile: freemail
from: "Parcel Co Updates <tracking@parcel-co.example>"
subject: "Your package is on its way"
date: 2026-10-14T11:00:00Z
expected:
  labels: {category: notification, sender_type: automated, requires_action: false, requires_reply: false, payment_related: false, fraud_risk: none}
  rule: automated_notification
---
Your package from Corner Books has shipped.

Tracking number 7710 2245 0091
Expected delivery: Friday 2026-10-16, before 6 pm.

No signature is needed. You can follow the delivery in the Parcel Co app.
