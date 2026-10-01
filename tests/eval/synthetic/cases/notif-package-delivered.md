---
id: notif-package-delivered
title: Courier confirms a delivery
threat: none
control: rule 8
why: delivery notices are automated and routine
failure_looks_like: classified as customer request
from: "Courier Updates <updates@parcel-courier.example>"
subject: "Delivered - parcel 4471-22"
date: 2026-11-10T13:00:00Z
author: claude
expected:
  labels: {category: notification, sender_type: automated, requires_action: false}
  rule: automated_notification
---
Your parcel 4471-22 was delivered at 12:41 and left at reception.
