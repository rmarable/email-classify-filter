---
id: payroll-deposit-change
title: Employee asks payroll to change direct deposit from a personal address
threat: payroll diversion
control: fraud guard (staff sender type from outside the organisation)
why: attackers pose as staff to redirect salaries
failure_looks_like: handled as a routine HR request
from: "Sam Staffer <sam-staffer-home@freemail.example>"
to: [payroll@acme.example]
subject: "Direct deposit change before next payday"
date: 2026-10-02T14:05:00Z
expected:
  labels: {category: vendor_change_request, payment_related: true, fraud_risk: high, sender_type: team}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
author: claude
---
Hi Payroll,

I changed banks. Please send my pay to the new account below starting this Friday. I'm writing
from my personal email because I'm locked out of the work one.

Account: 00456-TEST-78
Thanks, Sam
