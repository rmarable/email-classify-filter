---
id: long-030
title: "Long card long-030"
threat: "vendor-newsletter: benign long mail"
control: "nothing to flag in a long email"
why: "window tuning step 4 (plan draft 8)"
failure_looks_like: "flagged as fraud"
review: "a long email with nothing to flag; confirm the labels fit the whole email"
author: claude
profile: org
from: "Vendor B News <news@vendor-b.example>"
subject: "Vendor B: product news for autumn"
date: 2026-10-19T14:55:00Z
target_chars: 4150
pad_voice: vendor
signature: |
  Vendor B
quoted_thread:
  - from: "\"Pat Lee <ap@acme.example>\""
    date: 2026-10-15T10:00:00Z
    body: "The office move on our side is finished. Phone extensions and team names are the same, so you should not notice any difference when you contact the usual people.\n\nOur recycling partner collects used pallets each week. If you have any spare pallets from earlier deliveries, the driver can take them back on the next visit at no extra charge.\n\nA quick note on the shipping labels: the new format puts the order reference above the address block. Older labels are still accepted, but please switch when you next reprint a batch.\n\nFollowing last week's meeting, the facilities group agreed to move the loading bay deliveries to the morning slot. Drivers have been told, and the gate staff have the updated rota."
  - from: "\"Vendor B News <news@vendor-b.example>\""
    date: 2026-10-14T10:00:00Z
    body: "The training session for the new ordering screens went well. A recording and a short guide will be shared with the attendees, and we can run another session if more people need it.\n\nThe sustainability report is nearly finished. It covers packaging, transport and energy use at both sites, and we will circulate a draft to partners before it is published.\n\nWe have added two new colours to the catalogue in response to requests from several customers. Samples are available on request, and the full range will be shown at the trade fair.\n\nThe revised care instructions are now printed inside each box lid. The wording is shorter and easier to follow, and the diagrams match the current version of the product."
  - from: "\"Pat Lee <ap@acme.example>\""
    date: 2026-10-13T10:00:00Z
    body: "Our quality team ran the usual spot checks on the latest batch. Everything was within the agreed tolerances, and the test results are kept on file should you ever want to see them.\n\nThe holiday rota for our support desk is confirmed. There will always be someone on duty during working hours, though replies may take a little longer in the busiest week.\n\nOur van fleet is being replaced over the next few months. The new vans are quieter and cleaner, and the drivers will keep their usual routes during the changeover.\n\nThe returns process is unchanged: items come back in their original packaging with the returns slip, and the warehouse checks them within a few working days of arrival."
expected:
  labels: {category: marketing, priority: low, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: false, sender_type: vendor, fraud_risk: none, phishing: false, private_individual: false}
  rule: marketing
  safety: {}
---
Hello from Vendor B. Here is our product news for the autumn season.

{{pad}}

Thanks for reading, and see you next month.
