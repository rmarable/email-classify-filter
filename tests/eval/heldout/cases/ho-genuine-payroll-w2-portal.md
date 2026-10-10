---
id: ho-genuine-payroll-w2-portal
title: Payroll provider says W-2 forms are ready in the employer portal (genuine)
threat: none (look-alike of a W-2 / employee tax-data request)
control: a notice that tax forms are available, which sends no data and asks for none, must not be treated as a W-2 theft attempt
why: "Acme's payroll provider tells the employer admin that the year's W-2s are published in the portal. It asks for no employee data and attaches none, and the link goes to the provider's own domain. Dated January, when these notices come out. Auth pass: the provider's own domain, which publishes DMARC. A regular sender. Held-out card; written without the plan, keyword lists or signals."
failure_looks_like: escalated as a W-2 phishing attempt, or labelled regulatory
author: claude
from: "PayStream Payroll <employer-notices@paystream.example>"
to: [payroll@acme.example]
subject: "Your 2025 Forms W-2 are ready"
date: 2026-01-23T15:00:00Z
expected:
  facts: {auth_result: pass, sender_seen_before: true}
  labels: {category: finance, priority: medium, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: true, sender_type: vendor, fraud_risk: low}
---
Hello Acme,

Your 2025 year-end tax forms are ready.

  Company code: ACM7
  Forms W-2 published: 214
  Employee access: from 26 January 2026 in the PayStream employee app
  Paper copies: mailed to home addresses by 30 January 2026

Admins can view and download the full set, and the W-3 summary, from the employer
portal under Reports > Year End:
https://employer.paystream.example/reports/year-end

Before 13 February, please:
- check the totals against your final 2025 payroll register
- tell us about any corrections so we can issue a W-2c

We already filed copies with the Social Security Administration for you as part of
your year-end service, so there's nothing to upload.

Reminder: PayStream will never ask you to email W-2s or employee details to us or
anyone else. Everything stays in the portal.

Questions? Your service team is on 555-0127.

PayStream Payroll, Employer Services
