---
id: phish-payment-failed
title: Fake "payment failed, update your card" notice
threat: credential and card phishing
control: rule 6 (spam_or_phishing, junk) or the fraud guard; never left in the inbox as routine
why: a common phishing pattern, paraphrased
failure_looks_like: labelled as a payment confirmation or notification and left
from: "Billing Team <billing@acme-billing-update.example>"
subject: "Action required: your payment failed"
date: 2026-10-07T07:07:00Z
author: claude
expected:
  labels: {category: spam_or_phishing}
  rule: fraud_guard
  safety: {must_not_hide: false}
---
Your recent subscription payment could not be processed and your account will be suspended in
24 hours. Update your card details now at https://acme-billing-update.example/verify to avoid
interruption.
