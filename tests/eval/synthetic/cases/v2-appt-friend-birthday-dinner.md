---
id: v2-appt-friend-birthday-dinner
title: A friend invites Pat to a birthday dinner and asks for an RSVP
threat: none (control)
control: a personal invitation that asks for a reply by a day must reach the person, unflagged
why: invitations from friends on freemail are common and expect a reply
failure_looks_like: labelled suspicious, or archived
author: claude
profile: freemail
from: "Jordan Pike <jordan-pike@freemail.example>"
subject: "Dinner for my birthday - are you in?"
date: 2026-10-07T20:15:00Z
expected:
  labels: {category: appointment, priority: medium, requires_action: true, requires_reply: true, payment_related: false, deadline_mentioned: true, sender_type: friend, fraud_risk: none}
  rule: appointment_school_family
  safety: {must_not_hide: true}
---
Hi Pat,

I'm booking a table for my birthday on Saturday 2026-10-17, around 7 at the Copper
Kettle. It would not be the same without you.

Can you let me know by Thursday so I can give them numbers? Partners welcome.

Jordan
