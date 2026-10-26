---
id: long-058
title: "Long card long-058"
threat: "cousin-card-text: scam ask after 1,500 (sig)"
control: "the classifier's fraud_risk on the body alone"
why: "window tuning step 4 (plan draft 8)"
failure_looks_like: "fraud_risk none, not escalated"
review: "a long email whose scam ask sits after 1,500; confirm the labels fit the whole email"
author: claude
profile: freemail
from: "Riley Marsh <riley-marsh@freemail.example>"
subject: "Hello from your cousin"
date: 2026-10-20T09:00:00Z
signal: "text me your card's number and the three digits on the back to 555-0183"
target_chars: 1625
pad_voice: personal
signature: |
  Riley

  PS: I'm stuck at the airport and my card won't work. Could you text me your card's number and the three digits on the back to 555-0183? I'll sort it out with you when I'm home, and please don't worry Auntie with this.
expected:
  labels: {category: private, priority: high, requires_action: false, requires_reply: false, payment_related: true, deadline_mentioned: false, sender_type: family, fraud_risk: high, phishing: true, private_individual: true}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
---
Hello cousin! It's been ages, so here's a proper catch-up from me.

{{pad}}

Lots of love.
