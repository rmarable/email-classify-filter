---
id: long-052
title: "Long card long-052"
threat: "price-list-update: benign long mail"
control: "nothing to flag in a long email"
why: "window tuning step 4 (plan draft 8)"
failure_looks_like: "flagged as fraud"
review: "a long email with nothing to flag; confirm the labels fit the whole email"
author: claude
profile: org
from: "Vendor D Trade Desk <trade@vendor-d.example>"
subject: "Vendor D: updated trade price list"
date: 2026-10-19T11:38:00Z
target_chars: 4225
pad_voice: vendor
signature: |
  Trade Desk
  Vendor D
quoted_thread:
  - from: "\"Pat Lee <ap@acme.example>\""
    date: 2026-10-15T10:00:00Z
    body: "Next quarter we will review the range of smaller items. A few slow sellers may be retired, and we will give plenty of notice before anything you order regularly is changed.\n\nThanks again for the update on the spring order. The warehouse confirmed that the pallets arrived in good condition and the count matched the packing list, so nothing further is needed on our side for that delivery.\n\nOur office will be closed on the public holiday next month. Orders placed during the closure will be picked and packed on the first working day after it, in the order they came in.\n\nWe have reviewed the feedback from the site visit. Most of the points were minor and have already been handled, and the remaining two are on the list for the next maintenance window."
  - from: "\"Vendor D Trade Desk <trade@vendor-d.example>\""
    date: 2026-10-14T10:00:00Z
    body: "Stock levels for the standard items look healthy for the season. The seasonal range is selling a little faster than planned, so we have brought forward the next production run.\n\nJust a reminder that the annual stock count takes place at the end of the month. Deliveries can still be received that week, but collections may be slower than usual.\n\nThe engineering team completed the routine checks on the packing line. No faults were found, and the line is running at its normal speed again after the short planned stop.\n\nWe noticed the delivery address on your last few orders has a different unit number. The driver found you without trouble, but it would help to have the full address on the order."
  - from: "\"Pat Lee <ap@acme.example>\""
    date: 2026-10-13T10:00:00Z
    body: "Some customers have asked whether the outer cartons can be recycled. They can, and from next month the cartons will carry a small printed note saying so on the side panel.\n\nThanks for taking part in the customer survey. The most common request was clearer delivery windows, and we are working with the carriers on a narrower time slot for each drop.\n\nWe have updated the measurements in the online catalogue for the larger shelving units. The products themselves have not changed; the earlier figures were rounded too far.\n\nWe have restocked the spare parts that were short in the autumn. Hinges, brackets and the small fixing kits are all back on the shelves and can be added to your next order."
expected:
  labels: {category: notification, priority: low, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: false, sender_type: vendor, fraud_risk: none, phishing: false, private_individual: false}
  rule: otherwise
  safety: {}
---
Hello, our updated trade price list takes effect at the start of next month.

{{pad}}

The new list is attached to your trade profile; nothing is needed from you.
