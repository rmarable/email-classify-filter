---
id: bug-export-fails
title: Customer reports an export error
threat: none
control: rule 3 labels bug reports; requires_reply continues to the actor
why: the ordinary bug report
failure_looks_like: archived, or no bug_report label
from: "Customer M Support <it@customer-m.example>"
subject: "CSV export fails with an error"
date: 2026-10-03T14:00:00Z
review: a known sender (passes DMARC, seen before) so it reaches the `actor` role, not `actor_high` (v3.0.0 H5); confirm nothing else changes
author: claude
expected:
  labels: {category: bug_report, requires_reply: true, fraud_risk: none}
  rule: bug_report
  facts: {auth_result: pass, sender_seen_before: true, bulk_corroborates: false}
---
Hello,

Since yesterday the CSV export stops at 50% with "export failed (code 17)". We tried two
browsers. Can you look into it?

Customer M Support
