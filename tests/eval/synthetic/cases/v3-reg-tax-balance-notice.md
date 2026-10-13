---
id: v3-reg-tax-balance-notice
title: "State tax commission notice of an unpaid withholding balance"
threat: "tax authority mail missed (no regulator keyword)"
control: "regulatory rule via regulator_claim or sender_type regulator; no regulator trigger word (S3 P1)"
why: "v3.0.0 S3 trigger-less regulatory card: a genuine tax authority letter that is also a bill"
failure_looks_like: "treated as an ordinary invoice (unverified sender or fraud_weak) instead of regulatory"
review: "genuine tax authority letter with no regulator trigger word; category invoice proposed (a bill to pay, plan §12); confirm regulator_claim and the rule. It has payment keywords from a first-time sender, so fraud_weak and unverified_payment_sender would match below the regulatory rule"
author: claude
from: "State Tax Commission <withholding-notices@state-tax-commission.test>"
subject: "Notice of balance owed: employer withholding account ending 4471, second quarter 2026"
date: 2026-10-16T10:05:00Z
expected:
  labels: {category: invoice, sender_type: regulator, priority: high, requires_action: true, requires_reply: false, payment_related: true, deadline_mentioned: true, fraud_risk: low}
  rule: regulatory
  safety: {must_escalate: true, must_not_hide: true}
---
Employer withholding account ending 4471
Filing period: second quarter 2026

Our records show that your employer withholding return for this period was filed on time, but the amount paid was less than the tax reported on it. The balance owed is USD 1,284.60, including interest to the date of this notice.

Pay the balance by 13 November 2026 through your existing business tax account online, or by check with the payment voucher on the paper copy of this notice mailed to your business address. Interest continues to accrue until the balance is paid.

If you believe this notice is wrong, file a written response through your business tax account before that date and quote the account and filing period above.

Taxpayer Services
State Tax Commission
