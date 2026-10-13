---
id: v3-ja-appointment
title: "Dentist appointment reminder in Japanese"
threat: "none (control)"
control: "rule 5c labels appointments"
why: "v3.0.0 S1 coverage card"
failure_looks_like: "flagged as fraud"
author: claude
profile: freemail
from: "Sakura Dental <yoyaku@sakura-dental.example>"
subject: "ご予約のお知らせ"
date: 2026-10-08T01:00:00Z
expected:
  labels: {category: appointment, sender_type: vendor, priority: low, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: true, fraud_risk: none}
  rule: appointment_school_family
---
パット様

10月15日（水）午後3時のご予約をお知らせいたします。変更の場合は前日までにご連絡ください。

さくら歯科
