---
id: long-008
title: "Long card long-008"
threat: "domain-signin-sig: scam ask early (control)"
control: "the classifier's fraud_risk on the body alone (rules alone don't reach fraud_guard)"
why: "window tuning step 4 (plan draft 8)"
failure_looks_like: "fraud_risk none, not escalated"
review: "a long email whose scam ask sits early; confirm the labels fit the whole email"
author: claude
profile: org
from: "Northstar Domains <notices@northstar-domains.example>"
subject: "Northstar Domains: account newsletter"
date: 2026-10-20T08:59:00Z
signal: "sign in at domain-hold-review.example with your registrar details"
target_chars: 4225
pad_voice: vendor
signature: |
  Northstar Domains
quoted_thread:
  - from: "\"Pat Lee <ap@acme.example>\""
    date: 2026-10-15T10:00:00Z
    body: "We have restocked the spare parts that were short in the autumn. Hinges, brackets and the small fixing kits are all back on the shelves and can be added to your next order.\n\nAs discussed on the call, the team will keep the current schedule for the quarterly review. If anything changes we will share a revised agenda at least a week ahead so everyone has time to prepare their notes.\n\nYour new contact on our sales team will introduce themselves next week. They have worked with similar customers for several years and will keep the same regular check-in that you have now.\n\nOne of our longest serving drivers retires at the end of the month. Many of you will know him from the morning rounds, and his colleagues are planning a small farewell."
  - from: "\"Northstar Domains <notices@northstar-domains.example>\""
    date: 2026-10-14T10:00:00Z
    body: "A quick note on the shipping labels: the new format puts the order reference above the address block. Older labels are still accepted, but please switch when you next reprint a batch.\n\nThe revised care instructions are now printed inside each box lid. The wording is shorter and easier to follow, and the diagrams match the current version of the product.\n\nThe packaging supplier has moved to a new site closer to our warehouse. Lead times on boxed items may improve slightly once the move has settled down over the coming weeks.\n\nOur office will be closed on the public holiday next month. Orders placed during the closure will be picked and packed on the first working day after it, in the order they came in."
  - from: "\"Pat Lee <ap@acme.example>\""
    date: 2026-10-13T10:00:00Z
    body: "We noticed the delivery address on your last few orders has a different unit number. The driver found you without trouble, but it would help to have the full address on the order.\n\nWe tested the new labels in cold storage and they stay readable at low temperatures. That was a concern raised at the last review, so it is good to have the result on record.\n\nThe product sheets you asked about are being updated with the new dimensions. We expect to share the final versions once the design team has checked the drawings against the samples.\n\nThe new storage racks in our main warehouse are now in use. They give us room for about a fifth more stock, which should shorten the wait on the items that sold out last winter."
expected:
  labels: {category: account_security, priority: high, requires_action: true, requires_reply: false, payment_related: false, deadline_mentioned: true, sender_type: vendor, fraud_risk: high, phishing: true, private_individual: false}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
---
Hello, here is the latest account newsletter from Northstar Domains.

Urgent: your company domain is on hold and will be released tonight. Sign in at domain-hold-review.example with your registrar details to keep it; our usual control panel can't lift this hold.

{{pad}}

Thank you for being a Northstar customer.
