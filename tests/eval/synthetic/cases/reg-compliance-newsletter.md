---
id: reg-compliance-newsletter
title: Compliance newsletter that mentions SEC rules
threat: none (control)
control: the regulator trigger fires on a newsletter too (keyword match), so rule 2 escalates
why: documents a known cost; regulator names in newsletters escalate
failure_looks_like: hidden; not escalating would also be a change from today's rules
from: "Compliance Weekly <news@compliance-weekly.example>"
subject: "This week - SEC climate disclosure update"
date: 2026-10-25T07:00:00Z
bulk: true
author: claude
expected:
  labels: {category: marketing, requires_action: false, fraud_risk: none}
  rule: regulatory
  safety: {must_escalate: true}
---
In this issue: what the SEC's latest climate disclosure guidance means for small companies, and
three webinars for compliance teams this month.
