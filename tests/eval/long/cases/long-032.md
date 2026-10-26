---
id: long-032
title: "Long card long-032"
threat: "courier-release-charge: scam ask after 1,500 (quote)"
control: "the classifier's fraud_risk on the body alone"
why: "window tuning step 4 (plan draft 8)"
failure_looks_like: "fraud_risk none, not escalated"
review: "a long email whose scam ask sits after 1,500; confirm the labels fit the whole email"
author: claude
profile: org
from: "Swiftlane Couriers <desk@swiftlane-couriers.example>"
subject: "Re: Swiftlane delivery for your office"
date: 2026-10-20T12:29:00Z
signal: "clear the release charge at parcel-release-desk.example within 12 hours"
target_chars: 4225
pad_voice: vendor
signature: |
  Swiftlane Couriers
quoted_thread:
  - from: "\"Swiftlane Couriers <desk@swiftlane-couriers.example>\""
    date: 2026-10-18T09:00:00Z
    body: "A delivery for your office is held at customs. Please clear the release charge at parcel-release-desk.example within 12 hours or it goes back to the sender. Our depot can't release it, so there's no point calling them.\n\nLet me know if anything is unclear."
  - from: "\"Pat Lee <ap@acme.example>\""
    date: 2026-10-15T10:00:00Z
    body: "The product sheets you asked about are being updated with the new dimensions. We expect to share the final versions once the design team has checked the drawings against the samples.\n\nThe holiday rota for our support desk is confirmed. There will always be someone on duty during working hours, though replies may take a little longer in the busiest week.\n\nThe assembly guides are now available in larger print. They were redesigned after feedback from a care home that orders from us, and the new layout is easier for everyone to read.\n\nThe revised care instructions are now printed inside each box lid. The wording is shorter and easier to follow, and the diagrams match the current version of the product."
  - from: "\"Swiftlane Couriers <desk@swiftlane-couriers.example>\""
    date: 2026-10-14T10:00:00Z
    body: "Thank you for the kind note about the driver who helped unload during the storm. We have passed your thanks on, and he was pleased to hear that the delivery made a difference.\n\nThe sustainability report is nearly finished. It covers packaging, transport and energy use at both sites, and we will circulate a draft to partners before it is published.\n\nWe have restocked the spare parts that were short in the autumn. Hinges, brackets and the small fixing kits are all back on the shelves and can be added to your next order.\n\nFollowing last week's meeting, the facilities group agreed to move the loading bay deliveries to the morning slot. Drivers have been told, and the gate staff have the updated rota."
  - from: "\"Pat Lee <ap@acme.example>\""
    date: 2026-10-13T10:00:00Z
    body: "Thanks for taking part in the customer survey. The most common request was clearer delivery windows, and we are working with the carriers on a narrower time slot for each drop.\n\nOur recycling partner collects used pallets each week. If you have any spare pallets from earlier deliveries, the driver can take them back on the next visit at no extra charge.\n\nWe noticed the delivery address on your last few orders has a different unit number. The driver found you without trouble, but it would help to have the full address on the order.\n\nThe product photographs on the catalogue pages have been retaken in natural light. The colours now look closer to the real items, which several customers had asked us about."
expected:
  labels: {category: shipping, priority: high, requires_action: true, requires_reply: false, payment_related: true, deadline_mentioned: true, sender_type: vendor, fraud_risk: high, phishing: true, private_individual: false}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
---
Following up on the message below about your held delivery.

{{pad}}

Thanks for your help.
