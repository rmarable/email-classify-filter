---
id: v3-reg-court-hearing-notice
title: "County court clerk sends a notice of hearing in a civil case"
threat: "court mail missed (no regulator keyword)"
control: "regulatory rule via regulator_claim or sender_type regulator; no regulator trigger word (S3 P1)"
why: "v3.0.0 S3 trigger-less regulatory card: a court acting toward us, with a filing deadline"
failure_looks_like: "labelled notification or other and left unescalated"
review: "genuine court notice with no regulator trigger word: confirm regulator_claim, sender_type regulator for a court, and the rule; category other proposed (appointment possible for the hearing date)"
author: claude
from: "Harbor County Circuit Court Civil Clerk <civil-clerk@harbor-county-courts.test>"
subject: "Notice of hearing, case CV-2026-04418, Acme named as defendant"
date: 2026-10-15T15:25:00Z
expected:
  labels: {category: appointment, sender_type: regulator, priority: high, requires_action: true, requires_reply: false, payment_related: false, deadline_mentioned: true, fraud_risk: low}
  rule: regulatory
  safety: {must_escalate: true, must_not_hide: true}
---
You are receiving this notice because Acme has been named as defendant in civil case CV-2026-04418. The summons and complaint were served on your registered agent on 9 October 2026.

The court has set an initial case-management hearing for 17 November 2026 at 9:30 in Courtroom 4B of the Harbor County Courthouse. A written answer must be filed with this office within 21 days of service. If no answer is filed and no one appears for the defendant, the court may enter a default judgment.

Documents may be filed through the court's electronic filing portal or in person at the civil clerk's window. For questions about filing procedure only, call 555-0123. This office cannot give legal advice.

Civil Clerk's Office
Harbor County Circuit Court
