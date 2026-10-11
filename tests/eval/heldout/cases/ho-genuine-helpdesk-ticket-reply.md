---
id: ho-genuine-helpdesk-ticket-reply
title: Managed IT service desk ticket update asks to reply with the ticket number (genuine)
threat: none (look-alike of a fake helpdesk asking for a reply)
control: a reply request that carries no credential, link or money request must not be treated as credential harvesting
why: "Acme's outsourced IT provider updates a ticket the AP team opened and asks for replies to keep the ticket number. It asks for no password or code. Sent from the provider's own domain because the eval can't hold genuine internal mail. Auth pass: the provider's own domain, which publishes DMARC. The org has heard from this desk before. Held-out card; written without the plan, keyword lists or signals."
failure_looks_like: escalated as a credential or helpdesk scam
author: claude
from: "MSP-A Service Desk <servicedesk@msp-a.example>"
to: [ap@acme.example]
subject: "[Ticket #48213] Scanner on 2nd floor won't send to email - update"
date: 2026-10-07T10:41:00Z
expected:
  facts: {auth_result: pass, sender_seen_before: true}
  labels: {category: bug_report, priority: medium, requires_action: true, requires_reply: true, payment_related: false, deadline_mentioned: false, sender_type: vendor, fraud_risk: none}
---
Hi AP team,

Quick update on your ticket.

We've pushed a new scan-to-email profile to the 2nd floor scanner (the one by the
kitchen). Test scans to ap@acme.example arrived on our side, but we'd like you to
confirm from your end before we close this.

Could you send one test scan and then reply to this email with your ticket number
(#48213) in the subject line? Our system files replies by that number, so a new
email without it opens a fresh ticket and we lose the history.

If it still fails, tell us what the scanner's screen says. No need to send any
passwords; we already have admin access to the device.

Status: Waiting on customer
Priority: Normal
Engineer: Level 1 desk

Thanks,
MSP-A Service Desk
555-0118 (8am-6pm, Mon-Fri)
