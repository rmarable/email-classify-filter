---
id: long-031
title: "Long card long-031"
threat: "lease-director-data: scam ask after 1,500 (quote)"
control: "the classifier's fraud_risk on the body alone"
why: "window tuning step 4 (plan draft 8)"
failure_looks_like: "fraud_risk none, not escalated"
review: "a long email whose scam ask sits after 1,500; confirm the labels fit the whole email"
author: claude
profile: org
from: "Oakridge Equipment Leasing <leases@oakridge-leasing.example>"
subject: "Re: Oakridge lease review"
date: 2026-10-23T11:15:00Z
signal: "confirm the director's home address and date of birth on the form at lease-review-desk.example"
target_chars: 4250
pad_voice: vendor
signature: |
  Lease Reviews
  Oakridge Equipment Leasing
quoted_thread:
  - from: "\"Oakridge Equipment Leasing <leases@oakridge-leasing.example>\""
    date: 2026-10-18T09:00:00Z
    body: "Your equipment lease will be ended today unless you confirm the director's home address and date of birth on the form at lease-review-desk.example. Our usual leasing contact isn't involved in this review.\n\nLet me know if anything is unclear."
  - from: "\"Pat Lee <ap@acme.example>\""
    date: 2026-10-15T10:00:00Z
    body: "One of our longest serving drivers retires at the end of the month. Many of you will know him from the morning rounds, and his colleagues are planning a small farewell.\n\nOur van fleet is being replaced over the next few months. The new vans are quieter and cleaner, and the drivers will keep their usual routes during the changeover.\n\nSome customers have asked whether the outer cartons can be recycled. They can, and from next month the cartons will carry a small printed note saying so on the side panel.\n\nYour new contact on our sales team will introduce themselves next week. They have worked with similar customers for several years and will keep the same regular check-in that you have now."
  - from: "\"Oakridge Equipment Leasing <leases@oakridge-leasing.example>\""
    date: 2026-10-14T10:00:00Z
    body: "The sustainability report is nearly finished. It covers packaging, transport and energy use at both sites, and we will circulate a draft to partners before it is published.\n\nWe have reviewed the feedback from the site visit. Most of the points were minor and have already been handled, and the remaining two are on the list for the next maintenance window.\n\nAs discussed on the call, the team will keep the current schedule for the quarterly review. If anything changes we will share a revised agenda at least a week ahead so everyone has time to prepare their notes.\n\nThe office move on our side is finished. Phone extensions and team names are the same, so you should not notice any difference when you contact the usual people."
  - from: "\"Pat Lee <ap@acme.example>\""
    date: 2026-10-13T10:00:00Z
    body: "The new pallet jacks arrived and the team has been trained on them. Loading is a little quicker now, which helps on days when several collections are booked close together.\n\nWe have restocked the spare parts that were short in the autumn. Hinges, brackets and the small fixing kits are all back on the shelves and can be added to your next order.\n\nThe trade fair stand went well this year. We met several of our regular customers in person and took notes on the ideas they shared, which the product team will look at this quarter.\n\nOur quality team ran the usual spot checks on the latest batch. Everything was within the agreed tolerances, and the test results are kept on file should you ever want to see them."
expected:
  labels: {category: finance, priority: high, requires_action: true, requires_reply: false, payment_related: false, deadline_mentioned: true, sender_type: vendor, fraud_risk: high, phishing: true, private_individual: false}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
---
Bringing my note below to the top, as we need this today.

{{pad}}

Thank you.
