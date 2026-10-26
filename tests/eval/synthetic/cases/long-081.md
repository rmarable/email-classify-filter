---
id: long-081
title: "Long card long-081"
threat: "supplier-review-meeting: benign long mail"
control: "nothing to flag in a long email"
why: "window tuning step 4 (plan draft 8)"
failure_looks_like: "flagged as fraud"
review: "a long email with nothing to flag; confirm the labels fit the whole email"
author: claude
profile: org
from: "Vendor I Account Team <accounts-team@vendor-i.example>"
subject: "Quarterly review meeting"
date: 2026-10-21T12:20:00Z
target_chars: 4125
pad_voice: vendor
signature: |
  Account Team
  Vendor I
quoted_thread:
  - from: "\"Pat Lee <ap@acme.example>\""
    date: 2026-10-15T10:00:00Z
    body: "As discussed on the call, the team will keep the current schedule for the quarterly review. If anything changes we will share a revised agenda at least a week ahead so everyone has time to prepare their notes.\n\nWe have restocked the spare parts that were short in the autumn. Hinges, brackets and the small fixing kits are all back on the shelves and can be added to your next order.\n\nThe engineering team completed the routine checks on the packing line. No faults were found, and the line is running at its normal speed again after the short planned stop.\n\nThanks again for the update on the spring order. The warehouse confirmed that the pallets arrived in good condition and the count matched the packing list, so nothing further is needed on our side for that delivery."
  - from: "\"Vendor I Account Team <accounts-team@vendor-i.example>\""
    date: 2026-10-14T10:00:00Z
    body: "The returns process is unchanged: items come back in their original packaging with the returns slip, and the warehouse checks them within a few working days of arrival.\n\nWe have added two new colours to the catalogue in response to requests from several customers. Samples are available on request, and the full range will be shown at the trade fair.\n\nOur recycling partner collects used pallets each week. If you have any spare pallets from earlier deliveries, the driver can take them back on the next visit at no extra charge.\n\nAs you know, the warehouse team works two shifts. From next month the second shift will start an hour earlier, which should mean evening collections are ready sooner."
  - from: "\"Pat Lee <ap@acme.example>\""
    date: 2026-10-13T10:00:00Z
    body: "Our customer service hours are unchanged. The team can be reached during the working day, and messages that arrive outside those hours are answered the next morning.\n\nThe assembly guides are now available in larger print. They were redesigned after feedback from a care home that orders from us, and the new layout is easier for everyone to read.\n\nWe have updated the measurements in the online catalogue for the larger shelving units. The products themselves have not changed; the earlier figures were rounded too far.\n\nJust a reminder that the annual stock count takes place at the end of the month. Deliveries can still be received that week, but collections may be slower than usual."
expected:
  labels: {category: appointment, priority: medium, requires_action: true, requires_reply: true, payment_related: false, deadline_mentioned: true, sender_type: vendor, fraud_risk: none, phishing: false, private_individual: false}
  rule: appointment_school_family
  safety: {}
---
Hello, it's time for our quarterly review, and we'd like to find a date that suits you.

{{pad}}

Would Tuesday the twelfth or Thursday the fourteenth work for a one-hour meeting?
