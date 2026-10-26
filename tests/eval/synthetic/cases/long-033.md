---
id: long-033
title: "Long card long-033"
threat: "maintenance-notice: benign long mail"
control: "nothing to flag in a long email"
why: "window tuning step 4 (plan draft 8)"
failure_looks_like: "flagged as fraud"
review: "a long email with nothing to flag; confirm the labels fit the whole email"
author: claude
profile: org
from: "Summit Hosting Service Desk <service@summit-hosting.example>"
subject: "Planned maintenance and service notes"
date: 2026-10-21T13:55:00Z
target_chars: 4225
pad_voice: vendor
signature: |
  Summit Hosting Service Desk
quoted_thread:
  - from: "\"Pat Lee <ap@acme.example>\""
    date: 2026-10-15T10:00:00Z
    body: "Our quality team ran the usual spot checks on the latest batch. Everything was within the agreed tolerances, and the test results are kept on file should you ever want to see them.\n\nWe tested the new labels in cold storage and they stay readable at low temperatures. That was a concern raised at the last review, so it is good to have the result on record.\n\nA quick note on the shipping labels: the new format puts the order reference above the address block. Older labels are still accepted, but please switch when you next reprint a batch.\n\nThe engineering team completed the routine checks on the packing line. No faults were found, and the line is running at its normal speed again after the short planned stop."
  - from: "\"Summit Hosting Service Desk <service@summit-hosting.example>\""
    date: 2026-10-14T10:00:00Z
    body: "As you know, the warehouse team works two shifts. From next month the second shift will start an hour earlier, which should mean evening collections are ready sooner.\n\nNext quarter we will review the range of smaller items. A few slow sellers may be retired, and we will give plenty of notice before anything you order regularly is changed.\n\nThank you for the photos of the damaged carton. We have passed them to the carrier and will let you know what they say, though the goods inside appear to have been unaffected.\n\nThe holiday rota for our support desk is confirmed. There will always be someone on duty during working hours, though replies may take a little longer in the busiest week."
  - from: "\"Pat Lee <ap@acme.example>\""
    date: 2026-10-13T10:00:00Z
    body: "Thank you for the kind note about the driver who helped unload during the storm. We have passed your thanks on, and he was pleased to hear that the delivery made a difference.\n\nFollowing last week's meeting, the facilities group agreed to move the loading bay deliveries to the morning slot. Drivers have been told, and the gate staff have the updated rota.\n\nWe noticed the delivery address on your last few orders has a different unit number. The driver found you without trouble, but it would help to have the full address on the order.\n\nOne of our longest serving drivers retires at the end of the month. Many of you will know him from the morning rounds, and his colleagues are planning a small farewell."
expected:
  labels: {category: notification, priority: low, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: true, sender_type: vendor, fraud_risk: none, phishing: false, private_individual: false}
  rule: otherwise
  safety: {}
---
Hello, here are this month's service notes, including a planned maintenance window.

{{pad}}

Maintenance runs on Sunday night; no action is needed from you.
