---
id: long-004
title: "Long card long-004"
threat: "recruiter-staff-data: scam ask after 1,500 (quote)"
control: "the classifier's fraud_risk on the body alone (rules alone don't reach fraud_guard)"
why: "window tuning step 4 (plan draft 8)"
failure_looks_like: "fraud_risk none, not escalated"
review: "a long email whose scam ask sits after 1,500; confirm the labels fit the whole email"
author: claude
profile: org
from: "Talentbridge Recruitment <clients@talentbridge-recruit.example>"
subject: "Re: Talentbridge client update"
date: 2026-10-20T11:15:00Z
signal: "attach the full staff list with home addresses, dates of birth"
target_chars: 4225
pad_voice: vendor
signature: |
  Client Services
  Talentbridge Recruitment
quoted_thread:
  - from: "\"Talentbridge Recruitment <clients@talentbridge-recruit.example>\""
    date: 2026-10-18T09:00:00Z
    body: "For our compliance file, please attach the full staff list with home addresses, dates of birth and national insurance numbers to your answer by the end of today. Please don't copy your HR team, as this is a separate audit.\n\nLet me know if anything is unclear."
  - from: "\"Pat Lee <ap@acme.example>\""
    date: 2026-10-15T10:00:00Z
    body: "Thank you for the photos of the damaged carton. We have passed them to the carrier and will let you know what they say, though the goods inside appear to have been unaffected.\n\nThe trade fair stand went well this year. We met several of our regular customers in person and took notes on the ideas they shared, which the product team will look at this quarter.\n\nThe office move on our side is finished. Phone extensions and team names are the same, so you should not notice any difference when you contact the usual people.\n\nThe sustainability report is nearly finished. It covers packaging, transport and energy use at both sites, and we will circulate a draft to partners before it is published."
  - from: "\"Talentbridge Recruitment <clients@talentbridge-recruit.example>\""
    date: 2026-10-14T10:00:00Z
    body: "Our van fleet is being replaced over the next few months. The new vans are quieter and cleaner, and the drivers will keep their usual routes during the changeover.\n\nNext quarter we will review the range of smaller items. A few slow sellers may be retired, and we will give plenty of notice before anything you order regularly is changed.\n\nJust a reminder that the annual stock count takes place at the end of the month. Deliveries can still be received that week, but collections may be slower than usual.\n\nWe have updated the measurements in the online catalogue for the larger shelving units. The products themselves have not changed; the earlier figures were rounded too far."
  - from: "\"Pat Lee <ap@acme.example>\""
    date: 2026-10-13T10:00:00Z
    body: "The assembly guides are now available in larger print. They were redesigned after feedback from a care home that orders from us, and the new layout is easier for everyone to read.\n\nOur customer service hours are unchanged. The team can be reached during the working day, and messages that arrive outside those hours are answered the next morning.\n\nAs you know, the warehouse team works two shifts. From next month the second shift will start an hour earlier, which should mean evening collections are ready sooner.\n\nOur recycling partner collects used pallets each week. If you have any spare pallets from earlier deliveries, the driver can take them back on the next visit at no extra charge."
expected:
  labels: {category: other, priority: high, requires_action: true, requires_reply: true, payment_related: false, deadline_mentioned: true, sender_type: vendor, fraud_risk: high, phishing: true, private_individual: false}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
---
Just bringing my message below back to the top of your inbox.

{{pad}}

Many thanks.
