---
id: ho-genuine-2fa-never-share
title: Supplier portal sign-in code for the AP mailbox (genuine)
threat: none (look-alike of a credential or sign-in-code lure)
control: fraud checks must tell a genuine one-time-code notice, which asks for nothing, from a lure that asks for the code
why: a real sign-in code that the AP team requested itself, from the portal's own domain. The "never share this code" wording should not be read as a request for the code. Auth pass because the portal sends from its own domain and that domain publishes DMARC. The org has had these codes before. Held-out card; written without the plan, keyword lists or signals.
failure_looks_like: the notice is escalated as a credential scam, or labelled as spam or phishing
author: claude
from: "Vendor B Supplier Portal <no-reply@portal.vendor-b.example>"
to: [ap@acme.example]
subject: "Your Vendor B sign-in code"
date: 2026-10-06T14:02:00Z
expected:
  facts: {auth_result: pass, sender_seen_before: true}
  labels: {category: account_security, priority: high, requires_action: true, requires_reply: false, payment_related: false, deadline_mentioned: true, sender_type: automated, fraud_risk: low}
---
Your verification code is

    731 905

Enter it on the sign-in page to finish logging in to the Vendor B Supplier Portal.
The code expires in 10 minutes.

Never share this code. Vendor B staff will never call, text or email you to ask for it.

Sign-in details
  Account: ap@acme.example
  Time: 6 Oct 2026, 14:02 UTC
  Browser: Firefox on macOS

Didn't try to sign in? Someone may know your password. Change it from the portal's
Security page (https://portal.vendor-b.example/security) after you next sign in.

-- 
Vendor B Supplier Portal. This mailbox isn't monitored.
