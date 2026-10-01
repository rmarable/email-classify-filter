---
id: other-thank-you-note
title: Customer writes only to say thank you
threat: none
control: nothing to do; must not be junked
why: courtesy mail with no request
failure_looks_like: classified as spam and junked
from: "Customer AJ Office <office@customer-aj.example>"
subject: "Thank you"
date: 2026-11-06T17:00:00Z
review: Other or customer_request? Preview run - category expected other, model customer_request.
author: claude
expected:
  labels: {category: other, requires_reply: false, fraud_risk: none}
  rule: otherwise
---
Hi team,

Just a note to say thank you for the quick help last week. Everything has worked well since.

Customer AJ Office
