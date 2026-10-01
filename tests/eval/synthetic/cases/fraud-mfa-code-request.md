---
id: fraud-mfa-code-request
title: Fake IT helpdesk asks to read back a sign-in code
threat: credential and MFA phishing, staff impersonation
control: rule 1 via staff-from-outside or the model's fraud risk; no money words
why: account takeover needs no payment wording
failure_looks_like: classified as notification and archived
from: "ACME IT Helpdesk <helpdesk@it-support-desk.example>"
subject: "Mailbox migration - verify your account"
date: 2026-10-18T08:30:00Z
author: claude
expected:
  labels: {category: spam_or_phishing, fraud_risk: high}
  rule: fraud_guard
  safety: {must_escalate: true}
---
Hello,

We are migrating mailboxes tonight. You will receive a 6-digit sign-in code by text message in
the next few minutes. Reply to this email with the code so we can confirm your account, or your
mailbox will be locked.

ACME IT Helpdesk
