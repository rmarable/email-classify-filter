---
id: home-bank-statement
title: Bank says the monthly statement is ready
threat: none (control)
control: a routine bank notice without links or money requests must not escalate
why: personal accounts get these monthly; the word bank alone is not a bank-detail keyword
failure_looks_like: escalated as bank fraud
author: claude
profile: freemail
from: "Harbor Savings <statements@harbor-savings.example>"
subject: "Your October statement is ready"
date: 2026-10-09T06:00:00Z
expected:
  labels: {category: finance, sender_type: automated, requires_action: false, requires_reply: false, payment_related: false, fraud_risk: none}
  rule: travel_finance
---
Hello,

Your statement for the checking account ending 0003 is ready. Sign in to online banking
or our app as you usually do to view it.

We will never ask for your password or a code by email.

Harbor Savings
