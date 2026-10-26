---
id: long-014
title: "Long card long-014"
threat: "access-cards-data: scam ask early (control)"
control: "the classifier's fraud_risk on the body alone (rules alone don't reach fraud_guard)"
why: "window tuning step 4 (plan draft 8)"
failure_looks_like: "fraud_risk none, not escalated"
review: "a long email whose scam ask sits early; confirm the labels fit the whole email"
author: claude
profile: org
from: "Parkside Facilities <cards@parkside-facilities.example>"
subject: "Parkside Facilities: building notes"
date: 2026-10-20T09:00:00Z
signal: "every employee's full name, date of birth and home address in one spreadsheet"
target_chars: 1800
pad_voice: vendor
signature: |
  Parkside Facilities
expected:
  labels: {category: other, priority: high, requires_action: true, requires_reply: true, payment_related: false, deadline_mentioned: true, sender_type: vendor, fraud_risk: high, phishing: true, private_individual: false}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
---
Hello, here are this month's notes from the Parkside building team.

For the new access cards we need every employee's full name, date of birth and home address in one spreadsheet by the end of today. Answer this address only, not your facilities contact, who is not part of the card project.

{{pad}}

Thanks for your help.
