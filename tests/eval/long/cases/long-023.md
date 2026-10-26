---
id: long-023
title: "Long card long-023"
threat: "security-agent-remote: scam ask after 1,500 (text)"
control: "the classifier's fraud_risk on the body alone"
why: "window tuning step 4 (plan draft 8)"
failure_looks_like: "fraud_risk none, not escalated"
review: "a long email whose scam ask sits after 1,500; confirm the labels fit the whole email"
author: claude
profile: org
from: "Shieldpoint Security Software <renewals@shieldpoint-security.example>"
subject: "Shieldpoint: product and service news"
date: 2026-10-19T09:17:00Z
signal: "install the renewal agent from the link below and give our engineer the number"
target_chars: 4350
pad_voice: vendor
signature: |
  Shieldpoint Security Software
quoted_thread:
  - from: "\"Pat Lee <ap@acme.example>\""
    date: 2026-10-15T10:00:00Z
    body: "Thank you for the kind note about the driver who helped unload during the storm. We have passed your thanks on, and he was pleased to hear that the delivery made a difference.\n\nThe product photographs on the catalogue pages have been retaken in natural light. The colours now look closer to the real items, which several customers had asked us about.\n\nWe have extended the warranty on the heavier units from one year to two. Nothing changes in how claims are handled; the longer period simply applies to orders from this month.\n\nOur purchasing team visited the timber yard that supplies the frames. The yard follows the forestry standards we agreed on, and the visit report is filed with our quality records."
  - from: "\"Shieldpoint Security Software <renewals@shieldpoint-security.example>\""
    date: 2026-10-14T10:00:00Z
    body: "The office move on our side is finished. Phone extensions and team names are the same, so you should not notice any difference when you contact the usual people.\n\nNext quarter we will review the range of smaller items. A few slow sellers may be retired, and we will give plenty of notice before anything you order regularly is changed.\n\nThe assembly guides are now available in larger print. They were redesigned after feedback from a care home that orders from us, and the new layout is easier for everyone to read.\n\nOur recycling partner collects used pallets each week. If you have any spare pallets from earlier deliveries, the driver can take them back on the next visit at no extra charge."
  - from: "\"Pat Lee <ap@acme.example>\""
    date: 2026-10-13T10:00:00Z
    body: "Thanks again for the update on the spring order. The warehouse confirmed that the pallets arrived in good condition and the count matched the packing list, so nothing further is needed on our side for that delivery.\n\nAs discussed on the call, the team will keep the current schedule for the quarterly review. If anything changes we will share a revised agenda at least a week ahead so everyone has time to prepare their notes.\n\nA quick note on the shipping labels: the new format puts the order reference above the address block. Older labels are still accepted, but please switch when you next reprint a batch.\n\nOur office will be closed on the public holiday next month. Orders placed during the closure will be picked and packed on the first working day after it, in the order they came in."
expected:
  labels: {category: action_alert, priority: high, requires_action: true, requires_reply: false, payment_related: false, deadline_mentioned: true, sender_type: vendor, fraud_risk: high, phishing: true, private_individual: false}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
---
Hello, here is the latest product and service news from Shieldpoint.

{{pad}}

Important: your protection lapsed last night and your machines are exposed. Please install the renewal agent from the link below and give our engineer the number it displays when he calls on 555-0161. Your IT provider can't do this step, so there's no need to ask them.

Stay safe, from the Shieldpoint team.
