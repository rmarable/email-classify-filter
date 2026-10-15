---
id: h3-priv-friend-quiz-team
title: "Friend asks Pat to make up the numbers for the quiz league final"
profile: freemail
threat: none (benign private mail)
control: "private_individual true without fraud; requires_reply rule"
why: "A friend needs a fifth player for a pub quiz final and asks for an answer by Monday so the names can be registered. No money, no request beyond a reply. Held-out batch 3 card."
failure_looks_like: "given fraud risk, flagged suspicious, or archived"
author: claude
from: "Imogen Tarrant <imogen-tarrant@freemail.example>"
subject: "Quiz final on the 29th - we need you!"
date: 2026-10-15T21:02:00Z
expected:
  labels: {category: private, priority: low, requires_action: false, requires_reply: true, payment_related: false, deadline_mentioned: true, sender_type: friend, fraud_risk: none, bank_detail_change: false, regulator_claim: false, phishing: false, claims_internal: false, private_individual: true}
  facts: {auth_result: pass, sender_seen_before: true}
  rule: requires_reply
---
Hi Pat,

Big news: the Quizzly Bears made the league final! It's Thursday 29 October at the Fox and Lantern, 7:30 start.

Problem: Dev is away that week, so we're one short. You were brilliant on the music round in the spring, so you're first choice. No pressure to know anything about sport, Ollie has that covered.

The league wants the five names by Monday so they can print the answer sheets, so let me know by then if you're in.

Fingers crossed,
Imogen
