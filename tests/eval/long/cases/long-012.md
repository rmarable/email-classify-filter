---
id: long-012
title: "Long card long-012"
threat: "recruiter-staff-data: scam ask early (control)"
control: "the classifier's fraud_risk on the body alone (rules alone don't reach fraud_guard)"
why: "window tuning step 4 (plan draft 8)"
failure_looks_like: "fraud_risk none, not escalated"
review: "a long email whose scam ask sits early; confirm the labels fit the whole email"
author: claude
profile: org
from: "Talentbridge Recruitment <clients@talentbridge-recruit.example>"
subject: "Re: Talentbridge client update"
date: 2026-10-19T10:56:00Z
signal: "attach the full staff list with home addresses, dates of birth"
target_chars: 4225
pad_voice: vendor
signature: |
  Client Services
  Talentbridge Recruitment
quoted_thread:
  - from: "\"Pat Lee <ap@acme.example>\""
    date: 2026-10-15T10:00:00Z
    body: "A quick note on the shipping labels: the new format puts the order reference above the address block. Older labels are still accepted, but please switch when you next reprint a batch.\n\nThe trade fair stand went well this year. We met several of our regular customers in person and took notes on the ideas they shared, which the product team will look at this quarter.\n\nThe product sheets you asked about are being updated with the new dimensions. We expect to share the final versions once the design team has checked the drawings against the samples.\n\nWe have updated the measurements in the online catalogue for the larger shelving units. The products themselves have not changed; the earlier figures were rounded too far."
  - from: "\"Talentbridge Recruitment <clients@talentbridge-recruit.example>\""
    date: 2026-10-14T10:00:00Z
    body: "We have reviewed the feedback from the site visit. Most of the points were minor and have already been handled, and the remaining two are on the list for the next maintenance window.\n\nThe returns process is unchanged: items come back in their original packaging with the returns slip, and the warehouse checks them within a few working days of arrival.\n\nThe training session for the new ordering screens went well. A recording and a short guide will be shared with the attendees, and we can run another session if more people need it.\n\nOne of our longest serving drivers retires at the end of the month. Many of you will know him from the morning rounds, and his colleagues are planning a small farewell."
  - from: "\"Pat Lee <ap@acme.example>\""
    date: 2026-10-13T10:00:00Z
    body: "Thank you for the photos of the damaged carton. We have passed them to the carrier and will let you know what they say, though the goods inside appear to have been unaffected.\n\nWe tested the new labels in cold storage and they stay readable at low temperatures. That was a concern raised at the last review, so it is good to have the result on record.\n\nJust a reminder that the annual stock count takes place at the end of the month. Deliveries can still be received that week, but collections may be slower than usual.\n\nThe new pallet jacks arrived and the team has been trained on them. Loading is a little quicker now, which helps on days when several collections are booked close together."
expected:
  labels: {category: other, priority: high, requires_action: true, requires_reply: true, payment_related: false, deadline_mentioned: true, sender_type: vendor, fraud_risk: high, phishing: true, private_individual: false}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
---
Just bringing my message below back to the top of your inbox.

For our compliance file, please attach the full staff list with home addresses, dates of birth and national insurance numbers to your answer by the end of today. Please don't copy your HR team, as this is a separate audit.

{{pad}}

Many thanks.
