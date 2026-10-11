---
id: h2-crypto-scam-extortion-wallet
title: Extortion email claims to have compromising recordings and demands bitcoin to a wallet
threat: extortion / crypto ransom demand
control: fraud triggers (crypto payment demand to a wallet, first-time sender, unauthenticated); classifier fraud_risk
why: "The bulk 'I recorded you through your webcam' extortion pattern, paraphrased: a threat, a deadline and a bitcoin amount to a wallet, in clumsy English with no links. Authentication set to none with sender_seen_before false: these runs go out from throwaway or freshly registered domains that plausibly publish no DMARC, and the sender has never written before. I did not spoof the recipient's own address, so the crypto demand is what has to carry the case. Held-out batch 2 card; written without the plan, keyword lists or signals."
failure_looks_like: the email is hidden, archived as spam without escalation, or given fraud_risk below high
review: "sender_type judgement call: person (a stranger) versus unknown. Expected rule not set; compute with ecf rules test."
author: claude
profile: freemail
from: "Your Friend <watcher@quietpost-relay.example>"
to: [pat-lee@freemail.example]
subject: "I know what you did. Read before deleting"
date: 2026-10-07T03:22:00Z
expected:
  labels: {category: spam_or_phishing, priority: high, requires_action: true, requires_reply: true, payment_related: true, deadline_mentioned: true, sender_type: unknown, fraud_risk: high}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
  facts: {auth_result: none, sender_seen_before: false}
---
Hello Pat.

I will be short. Some months ago you opened a link on a site you should not be visiting, and my software got onto your laptop. Since then I have access to your camera, your contacts and your messages.

I have video of you. You know which kind. Next to it, a recording of what was on your screen at the same time.

I am not interested to ruin your life. I just want to be paid for my work and go away.

Send 1450 USD in Bitcoin to this wallet:

TEST-WALLET-0733

(copy it exactly, it is case sensitive)

You have 48 hours from opening this message. I can see when you opened it. If payment arrives, everything is deleted and you never hear from me. If not, the video goes to everyone in your contact list, family and work first.

Do not reply, this address is not monitored. Do not go to police, I will know and I will send immediately. Changing passwords will not help, I am already inside.

Don't know how to buy Bitcoin? Search it, any exchange works, it takes 10 minutes.

The clock is running.
