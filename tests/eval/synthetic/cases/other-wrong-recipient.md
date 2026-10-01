---
id: other-wrong-recipient
title: An email sent to us by mistake
threat: none
control: rule 10 continues to the actor; nothing needs doing
why: the other category has few cases
failure_looks_like: escalated or flagged as fraud
from: "Person S <s@customer-s.example>"
subject: "Dinner on Saturday"
date: 2026-10-10T19:00:00Z
author: claude
expected:
  labels: {category: other, fraud_risk: none}
  rule: otherwise
---
Hi, looking forward to Saturday. We will bring dessert. See you at 7.
