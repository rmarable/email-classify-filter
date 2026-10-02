---
id: bank-change-reply-to
title: Bank change with a Reply-To on another domain
threat: vendor bank change; replies diverted
control: fraud trigger 1 (bank + change wording); Reply-To mismatch fact
why: replies go to the attacker even though the From looks right
failure_looks_like: reply_to_mismatch false or no escalation
from: "Vendor C Billing <billing@vendor-c.example>"
reply_to: "vendor-c-finance@mailhost.test"
subject: "New banking information effective immediately"
date: 2026-10-02T10:30:00Z
expected:
  labels: {category: vendor_change_request, payment_related: true, fraud_risk: high}
  facts: {reply_to_mismatch: true}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
author: claude
---
Dear customer,

Due to an audit our bank account has changed. Please update your records and remit all open
invoices to the new account. Reply to confirm once updated.

New account: published example IBAN GB82 WEST 1234 5698 7654 32
Vendor C Billing, 555-0117
