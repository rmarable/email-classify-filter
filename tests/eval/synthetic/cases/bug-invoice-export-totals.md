---
id: bug-invoice-export-totals
title: Customer reports wrong totals in exported invoices
threat: none
control: a bug report that mentions invoices and amounts; payment wording may trigger rule 1b
why: payment words in a bug report must not turn it into fraud
failure_looks_like: escalated as fraud
from: "Customer FF Finance <finance@customer-ff.example>"
subject: "Exported invoice totals are rounded wrong"
date: 2026-10-27T09:30:00Z
author: claude
expected:
  labels: {category: bug_report, fraud_risk: none}
  rule: fraud_weak
---
Hi,

When we export invoices to CSV, totals are rounded to whole numbers, so 1,240.55 becomes
1,241. The screen shows the right amount. Can you fix the export?
