---
id: long-051
title: "Long card long-051"
threat: "friend-relief-site: scam ask after 1,500 (quote)"
control: "the classifier's fraud_risk on the body alone"
why: "window tuning step 4 (plan draft 8)"
failure_looks_like: "fraud_risk none, not escalated"
review: "a long email whose scam ask sits after 1,500; confirm the labels fit the whole email"
author: claude
profile: freemail
from: "Jess Monroe <jess-monroe@freemail.example>"
subject: "Re: something I wanted to ask"
date: 2026-10-21T16:07:00Z
signal: "put something into helpnow-relief.example tonight"
target_chars: 4200
pad_voice: personal
signature: |
  Jess
quoted_thread:
  - from: "\"Jess Monroe <jess-monroe@freemail.example>\""
    date: 2026-10-18T09:00:00Z
    body: "The flood relief I mentioned needs money now. Could you put something into helpnow-relief.example tonight? It isn't on the news yet, so please don't share it around; they want to stay small.\n\nLet me know if anything is unclear."
  - from: "\"Jess Monroe <jess-monroe@freemail.example>\""
    date: 2026-10-15T10:00:00Z
    body: "Dad has been busy in his workshop building a bird table. It is a little lopsided, but the robins do not seem to mind, and he watches them from the kitchen every morning.\n\nI have joined a walking group that meets on Sunday mornings. They are a friendly lot, and we always end up at the same cafe for tea and a slice of cake afterwards.\n\nThe school play was a great success. The children had clearly practised for weeks, and the costumes were remarkable considering most of them were made from old curtains.\n\nThe neighbours' son has been practising the trumpet. He is improving, slowly, and I have started to recognise the tune, which I think is meant to be a carol."
  - from: "\"Jess Monroe <jess-monroe@freemail.example>\""
    date: 2026-10-14T10:00:00Z
    body: "My sister is expecting her second child in the spring. We are all delighted, and her eldest has already announced that he would like a brother, please.\n\nI took up watercolours again after many years. My first few attempts were muddy, but the evening class has helped, and I am quite pleased with a painting of the harbour.\n\nThe weather has finally turned. We lit the fire for the first time this season last night, and the cat has claimed the rug in front of it as her own.\n\nThank you again for the recipe. I made the soup on Sunday and everyone went back for seconds, even my brother, who usually refuses anything with lentils in it."
  - from: "\"Jess Monroe <jess-monroe@freemail.example>\""
    date: 2026-10-13T10:00:00Z
    body: "It was good to hear your news on the phone. I am glad the new job is going well, and that the journey is shorter than you expected once you found the right train.\n\nWe drove up to the lakes for a day last week. The leaves were just starting to change, and we had a picnic by the water before the clouds came in.\n\nThe village fete is next Saturday. There will be the usual cake stall and dog show, and I have somehow been talked into helping with the tombola.\n\nWe went to the farmers' market on Saturday and came home with far too much cheese. The bread stall was excellent too, and the man there remembered us from last time."
expected:
  labels: {category: private, priority: high, requires_action: false, requires_reply: false, payment_related: true, deadline_mentioned: true, sender_type: friend, fraud_risk: high, phishing: true, private_individual: true}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
---
Did you get a chance to read my note below?

{{pad}}

Thank you, really.
