---
id: h2-gc-hr-staff-rewards-lookalike
title: Outside lookalike "People Team" asks AP to buy cards for staff rewards
threat: HR impersonation / bulk gift-card purchase for fake recognition scheme
control: fraud triggers (gift-card purchase request, lookalike of the organisation's domain)
why: >-
  A lookalike of Acme's HR team asks AP to buy a batch of cards for quarter-end staff awards
  and send the codes to a coordinator. Authentication set to pass: acme-staffrewards.example
  is an attacker-owned lookalike that signs its own mail; it has never written before.
  Held-out batch 2 card; written without the plan, keyword lists or signals.
failure_looks_like: the email reads as a routine internal HR purchase and is not escalated
review: expected rule fraud_guard is assumed, not computed; confirm with ecf rules test
author: claude
from: "Acme People Team <people-team@acme-staffrewards.example>"
to: [ap@acme.example]
subject: "Q3 Shout-Out Awards - card purchase needed by Friday"
date: 2026-10-05T08:20:00Z
expected:
  labels: {category: spam_or_phishing, priority: high, requires_action: true, requires_reply: true, payment_related: true, deadline_mentioned: true, sender_type: team, fraud_risk: high}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
  facts: {auth_result: pass, sender_seen_before: false}
---
Hello AP team,

As part of the Q3 Shout-Out Awards, leadership approved a $50 thank-you for each of the 28 nominees this quarter. The rewards platform we normally use is being migrated, so this round we're doing it the old-fashioned way.

Could you please purchase 28 x $50 digital cards from any major retailer (mixing brands is fine, people have different preferences) and charge them to cost centre 4100 - People & Culture? Total $1,400.

Once purchased, please reply to this email with a simple list of the codes and PINs (or a spreadsheet if easier). Our awards coordinator will match them to nominees and send them out with the certificates on Friday. Please don't distribute them yourselves - we want the announcement to come from leadership.

Thanks for helping make this happen!

People & Culture
Acme
