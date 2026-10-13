---
id: v3-pi-job-question
title: "A private person asks about a job posting"
threat: "none (control)"
control: "rule 9: a reply is needed from someone unknown, nothing else"
why: "v3.0.0 S1 coverage card"
failure_looks_like: "escalated as fraud"
author: claude
from: "Riley Okafor <riley-okafor@freemail.example>"
subject: "Question about the support role"
date: 2026-10-07T18:00:00Z
expected:
  labels: {category: other, sender_type: person, priority: medium, requires_action: false, requires_reply: true, payment_related: false, deadline_mentioned: false, fraud_risk: none}
  rule: requires_reply
---
Hello, I saw the support specialist opening. Is the role open to remote applicants? Thank you.
