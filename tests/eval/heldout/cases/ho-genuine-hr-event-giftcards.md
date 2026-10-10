---
id: ho-genuine-hr-event-giftcards
title: People team thank-you mentions gift cards handed out at the staff picnic (genuine)
threat: none (look-alike of a gift-card request from HR or an executive)
control: a mention of gift cards that were already given out, with nothing asked, must not be read as a gift-card request
why: "a staff thank-you sent through Acme's HR platform (external, because the eval can't hold genuine internal mail). It mentions physical gift cards given at an event and asks no one to buy, send or reveal anything. Auth pass: the platform's own domain, which publishes DMARC. The org gets regular mail from this platform. Held-out card; written without the plan, keyword lists or signals."
failure_looks_like: escalated as a gift-card scam
author: claude
from: "Acme People Team via PeopleHub <notifications@peoplehub.example>"
to: [ap@acme.example]
subject: "Thank you for a great Harvest Picnic!"
date: 2026-09-29T08:30:00Z
expected:
  facts: {auth_result: pass, sender_seen_before: true}
  labels: {category: notification, priority: low, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: true, sender_type: unknown, fraud_risk: low}
---
Hi everyone,

What a day! Thanks to the 120-odd of you who came out to the Harvest Picnic on
Friday, and a special thank-you to the facilities crew who had the tents up by 8.

A few notes:

* Raffle winners got their prizes on the day. Every attendee also went home with a
  $20 Coffee Corner gift card from the welcome table. If you signed in but somehow
  missed yours, stop by the People desk on the ground floor this week. They're
  plastic cards in envelopes with your name on, so there's nothing to claim online.
* Lost property: two water bottles, one blue fleece and a very nice cake stand.
  Also at the People desk.
* Photos are up on the intranet under Events.

We'd love your feedback. The two-minute survey is in PeopleHub under My Tasks and
closes on 10 October.

See you at the winter party!

The People Team

--
Sent through PeopleHub on behalf of Acme. Manage notifications in your PeopleHub
profile.
