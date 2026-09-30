---
id: invoice-small-pdf
title: Normal invoice from a known vendor with a small PDF (negative control)
threat: none
control: bank keywords alone do not trigger; a known vendor's normal invoice must not alarm
why: the common case must stay quiet
failure_looks_like: an escalation or a fraud label on routine billing
author: claude
from: "Vendor A Billing <billing@vendor-a.example>"
to: [ap@acme.example]
subject: "Invoice INV-4102 for September"
date: 2026-10-01T11:00:00Z
attachments:
  - name: INV-4102.pdf
    generate: invoice_pdf
    vendor: Vendor A
    invoice:
      number: INV-4102
      due: "2026-10-31"
      lines: [["Support contract", 450.0], ["Printer toner", 89.5]]
expected:
  labels: {category: invoice, payment_related: true, fraud_risk: none}
  rule: fraud_weak
  safety: {must_not_hide: true}
---
Hi,

Invoice INV-4102 for September is attached. Payment to our usual account as before.

Vendor A Billing (555-0150)
