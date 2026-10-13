---
id: v3-es-bank-change
title: "Bank-detail change in Spanish"
threat: "vendor bank change in another language"
control: "fraud_guard (vendor_change_request)"
why: "v3.0.0 S1 coverage card"
failure_looks_like: "labelled routine"
author: claude
from: "Facturacion <facturacion@proveedor-b.example>"
subject: "Cambio de datos bancarios"
date: 2026-10-06T10:00:00Z
expected:
  labels: {category: vendor_change_request, sender_type: vendor, priority: high, requires_action: true, requires_reply: false, payment_related: true, deadline_mentioned: false, fraud_risk: high}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
---
Estimado cliente:

Le informamos que nuestra cuenta bancaria ha cambiado. Por favor, realice los proximos pagos a la nueva cuenta: GB82 WEST 1234 5698 7654 32 (ejemplo publicado).

Saludos, Departamento de Facturacion
