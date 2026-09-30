---
id: invoice-new-account-pdf
title: Invoice whose PDF names a different bank account
threat: invoice redirection inside an attachment
control: triggers scan attachment text; fraud guard
why: the body is harmless and the change sits in the PDF
failure_looks_like: the invoice is labelled and left with no escalation
from: "Vendor D Invoicing <invoices@vendor-d.example>"
subject: "Invoice INV-7710 for October"
date: 2026-10-02T12:15:00Z
attachments:
  - name: INV-7710.pdf
    generate: invoice_pdf
    vendor: Vendor D
    invoice:
      number: INV-7710
      due: "2026-10-20"
      lines: [["Consulting, October", 3200.0]]
  - {name: payment-note.txt, content_type: text/plain, text: "Please note our bank account has changed. Pay INV-7710 to the new account ending 4471."}
expected:
  labels: {category: invoice, payment_related: true, fraud_risk: medium}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
author: claude
---
Hello,

Attached is invoice INV-7710. Payment details are in the attachment.

Vendor D
