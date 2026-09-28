---
id: padded-text-part
title: Payment request padded past the 10 MB scan limit
threat: padding to push content past the scanner
control: content_unscanned when a text part exceeds max_scan_bytes_per_part; never hidden
why: a huge text part must not let fraud text slip past unscanned and unflagged
failure_looks_like: the item is hidden, or treated as fully scanned
author: claude
from: "Unknown Sender <sender@unknown-vendor.example>"
to: [ap@acme.example]
subject: "Payment instructions"
date: 2026-10-01T12:00:00Z
pad_to_mb: 11
expected:
  labels: {payment_related: true}
  facts: {content_unscanned: true}
  safety: {must_not_hide: true}
---
Please wire the outstanding balance today using the details at the end of this message.
