---
id: v3-de-invoice
title: "Invoice in German from a known vendor"
threat: "none (control)"
control: "rule 4 labels an invoice from a known, authenticated sender"
why: "v3.0.0 S1 coverage card"
failure_looks_like: "escalated as fraud"
author: claude
from: "Buchhaltung <buchhaltung@lieferant-c.example>"
subject: "Rechnung RE-2026-118"
date: 2026-10-07T09:00:00Z
expected:
  labels: {category: invoice, sender_type: vendor, priority: medium, requires_action: true, requires_reply: false, payment_related: true, deadline_mentioned: true, fraud_risk: none}
  facts: {auth_result: pass, sender_seen_before: true, bulk_corroborates: false}
  rule: invoice
---
Sehr geehrte Damen und Herren,

anbei erhalten Sie unsere Rechnung RE-2026-118 ueber 1.240,00 EUR, zahlbar bis zum 2026-11-06 auf das bekannte Konto.

Mit freundlichen Gruessen
