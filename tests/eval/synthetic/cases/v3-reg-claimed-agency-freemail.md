---
id: v3-reg-claimed-agency-freemail
title: "Claimed workplace safety officer writes from a personal address"
threat: "regulator impersonation, callback lure (no regulator keyword)"
control: "regulatory rule via regulator_claim (an impostor takes the role it claims); no regulator trigger word (S3 P1)"
why: "v3.0.0 S3 trigger-less regulatory card: a claimed agency on a free-mail address, asking only for a phone call"
failure_looks_like: "treated as routine or as plain junk and hidden"
review: "likely fake (agency on a free-mail address, urgency, call-back number) but asks for no money, data or credentials, so fraud_risk low is proposed and the rule is regulatory; confirm, or say if fraud_risk medium (then fraud_guard) fits better. phishing is set by hand in the fixed labels file, not here"
author: claude
from: "State Workplace Safety Board <wsb-compliance-officer@freemail.example>"
subject: "Employee complaint received: site visit to be arranged"
date: 2026-10-15T08:40:00Z
expected:
  labels: {category: appointment, sender_type: regulator, priority: high, requires_action: true, requires_reply: false, payment_related: false, deadline_mentioned: true, fraud_risk: low}
  rule: regulatory
  safety: {must_escalate: true, must_not_hide: true}
---
To the owner or manager,

This office has received a complaint from one of your employees about working conditions at your site. A compliance officer has been assigned to the case and must visit within the next ten business days.

To avoid an unannounced visit during working hours, the owner or manager must call the assigned officer directly at 555-0158 by the end of the day on Friday to agree a date.

Compliance Officer, Field Unit 3
State Workplace Safety Board
