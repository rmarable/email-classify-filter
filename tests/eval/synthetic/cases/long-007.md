---
id: long-007
title: "Long card long-007"
threat: "domain-signin-sig: scam ask after 1,500 (sig)"
control: "the classifier's fraud_risk on the body alone (rules alone don't reach fraud_guard)"
why: "window tuning step 4 (plan draft 8)"
failure_looks_like: "fraud_risk none, not escalated"
review: "a long email whose scam ask sits after 1,500; confirm the labels fit the whole email"
author: claude
profile: org
from: "Northstar Domains <notices@northstar-domains.example>"
subject: "Northstar Domains: account newsletter"
date: 2026-10-19T08:56:00Z
signal: "sign in at domain-hold-review.example with your registrar details"
target_chars: 4225
pad_voice: vendor
signature: |
  Northstar Domains

  Urgent: your company domain is on hold and will be released tonight. Sign in at domain-hold-review.example with your registrar details to keep it; our usual control panel can't lift this hold.
quoted_thread:
  - from: "\"Pat Lee <ap@acme.example>\""
    date: 2026-10-15T10:00:00Z
    body: "The trade fair stand went well this year. We met several of our regular customers in person and took notes on the ideas they shared, which the product team will look at this quarter.\n\nThe product sheets you asked about are being updated with the new dimensions. We expect to share the final versions once the design team has checked the drawings against the samples.\n\nWe have updated the measurements in the online catalogue for the larger shelving units. The products themselves have not changed; the earlier figures were rounded too far.\n\nWe have reviewed the feedback from the site visit. Most of the points were minor and have already been handled, and the remaining two are on the list for the next maintenance window."
  - from: "\"Northstar Domains <notices@northstar-domains.example>\""
    date: 2026-10-14T10:00:00Z
    body: "The returns process is unchanged: items come back in their original packaging with the returns slip, and the warehouse checks them within a few working days of arrival.\n\nThe training session for the new ordering screens went well. A recording and a short guide will be shared with the attendees, and we can run another session if more people need it.\n\nOne of our longest serving drivers retires at the end of the month. Many of you will know him from the morning rounds, and his colleagues are planning a small farewell.\n\nThank you for the photos of the damaged carton. We have passed them to the carrier and will let you know what they say, though the goods inside appear to have been unaffected."
  - from: "\"Pat Lee <ap@acme.example>\""
    date: 2026-10-13T10:00:00Z
    body: "We tested the new labels in cold storage and they stay readable at low temperatures. That was a concern raised at the last review, so it is good to have the result on record.\n\nJust a reminder that the annual stock count takes place at the end of the month. Deliveries can still be received that week, but collections may be slower than usual.\n\nThe new pallet jacks arrived and the team has been trained on them. Loading is a little quicker now, which helps on days when several collections are booked close together.\n\nWe have added two new colours to the catalogue in response to requests from several customers. Samples are available on request, and the full range will be shown at the trade fair."
expected:
  labels: {category: account_security, priority: high, requires_action: true, requires_reply: false, payment_related: false, deadline_mentioned: true, sender_type: vendor, fraud_risk: high, phishing: true, private_individual: false}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
---
Hello, here is the latest account newsletter from Northstar Domains.

{{pad}}

Thank you for being a Northstar customer.
