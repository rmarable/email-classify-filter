---
id: partner-integration-pitch
title: A company proposes an integration partnership
threat: none
control: requires_reply continues to the actor
why: partnership proposals are a category with few cases
failure_looks_like: escalated as fraud
from: "Partner Q Alliances <alliances@partner-q.example>"
subject: "Integration partnership"
date: 2026-10-11T10:00:00Z
review: a known sender (passes DMARC, seen before) so it reaches the `actor` role, not `actor_high` (v3.0.0 H5); confirm nothing else changes
author: claude
expected:
  labels: {category: partnership, fraud_risk: none}
  rule: requires_reply
  facts: {auth_result: pass, sender_seen_before: true, bulk_corroborates: false}
---
Hello ACME team,

We build scheduling software used by about 2,000 small businesses and think an integration with
your product would help both our customers. Would you be open to a short call?

Partner Q Alliances
