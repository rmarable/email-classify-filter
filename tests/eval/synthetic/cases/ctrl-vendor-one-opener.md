---
id: ctrl-vendor-one-opener
title: A vendor's account manager asks if the reader is available for a call
threat: none (negative for the bec_opener signal)
control: bec_opener needs two different opener phrases; one alone is ordinary mail (OD-479)
why: are you available is how most meeting requests start
failure_looks_like: escalated as executive impersonation
from: "Jordan Lake <jordan-lake@vendor-b.example>"
subject: "Call about next quarter"
date: 2026-11-19T11:20:00Z
author: claude
expected:
  labels: {category: other, sender_type: vendor, requires_reply: true, fraud_risk: none}
  rule: requires_reply
---
Hi,

Are you available Thursday afternoon for a 20-minute call about next quarter's delivery
schedule? Any time after 2 pm works for me.

Thanks,
Jordan Lake
Account Manager, Vendor B
