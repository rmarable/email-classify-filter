---
id: long-057
title: "Long card long-057"
threat: "project-status-thread: benign long mail"
control: "nothing to flag in a long email"
why: "window tuning step 4 (plan draft 8)"
failure_looks_like: "flagged as fraud"
review: "a long email with nothing to flag; confirm the labels fit the whole email"
author: claude
profile: org
from: "Vendor F Projects <projects@vendor-f.example>"
subject: "Re: fit-out project status"
date: 2026-10-19T13:12:00Z
target_chars: 4225
pad_voice: vendor
signature: |
  Projects Team
  Vendor F
quoted_thread:
  - from: "\"Pat Lee <ap@acme.example>\""
    date: 2026-10-15T10:00:00Z
    body: "Our recycling partner collects used pallets each week. If you have any spare pallets from earlier deliveries, the driver can take them back on the next visit at no extra charge.\n\nWe have added two new colours to the catalogue in response to requests from several customers. Samples are available on request, and the full range will be shown at the trade fair.\n\nThe returns process is unchanged: items come back in their original packaging with the returns slip, and the warehouse checks them within a few working days of arrival.\n\nThanks again for the update on the spring order. The warehouse confirmed that the pallets arrived in good condition and the count matched the packing list, so nothing further is needed on our side for that delivery."
  - from: "\"Vendor F Projects <projects@vendor-f.example>\""
    date: 2026-10-14T10:00:00Z
    body: "The engineering team completed the routine checks on the packing line. No faults were found, and the line is running at its normal speed again after the short planned stop.\n\nWe have restocked the spare parts that were short in the autumn. Hinges, brackets and the small fixing kits are all back on the shelves and can be added to your next order.\n\nAs discussed on the call, the team will keep the current schedule for the quarterly review. If anything changes we will share a revised agenda at least a week ahead so everyone has time to prepare their notes.\n\nYour new contact on our sales team will introduce themselves next week. They have worked with similar customers for several years and will keep the same regular check-in that you have now."
  - from: "\"Pat Lee <ap@acme.example>\""
    date: 2026-10-13T10:00:00Z
    body: "One of our longest serving drivers retires at the end of the month. Many of you will know him from the morning rounds, and his colleagues are planning a small farewell.\n\nA quick note on the shipping labels: the new format puts the order reference above the address block. Older labels are still accepted, but please switch when you next reprint a batch.\n\nThe revised care instructions are now printed inside each box lid. The wording is shorter and easier to follow, and the diagrams match the current version of the product.\n\nThe packaging supplier has moved to a new site closer to our warehouse. Lead times on boxed items may improve slightly once the move has settled down over the coming weeks."
expected:
  labels: {category: notification, priority: low, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: false, sender_type: vendor, fraud_risk: none, phishing: false, private_individual: false}
  rule: otherwise
  safety: {}
---
A quick status update on the fit-out project, following the thread below.

{{pad}}

We're on schedule; the next update will come at the end of the month.
