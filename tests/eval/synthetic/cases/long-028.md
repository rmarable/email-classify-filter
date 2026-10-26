---
id: long-028
title: "Long card long-028"
threat: "supplier-thread-thanks: benign long mail"
control: "nothing to flag in a long email"
why: "window tuning step 4 (plan draft 8)"
failure_looks_like: "flagged as fraud"
review: "a long email with nothing to flag; confirm the labels fit the whole email"
author: claude
profile: org
from: "Vendor C Account Team <team@vendor-c.example>"
subject: "Re: thanks for the visit"
date: 2026-10-19T15:16:00Z
target_chars: 4200
pad_voice: vendor
signature: |
  Account Team
  Vendor C
quoted_thread:
  - from: "\"Pat Lee <ap@acme.example>\""
    date: 2026-10-15T10:00:00Z
    body: "The revised care instructions are now printed inside each box lid. The wording is shorter and easier to follow, and the diagrams match the current version of the product.\n\nThank you for the kind note about the driver who helped unload during the storm. We have passed your thanks on, and he was pleased to hear that the delivery made a difference.\n\nThe sustainability report is nearly finished. It covers packaging, transport and energy use at both sites, and we will circulate a draft to partners before it is published.\n\nWe have restocked the spare parts that were short in the autumn. Hinges, brackets and the small fixing kits are all back on the shelves and can be added to your next order."
  - from: "\"Vendor C Account Team <team@vendor-c.example>\""
    date: 2026-10-14T10:00:00Z
    body: "Following last week's meeting, the facilities group agreed to move the loading bay deliveries to the morning slot. Drivers have been told, and the gate staff have the updated rota.\n\nThanks for taking part in the customer survey. The most common request was clearer delivery windows, and we are working with the carriers on a narrower time slot for each drop.\n\nOur recycling partner collects used pallets each week. If you have any spare pallets from earlier deliveries, the driver can take them back on the next visit at no extra charge.\n\nWe noticed the delivery address on your last few orders has a different unit number. The driver found you without trouble, but it would help to have the full address on the order."
  - from: "\"Pat Lee <ap@acme.example>\""
    date: 2026-10-13T10:00:00Z
    body: "The product photographs on the catalogue pages have been retaken in natural light. The colours now look closer to the real items, which several customers had asked us about.\n\nJust a reminder that the annual stock count takes place at the end of the month. Deliveries can still be received that week, but collections may be slower than usual.\n\nOne of our longest serving drivers retires at the end of the month. Many of you will know him from the morning rounds, and his colleagues are planning a small farewell.\n\nWe have reviewed the feedback from the site visit. Most of the points were minor and have already been handled, and the remaining two are on the list for the next maintenance window."
expected:
  labels: {category: notification, priority: low, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: false, sender_type: vendor, fraud_risk: none, phishing: false, private_individual: false}
  rule: otherwise
  safety: {}
---
Thanks again for hosting us last week. A few notes from our side below.

{{pad}}

Nothing is needed from you; we'll be in touch before the next review.
