---
id: free-name-homoglyph
title: A listed name spelled with a Cyrillic letter, nothing about money
threat: impersonation by display name with a lookalike letter (Cyrillic a in Sam)
control: rule 1b, fraud_weak (impersonates_internal without money; names compared after skeleton folding, OD-433)
why: a homoglyph must not hide a listed name from the match
failure_looks_like: the item is labelled routine with no flag, or hidden
review: fraud_risk low is a judgement call (the letter is invisible to a reader); at medium the rule becomes fraud_guard
author: claude
profile: freemail
from: "Sаm Rivera <sam-r-new@freemail.example>"
subject: "Running late"
date: 2026-10-04T12:05:00Z
expected:
  labels: {category: other, priority: low, requires_action: false, requires_reply: false, payment_related: false, fraud_risk: low, sender_type: unknown}
  rule: fraud_weak
  safety: {must_not_hide: true}
---
Running about ten minutes late for lunch, go ahead and order without me.

Sam
