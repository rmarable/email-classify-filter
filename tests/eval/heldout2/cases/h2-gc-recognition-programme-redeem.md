---
id: h2-gc-recognition-programme-redeem
title: Genuine employee-recognition programme explains how to redeem points for cards
threat: "none (benign look-alike: rewards and gift-card redemption wording)"
control: false-positive control for gift-card and rewards wording
why: >-
  Acme's recognition platform tells an employee they received points and explains how to
  redeem them for retailer cards inside the platform. It asks for no money, codes or
  credentials. Authentication set to pass with sender_seen_before true: genuine mail from the
  platform's own domain, which sends these regularly.
  Held-out batch 2 card; written without the plan, keyword lists or signals.
failure_looks_like: a genuine rewards notice is escalated or labelled phishing
review: >-
  no expected rule given; compute it with ecf rules test. Category notification vs
  payment_confirmation/remittance is a judgement call (points, not money, are coming in).
author: claude
from: "Kudos Hub for ACME <notifications@vendor-e.example>"
to: [ap@acme.example]
subject: "You've been recognised! 500 points from your teammates"
date: 2026-10-03T13:00:00Z
bulk: true
expected:
  labels: {category: notification, priority: low, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: false, sender_type: vendor, fraud_risk: none}
  facts: {auth_result: pass, sender_seen_before: true}
---
Hi AP Team,

Nice work! Two colleagues recognised you this week:

  "Thanks for turning around the September close so quickly." - 300 points
  "Huge help sorting out the vendor onboarding backlog." - 200 points

Your balance is now 1,250 points.

How to use your points
  - Sign in to Kudos Hub with your usual ACME single sign-on (from the Kudos tile on the intranet home page).
  - Open Rewards and choose a retailer card, charity donation or ACME merchandise. 1,000 points = $10.
  - Digital cards are delivered to your Kudos Hub wallet, not by email. You can view or print them any time from there.

A few reminders: points never expire while you work at ACME, and rewards over $100 per quarter may appear on your payslip as a taxable benefit. Kudos Hub and People & Culture will never ask you to buy cards or send codes by email.

Questions? Visit the Help section in Kudos Hub or contact People & Culture.

Kudos Hub, the recognition programme for ACME
You receive this email because you are enrolled in ACME's recognition programme. Manage notification settings in your Kudos Hub profile.
