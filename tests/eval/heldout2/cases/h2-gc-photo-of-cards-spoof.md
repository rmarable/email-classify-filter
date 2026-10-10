---
id: h2-gc-photo-of-cards-spoof
title: Spoofed office manager asks for photos of the purchased cards
threat: internal spoof / gift-card scam collecting photos of card backs rather than typed codes
control: fraud triggers (gift-card request, failed authentication on own domain, reply-to mismatch)
why: >-
  The From is the real office manager's acme.example address, forged; replies go to a
  free-mail address. It asks for clear photos of the front and back of each card, not typed
  codes. Authentication set to fail: this is a spoof of a known internal sender on the
  organisation's own domain, so DMARC fails; the address itself has been seen before.
  Held-out batch 2 card; written without the plan, keyword lists or signals.
failure_looks_like: the email passes as an internal request because the address is familiar
review: expected rule fraud_guard is assumed, not computed; confirm with ecf rules test
author: claude
from: "Morgan Hale <morgan-hale@acme.example>"
reply_to: "morgan-hale-acme@freemail.example"
to: [ap@acme.example]
subject: "Need help with something today"
date: 2026-10-09T11:35:00Z
expected:
  labels: {category: spam_or_phishing, priority: high, requires_action: true, requires_reply: true, payment_related: true, deadline_mentioned: true, sender_type: team, fraud_risk: high}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
  facts: {auth_result: fail, reply_to_mismatch: true, sender_seen_before: true}
---
Hey,

Are you around? I'm offsite at the vendor walkthrough and need a hand.

We're doing appreciation packs for the cleaning crew this afternoon and I completely forgot to grab the cards. Can you stop by a store on your lunch and get 5 of the $100 ones? Electronics store or the big online one, either works.

When you've got them, lay them out and take clear photos of the back of each one with the silver strip scratched off so the numbers are readable, plus one of the front. Send the pictures straight back to me here and I'll print them into the cards. Keep the receipt and I'll get you reimbursed through petty cash tomorrow.

Bit of a rush, they finish at 3. Thanks a million!

Morgan
