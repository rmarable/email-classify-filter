---
id: v2-fin-insurance-claim-photos
title: Insurance adjuster asks for claim photos by a date
threat: none (control)
control: a legitimate insurance notice that asks for documents (not money or credentials) needs action and a deadline, no fraud flag
why: tests requires_action and deadline_mentioned on finance mail from a named person at a company
failure_looks_like: flagged as fraud, or requires_action and deadline_mentioned left false
author: claude
profile: freemail
from: "Tess Arbor at Northfield Mutual Claims <tess-arbor@northfield-mutual.example>"
subject: "Your claim: photos needed"
date: 2026-10-08T14:05:00Z
expected:
  labels: {category: finance, priority: medium, requires_action: true, requires_reply: false, payment_related: false, deadline_mentioned: true, sender_type: company, fraud_risk: none}
  rule: travel_finance
---
Hello Pat,

I'm the adjuster handling your water damage claim on the home policy ending 0007.
To keep it moving, please upload photos of the kitchen ceiling and the damaged cabinets
in your online account by 2026-10-20.

You don't need to send anything by email. I'll be in touch once I've reviewed them.

Tess Arbor
Northfield Mutual Claims
