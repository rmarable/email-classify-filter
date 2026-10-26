---
id: long-018
title: "Long card long-018"
threat: "cleaner-card-phone: scam ask after 1,500 (text)"
control: "the classifier's fraud_risk on the body alone (rules alone don't reach fraud_guard)"
why: "window tuning step 4 (plan draft 8)"
failure_looks_like: "fraud_risk none, not escalated"
review: "a long email whose scam ask sits after 1,500; confirm the labels fit the whole email"
author: claude
profile: org
from: "Brightway Cleaning Services <office@brightway-cleaning.example>"
subject: "Brightway: notes for the quarter"
date: 2026-10-21T13:04:00Z
signal: "read out the full card number and expiry"
target_chars: 4300
pad_voice: vendor
signature: |
  Brightway Cleaning Services
quoted_thread:
  - from: "\"Pat Lee <ap@acme.example>\""
    date: 2026-10-15T10:00:00Z
    body: "Some customers have asked whether the outer cartons can be recycled. They can, and from next month the cartons will carry a small printed note saying so on the side panel.\n\nOur website now shows estimated delivery days for each region. The estimates come from the carriers and are usually accurate, though bad weather can still cause the odd delay.\n\nOur office will be closed on the public holiday next month. Orders placed during the closure will be picked and packed on the first working day after it, in the order they came in.\n\nYour new contact on our sales team will introduce themselves next week. They have worked with similar customers for several years and will keep the same regular check-in that you have now."
  - from: "\"Brightway Cleaning Services <office@brightway-cleaning.example>\""
    date: 2026-10-14T10:00:00Z
    body: "The returns process is unchanged: items come back in their original packaging with the returns slip, and the warehouse checks them within a few working days of arrival.\n\nThe assembly guides are now available in larger print. They were redesigned after feedback from a care home that orders from us, and the new layout is easier for everyone to read.\n\nThe sustainability report is nearly finished. It covers packaging, transport and energy use at both sites, and we will circulate a draft to partners before it is published.\n\nThanks for taking part in the customer survey. The most common request was clearer delivery windows, and we are working with the carriers on a narrower time slot for each drop."
  - from: "\"Pat Lee <ap@acme.example>\""
    date: 2026-10-13T10:00:00Z
    body: "The product photographs on the catalogue pages have been retaken in natural light. The colours now look closer to the real items, which several customers had asked us about.\n\nWe have reviewed the feedback from the site visit. Most of the points were minor and have already been handled, and the remaining two are on the list for the next maintenance window.\n\nThe new storage racks in our main warehouse are now in use. They give us room for about a fifth more stock, which should shorten the wait on the items that sold out last winter.\n\nThe packaging supplier has moved to a new site closer to our warehouse. Lead times on boxed items may improve slightly once the move has settled down over the coming weeks."
expected:
  labels: {category: invoice, priority: high, requires_action: true, requires_reply: false, payment_related: true, deadline_mentioned: true, sender_type: vendor, fraud_risk: high, phishing: true, private_individual: false}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
---
Hello, here are our notes for the quarter from the Brightway office.

{{pad}}

To keep your contract running past Friday, please have whoever holds the office card call us on 555-0124 and read out the full card number and expiry. Our usual contact for your account is away, so please don't wait for her.

Best wishes from the Brightway team.
