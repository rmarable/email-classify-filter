---
id: v3-reg-newsletter-mention
title: "Vendor newsletter that mentions a regulator"
threat: "a mention mistaken for a regulator claim"
control: "a newsletter that only mentions a regulator is not a regulator writing"
why: "v3.0.0 S1 coverage card"
failure_looks_like: "escalated as regulatory"
review: "the regulator keyword trigger escalates a mere mention (rule 2), so the expected rule is regulatory; the classifier should still not call it a regulator writing"
author: claude
from: "Compliance Weekly <weekly@compliance-news.example>"
subject: "What the new SEC disclosure rules mean for you"
date: 2026-10-08T07:30:00Z
bulk: true
expected:
  labels: {category: marketing, sender_type: company, priority: low, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: false, fraud_risk: none}
  rule: regulatory
---
This week: a plain-language summary of the SEC's new disclosure rules, and three webinars on preparing for them. Register free.
