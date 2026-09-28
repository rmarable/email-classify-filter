---
id: scanned-invoice-17mb
title: Scanned invoice just over the 16 MB standard limit
threat: oversized message (DKIM cannot be checked; content not fully read)
control: max_message_bytes on standard addresses; auth_result none; content_unscanned
why: large scanned invoices are normal but must not be trusted or hidden
failure_looks_like: auth_result pass, or the item is hidden
author: claude
from: "Vendor B Accounts <accounts@vendor-b.example>"
to: [billing@acme.example]
subject: "Scanned invoice INV-5501"
date: 2026-10-01T13:00:00Z
encoding: base64
attachments:
  - name: INV-5501-scan.pdf
    generate: invoice_pdf
    vendor: Vendor B
    pages: 2
    scanned: true
    target_eml_mb: 17
    invoice: {number: INV-5501, due: "2026-11-01"}
expected:
  labels: {category: invoice, payment_related: true}
  facts: {auth_result: none, content_unscanned: true}
  safety: {must_not_hide: true}
---
Scanned copy of invoice INV-5501 attached.
