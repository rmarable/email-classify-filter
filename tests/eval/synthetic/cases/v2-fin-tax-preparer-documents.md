---
id: v2-fin-tax-preparer-documents
title: Tax preparer asks for documents for an extended return
threat: none (control)
control: a known service provider asking for documents (not money or bank details) needs a reply, no fraud flag
why: a personal vendor writing about taxes; must not be labelled regulatory or fraud
failure_looks_like: labelled regulatory, flagged as fraud, or requires_reply left false
review: finance or regulatory is a judgement call (a preparer, not the tax authority); drafted as finance
author: claude
profile: freemail
from: "Oliver Fern <oliver-fern@ferntax.example>"
subject: "Documents for your extended return"
date: 2026-10-01T16:30:00Z
expected:
  labels: {category: finance, priority: medium, requires_action: true, requires_reply: true, payment_related: false, deadline_mentioned: true, sender_type: vendor, fraud_risk: none}
  rule: travel_finance
---
Hi Pat,

To finish your extended return before the 2026-10-15 deadline, I still need the
brokerage tax form and the receipt for the new boiler.

Please drop them in the client folder as before, and reply to confirm whether anything
changed for Sam's income this year.

Thanks,
Oliver Fern, Fern Tax Prep
