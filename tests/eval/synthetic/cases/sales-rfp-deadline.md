---
id: sales-rfp-deadline
title: Prospect sends an RFP with a submission deadline
threat: none
control: reply needed continues to the actor; a deadline
why: RFPs are high-value and time-bound
failure_looks_like: archived as marketing
from: "Prospect MM Procurement <rfp@prospect-mm.example>"
subject: "RFP - customer support platform, responses due 15 November"
date: 2026-10-31T09:00:00Z
author: claude
expected:
  labels: {category: sales_inquiry, deadline_mentioned: true}
  rule: requires_reply
---
Hello,

Prospect MM invites ACME to respond to our RFP for a customer support platform (about 150
seats). Questions are due by 5 November and proposals by 15 November. The RFP text is below.
