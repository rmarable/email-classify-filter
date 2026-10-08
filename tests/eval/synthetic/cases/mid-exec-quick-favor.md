---
id: mid-exec-quick-favor
title: Message claiming to be an executive asks if the reader is at their desk
threat: medium (the opening message of a gift-card or wire scam, with no request yet)
control: rule 1's bec_opener clause (two opener phrases from an outside sender, OD-479) escalates it
  with no money words; a medium fraud risk from the model escalates it too
why: the first message carries no money words, so no money clause or trigger can see it
failure_looks_like: classified as other and left
from: "Managing Director <md-acme-office@freemail.example>"
subject: "Quick favour"
date: 2026-11-13T11:45:00Z
review: Escalate, as spam_or_phishing? Preview run - category expected spam_or_phishing, model other.
author: claude
expected:
  labels: {category: spam_or_phishing, sender_type: team, fraud_risk: medium}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
---
Are you at your desk? I need a quick favour handled discreetly. I'm in meetings all day, so
email only. Reply as soon as you see this.

Sent from my phone
