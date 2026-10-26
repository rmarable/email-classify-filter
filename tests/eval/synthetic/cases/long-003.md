---
id: long-003
title: "Long card long-003"
threat: "friend-platform: scam ask after 1,500 (text)"
control: "the classifier's fraud_risk on the body alone (rules alone don't reach fraud_guard)"
why: "window tuning step 4 (plan draft 8)"
failure_looks_like: "fraud_risk none, not escalated"
review: "a long email whose scam ask sits after 1,500; confirm the labels fit the whole email"
author: claude
profile: freemail
from: "Sam Ortega <sam-ortega@freemail.example>"
subject: "A proper catch-up"
date: 2026-10-22T11:24:00Z
signal: "put whatever you can into quickgrow-returns.example"
target_chars: 4225
pad_voice: personal
signature: |
  Sam
quoted_thread:
  - from: "\"Sam Ortega <sam-ortega@freemail.example>\""
    date: 2026-10-15T10:00:00Z
    body: "The photos from the wedding came out beautifully. I will put a few in the post for you, as I know you prefer prints to looking at them on a screen.\n\nThe allotment has done well this year. We have more courgettes than we know what to do with, so do let me know if you would like some when we next see you.\n\nThe school play was a great success. The children had clearly practised for weeks, and the costumes were remarkable considering most of them were made from old curtains.\n\nThe street party for the jubilee is being planned already. Someone suggested bunting all the way down the road, so I have offered to help sew it over the winter."
  - from: "\"Sam Ortega <sam-ortega@freemail.example>\""
    date: 2026-10-14T10:00:00Z
    body: "It was good to hear your news on the phone. I am glad the new job is going well, and that the journey is shorter than you expected once you found the right train.\n\nWe have been clearing out the loft and found a box of old letters from Grandma. Some of them are very funny, and I will bring a few along for you to read.\n\nThe kittens next door have discovered our garden. They chase leaves across the lawn every afternoon, and I confess I spend far too long watching them from the window.\n\nThe children are back at school and the house is very quiet. I have been catching up on the jobs I kept putting off over the summer, starting with the cupboard under the stairs."
  - from: "\"Sam Ortega <sam-ortega@freemail.example>\""
    date: 2026-10-13T10:00:00Z
    body: "The repairs to the roof are done at last. The builders were tidy and quick once they started, and we no longer need the bucket in the attic when it rains.\n\nI have joined a walking group that meets on Sunday mornings. They are a friendly lot, and we always end up at the same cafe for tea and a slice of cake afterwards.\n\nThe book you lent me was wonderful. I stayed up far too late finishing it, and I keep thinking about the ending; we should talk about it next time we meet.\n\nThank you again for the recipe. I made the soup on Sunday and everyone went back for seconds, even my brother, who usually refuses anything with lentils in it."
expected:
  labels: {category: private, priority: high, requires_action: false, requires_reply: false, payment_related: true, deadline_mentioned: true, sender_type: friend, fraud_risk: high, phishing: true, private_individual: true}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
---
Hi! I've owed you a proper letter for months, so here goes.

{{pad}}

One more thing, and I'm only telling close friends: put whatever you can into quickgrow-returns.example this week. Mine doubled in two weeks. Don't mention it to the family until it pays out, they'd only worry.

Love to everyone.
