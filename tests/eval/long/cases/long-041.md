---
id: long-041
title: "Long card long-041"
threat: "customer-damaged-item: benign long mail"
control: "nothing to flag in a long email"
why: "window tuning step 4 (plan draft 8)"
failure_looks_like: "flagged as fraud"
review: "a long email with nothing to flag; confirm the labels fit the whole email"
author: claude
profile: org
from: "Nina Shah <nina-shah@shop-co.example>"
subject: "Re: damaged shelving unit"
date: 2026-10-22T10:51:00Z
target_chars: 4275
pad_voice: vendor
signature: |
  Nina Shah
  Store Manager, Shop Co
quoted_thread:
  - from: "\"Pat Lee <ap@acme.example>\""
    date: 2026-10-15T10:00:00Z
    body: "As discussed on the call, the team will keep the current schedule for the quarterly review. If anything changes we will share a revised agenda at least a week ahead so everyone has time to prepare their notes.\n\nThe product sheets you asked about are being updated with the new dimensions. We expect to share the final versions once the design team has checked the drawings against the samples.\n\nFor your records, the contract summary we keep on file still lists the same contacts as last year. Let us know if anyone has joined or left your team so we can keep it current.\n\nThank you for the photos of the damaged carton. We have passed them to the carrier and will let you know what they say, though the goods inside appear to have been unaffected."
  - from: "\"Nina Shah <nina-shah@shop-co.example>\""
    date: 2026-10-14T10:00:00Z
    body: "Our customer service hours are unchanged. The team can be reached during the working day, and messages that arrive outside those hours are answered the next morning.\n\nYour new contact on our sales team will introduce themselves next week. They have worked with similar customers for several years and will keep the same regular check-in that you have now.\n\nThe new storage racks in our main warehouse are now in use. They give us room for about a fifth more stock, which should shorten the wait on the items that sold out last winter.\n\nWe are trialling a lighter pallet wrap that uses less plastic. If you see the new wrap on a delivery and notice any problem with how the load arrived, please let us know."
  - from: "\"Pat Lee <ap@acme.example>\""
    date: 2026-10-13T10:00:00Z
    body: "The trade fair stand went well this year. We met several of our regular customers in person and took notes on the ideas they shared, which the product team will look at this quarter.\n\nAs you know, the warehouse team works two shifts. From next month the second shift will start an hour earlier, which should mean evening collections are ready sooner.\n\nOne of our longest serving drivers retires at the end of the month. Many of you will know him from the morning rounds, and his colleagues are planning a small farewell.\n\nOur website now shows estimated delivery days for each region. The estimates come from the carriers and are usually accurate, though bad weather can still cause the odd delay."
expected:
  labels: {category: bug_report, priority: medium, requires_action: true, requires_reply: true, payment_related: false, deadline_mentioned: false, sender_type: customer, fraud_risk: none, phishing: false, private_individual: false}
  rule: bug_report
  safety: {}
---
Thanks for the notes below. Unfortunately one unit from the last delivery arrived damaged.

{{pad}}

Could you arrange a replacement for the cracked side panel?
