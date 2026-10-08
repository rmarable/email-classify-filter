---
id: v2-acct-phish-unusual-purchase
title: Fake security alert about a purchase asks for card details to cancel it
threat: payment-card phishing dressed as a security warning
control: the classifier's fraud_risk and payment wording; must escalate
why: mixes a security alert with a payment-detail request
failure_looks_like: labelled account_security or billing_inquiry with low fraud risk
author: claude
profile: freemail
from: "Lantern Outfitters Security <security-check@lantern-orders.example>"
subject: "Unusual purchase on your account"
date: 2026-10-17T10:10:00Z
expected:
  labels: {category: spam_or_phishing, priority: high, requires_action: false, requires_reply: true, payment_related: true, deadline_mentioned: true, sender_type: company, fraud_risk: high}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
---
Hello,

A purchase of 349.99 was just placed on your Lantern Outfitters account from a new device.

If you did not make it, reply today with your full name, card number, expiry date and the 3-digit security code so we can cancel the order and refund you.

Lantern Outfitters Account Protection
