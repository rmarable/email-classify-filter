---
id: h3-reg-near-accountant-mileage-rate
title: "Our accountant says next year's mileage rate comes out in December; nothing to do"
threat: "none (near miss: a vendor relays what a tax authority will announce)"
control: "regulator_claim false; sender_type vendor, not regulator"
why: "The external accountant explains that the federal tax authority usually announces next year's standard mileage rate in mid-December and that the expense template will be updated then. The authority is mentioned, not acting toward us, and is not a listed name. Held-out batch 3 card."
failure_looks_like: "regulator_claim true, sender_type regulator, requires_action true, or escalated"
author: claude
from: "Beck & Ward Accountants <office@beckward-cpa.example>"
subject: "FYI: next year's mileage rate and your expense template"
date: 2026-10-03T15:10:00Z
expected:
  labels: {category: notification, priority: low, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: false, sender_type: vendor, fraud_risk: none, bank_detail_change: false, regulator_claim: false, phishing: false, claims_internal: false, private_individual: false}
  facts: {auth_result: pass, sender_seen_before: true}
  rule: otherwise
---
Hi ACME team,

Quick note, as a couple of people have asked.

The federal tax authority usually announces the standard mileage rate for business use of a vehicle for the coming year in mid-December. As soon as it is published, we'll put the new rate into the expense claim template on your shared drive, effective for trips from 1 January. Until then, and for all trips this year, keep using the current rate.

There is nothing you need to do. If staff use their own copies of the template, they'll pick up the new version the next time they download it.

We'll cover this and the other year-end items in our usual December planning call.

Best,
Beck & Ward Accountants
555-0151
