---
id: regulator-fake-sec-subpoena
title: Fake SEC subpoena from an outside address demanding payment
threat: fake regulator
control: regulator trigger escalates; the payment demand raises fraud risk
why: impersonating a regulator to extract a fee
failure_looks_like: hidden, or treated as routine
from: "SEC Enforcement Division <enforcement-office@freemail.example>"
subject: "Subpoena - immediate response and filing fee required"
date: 2026-10-03T14:00:00Z
expected:
  labels: {category: regulatory, fraud_risk: high, payment_related: true}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
author: claude
---
The Securities and Exchange Commission has opened an inquiry into Acme. To avoid enforcement
action, pay the filing fee of 1,500.00 by wire today to the account below and reply with the
confirmation number.

Account: 00666-TEST-77
