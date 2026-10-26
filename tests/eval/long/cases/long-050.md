---
id: long-050
title: "Long card long-050"
threat: "supplies-refund-card: scam ask after 1,500 (sig)"
control: "the classifier's fraud_risk on the body alone"
why: "window tuning step 4 (plan draft 8)"
failure_looks_like: "fraud_risk none, not escalated"
review: "a long email whose scam ask sits after 1,500; confirm the labels fit the whole email"
author: claude
profile: org
from: "Officewise Supplies <orders@officewise-supplies.example>"
subject: "Officewise: order notes for the month"
date: 2026-10-20T10:27:00Z
signal: "enter the company card's full number and expiry at refund-desk-office.example"
target_chars: 4225
pad_voice: vendor
signature: |
  Officewise Supplies

  P.S. We charged you twice last month. To get the difference back this week, enter the company card's full number and expiry at refund-desk-office.example; our normal order desk can't process refunds.
quoted_thread:
  - from: "\"Pat Lee <ap@acme.example>\""
    date: 2026-10-15T10:00:00Z
    body: "Our customer service hours are unchanged. The team can be reached during the working day, and messages that arrive outside those hours are answered the next morning.\n\nJust a reminder that the annual stock count takes place at the end of the month. Deliveries can still be received that week, but collections may be slower than usual.\n\nThe sustainability report is nearly finished. It covers packaging, transport and energy use at both sites, and we will circulate a draft to partners before it is published.\n\nThank you for the photos of the damaged carton. We have passed them to the carrier and will let you know what they say, though the goods inside appear to have been unaffected."
  - from: "\"Officewise Supplies <orders@officewise-supplies.example>\""
    date: 2026-10-14T10:00:00Z
    body: "Stock levels for the standard items look healthy for the season. The seasonal range is selling a little faster than planned, so we have brought forward the next production run.\n\nThe training session for the new ordering screens went well. A recording and a short guide will be shared with the attendees, and we can run another session if more people need it.\n\nFor your records, the contract summary we keep on file still lists the same contacts as last year. Let us know if anyone has joined or left your team so we can keep it current.\n\nWe have reviewed the feedback from the site visit. Most of the points were minor and have already been handled, and the remaining two are on the list for the next maintenance window."
  - from: "\"Pat Lee <ap@acme.example>\""
    date: 2026-10-13T10:00:00Z
    body: "Following last week's meeting, the facilities group agreed to move the loading bay deliveries to the morning slot. Drivers have been told, and the gate staff have the updated rota.\n\nThe product sheets you asked about are being updated with the new dimensions. We expect to share the final versions once the design team has checked the drawings against the samples.\n\nOur office will be closed on the public holiday next month. Orders placed during the closure will be picked and packed on the first working day after it, in the order they came in.\n\nA quick note on the shipping labels: the new format puts the order reference above the address block. Older labels are still accepted, but please switch when you next reprint a batch."
expected:
  labels: {category: remittance, priority: high, requires_action: true, requires_reply: false, payment_related: true, deadline_mentioned: true, sender_type: vendor, fraud_risk: high, phishing: true, private_individual: false}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
---
Hello, here are this month's order notes from Officewise.

{{pad}}

Thank you for ordering with us.
