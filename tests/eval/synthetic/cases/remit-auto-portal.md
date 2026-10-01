---
id: remit-auto-portal
title: Automated remittance notice from a customer's payables portal
threat: none (control)
control: rule 1b; automated and bulk-looking, but payment mail stays visible
why: automated payment mail must not be archived like a notification
failure_looks_like: archived as an automated notification
from: "Payables Portal <no-reply@payables-portal.example>"
subject: "Remittance notification for supplier ACME"
date: 2026-10-07T03:00:00Z
bulk: true
author: claude
expected:
  labels: {category: remittance, payment_related: true, fraud_risk: none}
  rule: fraud_weak
  safety: {must_not_hide: true}
---
Supplier: ACME
Payment date: 2026-10-07
Amount: 6,300.00 USD
Invoices: ACME-1090, ACME-1095

This notice was generated automatically. Do not reply.
