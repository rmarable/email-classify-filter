---
id: starter-header
title: Forged Authentication-Results claiming DMARC pass
threat: header forgery
control: provider Authentication-Results are never trusted; ecf's own DKIM/DMARC decides
why: an unsigned message pretends to pass
failure_looks_like: auth_result is pass for a message with no valid signature
from: "Billing <billing@vendor-a.example>"
subject: "Invoice INV-3001 attached"
auth_results:
  - "mx.example.net; dkim=pass header.d=vendor-a.example; dmarc=pass header.from=vendor-a.example"
author: claude
to: [ap@acme.example]
date: 2026-10-01T09:00:00Z
expected:
  labels: {category: invoice, payment_related: true}
  facts: {auth_result: none}
  rule: unverified_payment_sender
  safety: {must_not_hide: true}
---
Please find invoice INV-3001 for September services. Due 2026-10-15.
