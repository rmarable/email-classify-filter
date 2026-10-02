---
id: other-holiday-greeting
title: A customer's season's greetings
threat: none
control: rule 10
why: balances the set
failure_looks_like: flagged as fraud
from: "Customer T <team@customer-t.example>"
subject: "Season's greetings from Customer T"
date: 2026-12-18T09:00:00Z
author: claude
expected:
  labels: {category: other, fraud_risk: none}
  rule: otherwise
---
Thank you for a great year working together. Wishing everyone at ACME a restful holiday season.
