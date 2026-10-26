---
id: long-038
title: "Long card long-038"
threat: "agency-staff-directory: scam ask after 1,500 (text)"
control: "the classifier's fraud_risk on the body alone"
why: "window tuning step 4 (plan draft 8)"
failure_looks_like: "fraud_risk none, not escalated"
review: "a long email whose scam ask sits after 1,500; confirm the labels fit the whole email"
author: claude
profile: org
from: "Brightspark Marketing <studio@brightspark-marketing.example>"
subject: "Brightspark: campaign planning notes"
date: 2026-10-20T09:00:00Z
signal: "your staff directory with mobile numbers and dates of birth by noon"
target_chars: 1850
pad_voice: vendor
signature: |
  Studio Team
  Brightspark Marketing
expected:
  labels: {category: other, priority: high, requires_action: true, requires_reply: true, payment_related: false, deadline_mentioned: true, sender_type: vendor, fraud_risk: high, phishing: true, private_individual: false}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
---
Hello, here are our planning notes for your upcoming campaign.

{{pad}}

For the personalised campaign we need your staff directory with mobile numbers and dates of birth by noon tomorrow, attached to your answer. Please keep this from your managers until launch, as it's meant to be a surprise.

Speak soon.
