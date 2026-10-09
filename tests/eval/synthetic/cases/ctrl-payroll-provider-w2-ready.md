---
id: ctrl-payroll-provider-w2-ready
title: Payroll provider says the employees' W-2s are ready in its portal
threat: none (negative for the tax_form_request signal)
control: tax_form_request needs a request to send the forms; a notice that they are ready isn't one
why: payroll providers send this every January to every client
failure_looks_like: escalated as the W-2 scam
from: "Payroll Partner <notices@payroll-partner.example>"
subject: "Your 2026 W-2s are ready"
date: 2026-11-20T09:00:00Z
author: claude
expected:
  labels: {category: notification, sender_type: automated, requires_action: false, fraud_risk: none}
  facts: {auth_result: pass}
  rule: automated_notification
---
The 2026 W-2s for your 23 employees are ready. They will be filed with the agencies on your
schedule, and employees can view their own copies in the employee app.

No action is needed. This is an automatic notice from Payroll Partner.
