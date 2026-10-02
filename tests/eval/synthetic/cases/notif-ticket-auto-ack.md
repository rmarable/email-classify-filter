---
id: notif-ticket-auto-ack
title: Vendor support desk acknowledges ACME's ticket automatically
threat: none
control: rule 8
why: auto-acknowledgements look like replies from people
failure_looks_like: classified as customer request needing a reply
from: "Vendor Support <support@crm-vendor.example>"
subject: "[Ticket 88213] We received your request"
date: 2026-11-12T10:00:00Z
author: claude
expected:
  labels: {category: notification, sender_type: automated, requires_reply: false}
  rule: automated_notification
---
Thanks for contacting support. Your request has been received as ticket 88213. An agent will
reply within one business day. Please do not reply to this message.
