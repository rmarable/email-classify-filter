---
id: long-010
title: "Long card long-010"
threat: "cleaner-card-phone: scam ask early (control)"
control: "the classifier's fraud_risk on the body alone (rules alone don't reach fraud_guard)"
why: "window tuning step 4 (plan draft 8)"
failure_looks_like: "fraud_risk none, not escalated"
review: "a long email whose scam ask sits early; confirm the labels fit the whole email"
author: claude
profile: org
from: "Brightway Cleaning Services <office@brightway-cleaning.example>"
subject: "Brightway: notes for the quarter"
date: 2026-10-19T13:47:00Z
signal: "read out the full card number and expiry"
target_chars: 4300
pad_voice: vendor
signature: |
  Brightway Cleaning Services
quoted_thread:
  - from: "\"Pat Lee <ap@acme.example>\""
    date: 2026-10-15T10:00:00Z
    body: "We have reviewed the feedback from the site visit. Most of the points were minor and have already been handled, and the remaining two are on the list for the next maintenance window.\n\nWe tested the new labels in cold storage and they stay readable at low temperatures. That was a concern raised at the last review, so it is good to have the result on record.\n\nYour new contact on our sales team will introduce themselves next week. They have worked with similar customers for several years and will keep the same regular check-in that you have now.\n\nOur recycling partner collects used pallets each week. If you have any spare pallets from earlier deliveries, the driver can take them back on the next visit at no extra charge."
  - from: "\"Brightway Cleaning Services <office@brightway-cleaning.example>\""
    date: 2026-10-14T10:00:00Z
    body: "Our van fleet is being replaced over the next few months. The new vans are quieter and cleaner, and the drivers will keep their usual routes during the changeover.\n\nStock levels for the standard items look healthy for the season. The seasonal range is selling a little faster than planned, so we have brought forward the next production run.\n\nThank you for the kind note about the driver who helped unload during the storm. We have passed your thanks on, and he was pleased to hear that the delivery made a difference.\n\nThe new storage racks in our main warehouse are now in use. They give us room for about a fifth more stock, which should shorten the wait on the items that sold out last winter."
  - from: "\"Pat Lee <ap@acme.example>\""
    date: 2026-10-13T10:00:00Z
    body: "A quick note on the shipping labels: the new format puts the order reference above the address block. Older labels are still accepted, but please switch when you next reprint a batch.\n\nThe returns process is unchanged: items come back in their original packaging with the returns slip, and the warehouse checks them within a few working days of arrival.\n\nJust a reminder that the annual stock count takes place at the end of the month. Deliveries can still be received that week, but collections may be slower than usual.\n\nOur purchasing team visited the timber yard that supplies the frames. The yard follows the forestry standards we agreed on, and the visit report is filed with our quality records."
expected:
  labels: {category: invoice, priority: high, requires_action: true, requires_reply: false, payment_related: true, deadline_mentioned: true, sender_type: vendor, fraud_risk: high, phishing: true, private_individual: false}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
---
Hello, here are our notes for the quarter from the Brightway office.

To keep your contract running past Friday, please have whoever holds the office card call us on 555-0124 and read out the full card number and expiry. Our usual contact for your account is away, so please don't wait for her.

{{pad}}

Best wishes from the Brightway team.
