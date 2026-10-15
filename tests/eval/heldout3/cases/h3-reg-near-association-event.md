---
id: h3-reg-near-association-event
title: "Professional association's event mail: a state licensing board's staff speak at a breakfast briefing"
threat: "none (near miss: names a regulatory body as a speaker, does not act for one)"
control: "regulator_claim must be false; marketing rule archives it"
why: "A bulk event invitation from an association. A state licensing board's staff will explain new continuing-education rules; the board is a guest, not a sender acting toward us. Names no listed regulator or phrase. Event registration closing is a deadline, but nothing anyone must do. Held-out batch 3 card."
failure_looks_like: "regulator_claim true, sender_type regulator, or escalated as regulatory"
author: claude
from: "Mid-Market Finance Association <events@mmfa-news.example>"
subject: "Breakfast briefing, 12 November: the licensing board on new CPE rules"
date: 2026-10-15T07:00:00Z
bulk: true
expected:
  labels: {category: marketing, priority: low, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: true, sender_type: company, fraud_risk: none, bank_detail_change: false, regulator_claim: false, phishing: false, claims_internal: false, private_individual: false}
  facts: {auth_result: pass, sender_seen_before: true}
  rule: marketing
---
MMFA BREAKFAST BRIEFING

Thursday 12 November, 7:30 to 9:00
Riverside Conference Centre, Room 3

New continuing-education rules: what changes for finance staff in 2027

Two members of staff from the Brightwater State Board of Accountancy will walk through the board's revised continuing-education requirements: the new ethics hours, how carry-over works, and what records to keep. A member CFO will share how her team plans training hours across the year.

Breakfast is served from 7:15. Places are free for members and limited to 60. Registration closes on Friday 30 October or when the room is full.

Register from the Events page of your member account.

Also coming up: our year-end close roundtable in December and the spring conference in March.

You are receiving this email because you are a member of the Mid-Market Finance Association. Manage your preferences or unsubscribe from your member account.
