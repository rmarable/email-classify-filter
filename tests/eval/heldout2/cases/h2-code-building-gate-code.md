---
id: h2-code-building-gate-code
title: Building manager shares the new gate and lobby door code
profile: freemail
threat: none (benign control; a genuine notice that contains an access code)
control: fraud triggers and credential-request reading must not fire on a code that is shared, not asked for
why: A residential building's management office tells residents the new keypad code and asks them not to share it. The code is given to us, not asked for. Authentication set to pass with sender_seen_before true, because this is a regular sender writing from its own domain. Held-out batch 2 card; written without the plan, keyword lists or signals.
failure_looks_like: the email is labelled spam_or_phishing or account_security, or given any fraud risk
author: claude
from: "Maple Court Management <office@maplecourt-mgmt.example>"
subject: "New gate and lobby door code from Monday 13 October"
date: 2026-10-09T16:30:00Z
expected:
  labels: {category: notification, priority: high, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: true, sender_type: vendor, fraud_risk: none}
  facts: {auth_result: pass, sender_seen_before: true}
---
Dear residents,

As mentioned at the last residents' meeting, we're changing the keypad codes now that the contractors have finished. From Monday 13 October the code for the main gate on Elm Lane and for the lobby door will be:

    4826 then #

The old code stops working at 08:00 that morning. The bin store and the bike room keep their current code for now; we'll change those in November and let you know.

Please don't give the new code to delivery drivers or post it in the residents' group chat. During office hours couriers can use the call button for the office, and parcels can still go in the parcel room as usual.

If you have a carer, cleaner or anyone else who needs regular access, reply to this email with their name and we'll arrange a fob for them rather than passing on the code.

Thanks for your patience during the works.

Maple Court Management Office
555-0118 (weekdays 9 to 5)
