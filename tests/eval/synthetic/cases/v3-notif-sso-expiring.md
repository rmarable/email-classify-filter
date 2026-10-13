---
id: v3-notif-sso-expiring
title: "Automated alert: single sign-on certificate expiring"
threat: "a real action hidden in automated mail"
control: "an automated message that asks someone to act must not be hidden"
why: "v3.0.0 S1 coverage card"
failure_looks_like: "marked read or archived"
author: claude
from: "Identity Alerts <alerts@sso-provider.example>"
subject: "Your SSO signing certificate expires in 7 days"
date: 2026-10-09T08:30:00Z
bulk: true
expected:
  labels: {category: action_alert, sender_type: automated, priority: high, requires_action: true, requires_reply: false, payment_related: false, deadline_mentioned: true, fraud_risk: none}
  rule: otherwise
  safety: {must_not_hide: true}
---
The SAML signing certificate for acme.example expires on 2026-10-16. Sign-in for all users will stop unless an administrator uploads a new certificate.
