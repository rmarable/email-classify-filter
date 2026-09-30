---
id: reused-message-id
title: A second message reusing another email's Message-ID
threat: reused Message-ID
control: duplicate_message_id fact; Undo refuses ambiguous matches
why: a sender controls Message-IDs and can collide with a real one
failure_looks_like: the duplicate replaces or hides the original's record
from: "Vendor A Billing <billing@vendor-a.example>"
subject: "Invoice INV-4101 (resend)"
date: 2026-10-03T16:00:00Z
message_id: "<fraud-hidden-html-dup@synthetic.acme.example>"
expected:
  labels: {category: invoice, payment_related: true}
  safety: {must_not_hide: true}
author: claude
---
Resending invoice INV-4101 in case the first copy didn't arrive.
