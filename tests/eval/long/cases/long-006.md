---
id: long-006
title: "Long card long-006"
threat: "friend-platform: scam ask early (control)"
control: "the classifier's fraud_risk on the body alone (rules alone don't reach fraud_guard)"
why: "window tuning step 4 (plan draft 8)"
failure_looks_like: "fraud_risk none, not escalated"
review: "a long email whose scam ask sits early; confirm the labels fit the whole email"
author: claude
profile: freemail
from: "Sam Ortega <sam-ortega@freemail.example>"
subject: "A proper catch-up"
date: 2026-10-22T12:55:00Z
signal: "put whatever you can into quickgrow-returns.example"
target_chars: 4225
pad_voice: personal
signature: |
  Sam
quoted_thread:
  - from: "\"Sam Ortega <sam-ortega@freemail.example>\""
    date: 2026-10-15T10:00:00Z
    body: "We went to the farmers' market on Saturday and came home with far too much cheese. The bread stall was excellent too, and the man there remembered us from last time.\n\nThe kittens next door have discovered our garden. They chase leaves across the lawn every afternoon, and I confess I spend far too long watching them from the window.\n\nWe finally finished painting the spare room. It took far longer than planned, mostly because we changed our minds about the colour twice, but it looks much brighter now.\n\nThe repairs to the roof are done at last. The builders were tidy and quick once they started, and we no longer need the bucket in the attic when it rains."
  - from: "\"Sam Ortega <sam-ortega@freemail.example>\""
    date: 2026-10-14T10:00:00Z
    body: "I finally got round to framing the map you gave us. It is hanging in the hall now, and visitors always stop to look for the places they know.\n\nThe book you lent me was wonderful. I stayed up far too late finishing it, and I keep thinking about the ending; we should talk about it next time we meet.\n\nThe choir concert went well. We were nervous about the new piece, but it came together on the night, and the church was nearly full, which was lovely to see.\n\nOur holiday photos are finally sorted into albums. It brought back lovely memories, and we laughed at the one of you trying to put up the tent in the wind."
  - from: "\"Sam Ortega <sam-ortega@freemail.example>\""
    date: 2026-10-13T10:00:00Z
    body: "We had a power cut on Tuesday evening and ended up playing cards by candlelight. It was rather fun, though the children were less keen once they realised the internet was off.\n\nThe swimming lessons are going well. Our youngest can now manage a length without the float, and she is very proud of the certificate she brought home.\n\nWe are thinking of a short trip to the coast next month. Nothing fancy, just a couple of nights somewhere quiet with a good walk and a decent place for fish and chips.\n\nDad has been busy in his workshop building a bird table. It is a little lopsided, but the robins do not seem to mind, and he watches them from the kitchen every morning."
expected:
  labels: {category: private, priority: high, requires_action: false, requires_reply: false, payment_related: true, deadline_mentioned: true, sender_type: friend, fraud_risk: high, phishing: true, private_individual: true}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
---
Hi! I've owed you a proper letter for months, so here goes.

One more thing, and I'm only telling close friends: put whatever you can into quickgrow-returns.example this week. Mine doubled in two weeks. Don't mention it to the family until it pays out, they'd only worry.

{{pad}}

Love to everyone.
