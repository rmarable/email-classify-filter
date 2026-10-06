---
id: free-shared-first-name
title: A different person who shares only a first name with a listed contact (negative control)
threat: none
control: impersonation needs every word of a listed name (OD-433); a shared first name is not enough
why: false impersonation alarms on common first names would make the flag worthless
failure_looks_like: the item is flagged or escalated as impersonation of Sam Rivera
author: claude
profile: freemail
from: "Sam Ortiz <sam-ortiz@freemail.example>"
subject: "Book club moved to Thursday"
date: 2026-10-02T17:00:00Z
expected:
  labels: {category: other, priority: low, requires_action: false, requires_reply: false, payment_related: false, fraud_risk: none, sender_type: unknown}
  rule: otherwise
---
Hi all, book club is on Thursday this month instead of Wednesday. Same time, same room
at the library. We're on chapters 4 to 7.

Sam Ortiz
