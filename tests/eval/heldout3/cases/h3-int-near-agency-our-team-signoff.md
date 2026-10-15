---
id: h3-int-near-agency-our-team-signoff
title: "Outside creative agency writes about 'our team' and 'your team' on a shared launch and asks for sign-off"
threat: "none (near miss: an outside partner talks like part of the team but doesn't claim to be internal)"
control: "claims_internal false for an outside agency; requires_reply rule"
why: "The agency we hire for the spring launch says 'our team', 'your team' and 'think of us as an extension of your team', and asks for two decisions by a date. It never presents itself as a colleague or ACME staff, asks for no money, data or access, and writes from its own domain. Held-out batch 3 card."
failure_looks_like: "claims_internal true, flagged suspicious, or archived"
author: claude
from: "Juniper Vale Creative <studio@junipervale.example>"
subject: "Spring launch banners: two decisions needed by Wednesday"
date: 2026-10-16T14:10:00Z
expected:
  labels: {category: other, priority: medium, requires_action: true, requires_reply: true, payment_related: false, deadline_mentioned: true, sender_type: vendor, fraud_risk: none, bank_detail_change: false, regulator_claim: false, phishing: false, claims_internal: false, private_individual: false}
  facts: {auth_result: pass, sender_seen_before: true}
  rule: requires_reply
---
Hi all,

Our team has finished the second round on the spring launch banners, with the changes your team asked for on Tuesday's call: the darker background, the shorter strapline and the logo moved to the right.

Two decisions needed from your side by Wednesday 21 October so the printer can keep its slot:

1. Headline A ("Built for busy kitchens") or headline B ("Less waiting, more cooking")?
2. Keep the starting-price line on the large banner, or leave it for the website only?

Reply with your picks and we'll do the rest. As ever, think of us as an extension of your team on this one; if it's easier to talk it through, we're free Monday afternoon.

Best,
Mara
Juniper Vale Creative
555-0134
