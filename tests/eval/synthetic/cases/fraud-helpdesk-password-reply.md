---
id: fraud-helpdesk-password-reply
title: Outside "help desk" asks for the mailbox password by reply
threat: high (credential request; a positive for the credential_request signal)
control: fraud_risk high escalates it; credential_request is a model signal only (OD-485)
why: the password-by-reply ask is the oldest credential lure and needs no link
failure_looks_like: account_security or notification with fraud_risk low
from: "IT Help Desk <helpdesk@acme-it-support.example>"
subject: "Mailbox migration tonight"
date: 2026-11-21T16:00:00Z
author: claude
expected:
  labels: {category: spam_or_phishing, sender_type: team, fraud_risk: high}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
---
We are moving all mailboxes to the new server tonight. So that your mail moves with you,
reply with your current password before 6 pm. Mailboxes without a reply will be moved empty.

IT Help Desk
