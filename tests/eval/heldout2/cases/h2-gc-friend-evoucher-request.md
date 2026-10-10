---
id: h2-gc-friend-evoucher-request
title: Friend impostor on a new free-mail address asks for e-vouchers
threat: friend-in-need impersonation / prepaid e-voucher codes (no "gift card" wording)
control: fraud triggers (voucher/e-code request, known contact's name on an unknown address)
why: >-
  A new free-mail address using a listed friend's name says their bank card is blocked and asks
  for digital store vouchers for a niece's birthday, codes by reply. It never says "gift card".
  Authentication set to pass: the impostor owns this free-mail account and the provider signs
  it; the address has not been seen before (the real friend uses another address).
  Held-out batch 2 card; written without the plan, keyword lists or signals.
failure_looks_like: the email is labelled personal mail from a friend and not escalated
review: >-
  expected rule fraud_guard is assumed, not computed; confirm with ecf rules test. Without a
  gift-card term, the payment trigger depends on voucher/e-code wording.
author: claude
profile: freemail
from: "Sam Rivera <sam-rivera-mobile@freemail.example>"
subject: "can you do me a tiny favour?"
date: 2026-10-04T19:58:00Z
expected:
  labels: {category: spam_or_phishing, priority: high, requires_action: true, requires_reply: true, payment_related: true, deadline_mentioned: true, sender_type: friend, fraud_risk: high}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
  facts: {auth_result: pass, sender_seen_before: false}
---
Hi Pat,

sorry to bother you on a Sunday. Using this address because I'm locked out of my usual one, long story.

It's my niece's birthday tomorrow and I promised I'd sort her present, but my bank has frozen my card after some weird charge and I can't buy anything online till they call me back Tuesday. She only wants e-vouchers for that online game store (the digital top-up ones, $50 each, she wants 4).

Could you buy them and just reply with the codes? They come instantly by email when you buy them on the store's site. I'll pay you back first thing Tuesday, promise, or I'll owe you dinner.

Don't call, I'm at the hospital visiting my dad and phones have to be off on the ward. Email is easiest.

Thank you thank you
Sam x
