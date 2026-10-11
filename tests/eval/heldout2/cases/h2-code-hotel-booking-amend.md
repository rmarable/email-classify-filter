---
id: h2-code-hotel-booking-amend
title: Hotel asks for the booking confirmation code to move a reservation
threat: none (benign control; vendor asks for its own booking code in reply to our request)
control: credential-request reading must not treat a booking confirmation code as a sign-in code
why: We asked the hotel to move a stay by a day. Their reservations desk replies that it can't find the booking from the sending address and asks for the confirmation code and the lead guest's surname. It also tells us not to send card details by email. Authentication set to pass with sender_seen_before true, because this is genuine mail from the hotel's own domain, which earlier sent the booking confirmation. Held-out batch 2 card; written without the plan, keyword lists or signals.
failure_looks_like: the email is labelled spam_or_phishing or account_security, or escalated as a credential request
review: fraud_risk none vs low; it asks for data (a booking code), but only its own code and only in answer to our request
author: claude
from: "Harbourview Hotel Reservations <reservations@harbourview-hotel.example>"
to: [travel@acme.example]
subject: "RE: Change of dates - two guests, late October"
date: 2026-10-06T14:05:00Z
expected:
  labels: {category: travel, priority: medium, requires_action: true, requires_reply: true, payment_related: false, deadline_mentioned: true, sender_type: vendor, fraud_risk: low}
  facts: {auth_result: pass, sender_seen_before: true}
---
Dear Guest,

Thank you for your message. We are happy to move your stay from 21-23 October to 22-24 October. At the moment we still have a King Deluxe available for both nights at the same rate, but we can only hold it once the change has been made.

Because your email came from a different address from the one on the reservation, we are not able to locate the booking by name alone. Could you please reply with the six-character confirmation code from your original booking confirmation (it begins with HV and appears near the top of that email), together with the surname of the lead guest? As soon as we have it we will amend the reservation and send you an updated confirmation.

For your security, please do not send card details by email. If anything about the rate changes we will ask you to call the front desk on 555-0164 instead.

Kind regards,

Reservations Team
Harbourview Hotel

> Hello, could we push our booking back by one night, same room type?
> Arriving the 22nd now and leaving the 24th.
> Thanks, Avery (ACME travel desk)
