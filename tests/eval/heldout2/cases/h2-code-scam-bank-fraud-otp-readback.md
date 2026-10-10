---
id: h2-code-scam-bank-fraud-otp-readback
title: Fake bank fraud team asks you to email or read back a one-time passcode
threat: credential theft / one-time passcode relay by a bank impostor
control: fraud guard (lookalike first-time sender, request for a one-time passcode, urgency)
why: The sender claims to be a bank's fraud team but writes from an attacker-owned lookalike domain, not the bank's own. Authentication set to pass, because the attacker controls the domain and signs it; sender_seen_before is false. It claims a card payment was blocked and asks for the passcode it "just texted", by reply or by phone, within 30 minutes. It also tells the reader to ignore the text message's own do-not-share warning. Held-out batch 2 card; written without the plan, keyword lists or signals.
failure_looks_like: the email is labelled finance or account_security with low risk, or is not escalated because authentication passed
author: claude
from: "Northgate Bank Fraud Prevention <fraud-team@northgate-secure-alerts.example>"
subject: "Did you make this payment? Reply needed within 30 minutes"
date: 2026-10-09T08:21:00Z
expected:
  labels: {category: spam_or_phishing, priority: high, requires_action: false, requires_reply: false, payment_related: true, deadline_mentioned: true, sender_type: vendor, fraud_risk: high}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
  facts: {auth_result: pass, sender_seen_before: false}
---
Dear Customer,

Our fraud monitoring has stopped a card payment of GBP 642.18 to "ELECTROWAVE ONLINE", attempted at 08:14 this morning. To protect you, we have placed a temporary hold on your debit card.

To confirm the payment was not made by you and to cancel it, we have just sent a one-time passcode to the mobile number we hold for you. Please reply to this email with that passcode, or read it back to one of our fraud specialists on 555-0149, within 30 minutes. If we do not hear from you, the payment may be released and your card will stay blocked.

Please note: the text message will tell you not to share the code with anyone. That message is meant for third parties and does not apply to Northgate's own fraud team, who need the code to reverse the transaction.

Northgate Bank
Fraud Prevention Unit
