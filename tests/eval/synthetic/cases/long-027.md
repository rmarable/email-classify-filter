---
id: long-027
title: "Long card long-027"
threat: "customer-delivery-question: benign long mail"
control: "nothing to flag in a long email"
why: "window tuning step 4 (plan draft 8)"
failure_looks_like: "flagged as fraud"
review: "a long email with nothing to flag; confirm the labels fit the whole email"
author: claude
profile: org
from: "Jordan Price <jordan-price@client-co.example>"
subject: "Re: our spring order"
date: 2026-10-22T10:44:00Z
target_chars: 4225
pad_voice: vendor
signature: |
  Jordan Price
  Operations, Client Co
quoted_thread:
  - from: "\"Pat Lee <ap@acme.example>\""
    date: 2026-10-15T10:00:00Z
    body: "For your records, the contract summary we keep on file still lists the same contacts as last year. Let us know if anyone has joined or left your team so we can keep it current.\n\nOur website now shows estimated delivery days for each region. The estimates come from the carriers and are usually accurate, though bad weather can still cause the odd delay.\n\nThe revised care instructions are now printed inside each box lid. The wording is shorter and easier to follow, and the diagrams match the current version of the product.\n\nThanks again for the update on the spring order. The warehouse confirmed that the pallets arrived in good condition and the count matched the packing list, so nothing further is needed on our side for that delivery."
  - from: "\"Jordan Price <jordan-price@client-co.example>\""
    date: 2026-10-14T10:00:00Z
    body: "We have updated the measurements in the online catalogue for the larger shelving units. The products themselves have not changed; the earlier figures were rounded too far.\n\nThank you for the photos of the damaged carton. We have passed them to the carrier and will let you know what they say, though the goods inside appear to have been unaffected.\n\nThe product photographs on the catalogue pages have been retaken in natural light. The colours now look closer to the real items, which several customers had asked us about.\n\nOur quality team ran the usual spot checks on the latest batch. Everything was within the agreed tolerances, and the test results are kept on file should you ever want to see them."
  - from: "\"Pat Lee <ap@acme.example>\""
    date: 2026-10-13T10:00:00Z
    body: "Our office will be closed on the public holiday next month. Orders placed during the closure will be picked and packed on the first working day after it, in the order they came in.\n\nWe have restocked the spare parts that were short in the autumn. Hinges, brackets and the small fixing kits are all back on the shelves and can be added to your next order.\n\nOur customer service hours are unchanged. The team can be reached during the working day, and messages that arrive outside those hours are answered the next morning.\n\nThe office move on our side is finished. Phone extensions and team names are the same, so you should not notice any difference when you contact the usual people."
expected:
  labels: {category: shipping, priority: medium, requires_action: true, requires_reply: true, payment_related: false, deadline_mentioned: false, sender_type: customer, fraud_risk: none, phishing: false, private_individual: false}
  rule: shipping
  safety: {}
---
Thanks for the update below. One question before we plan our week.

{{pad}}

Could you tell us which day the second half of our order will arrive?
