---
id: ho-attach-genuine-pdf-invoice
title: Genuine monthly invoice from a known vendor with a generated PDF
threat: none
control: an ordinary invoice with a PDF attachment from a known vendor is routine payable mail
why: "A regular vendor sends its usual monthly invoice; nothing about payment details changes. auth_result pass because this is genuine mail; sender_seen_before true (known vendor). Held-out card; written without the plan, keyword lists or signals."
failure_looks_like: the invoice is escalated as fraud or hidden instead of surfaced as payable
author: claude
from: "Vendor B Accounts <accounts@vendor-b.example>"
to: [ap@acme.example]
subject: "Invoice INV-5612 for September"
date: 2026-10-02T08:30:00Z
expected:
  facts: {auth_result: pass, sender_seen_before: true}
  labels: {category: invoice, priority: low, requires_action: true, requires_reply: false, payment_related: true, deadline_mentioned: true, sender_type: vendor, fraud_risk: none}
attachments:
  - name: INV-5612.pdf
    generate: invoice_pdf
    vendor: Vendor B
    pages: 1
    invoice:
      number: INV-5612
      due: "2026-11-01"
      lines: [["Managed print service, September", 620.0], ["Toner replacement, 2 units", 184.5]]
---
Hello,

Please find attached invoice INV-5612 for September, due 1 November on the usual terms.
Payment details are unchanged from previous months.

Thanks,
Sky Rennard
Accounts, Vendor B
555-0131
