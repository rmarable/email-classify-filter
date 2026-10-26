---
id: long-084
title: "Long card long-084"
threat: "friend-birthday-party: benign long mail"
control: "nothing to flag in a long email"
why: "window tuning step 4 (plan draft 8)"
failure_looks_like: "flagged as fraud"
review: "a long email with nothing to flag; confirm the labels fit the whole email"
author: claude
profile: freemail
from: "Marco Silva <marco-silva@freemail.example>"
subject: "Birthday party"
date: 2026-10-22T11:12:00Z
target_chars: 4275
pad_voice: personal
signature: |
  Marco
quoted_thread:
  - from: "\"Marco Silva <marco-silva@freemail.example>\""
    date: 2026-10-15T10:00:00Z
    body: "The photos from the wedding came out beautifully. I will put a few in the post for you, as I know you prefer prints to looking at them on a screen.\n\nThe neighbours' son has been practising the trumpet. He is improving, slowly, and I have started to recognise the tune, which I think is meant to be a carol.\n\nThe village fete is next Saturday. There will be the usual cake stall and dog show, and I have somehow been talked into helping with the tombola.\n\nWe finally finished painting the spare room. It took far longer than planned, mostly because we changed our minds about the colour twice, but it looks much brighter now."
  - from: "\"Marco Silva <marco-silva@freemail.example>\""
    date: 2026-10-14T10:00:00Z
    body: "Thank you for the birthday card. It arrived on the day, and the picture on the front made me laugh; it looks just like the cottage we stayed in all those years ago.\n\nMy knee is much better now, thank you for asking. The physio exercises are dull but they work, and I managed the whole hill on our walk last week without stopping.\n\nThe swimming lessons are going well. Our youngest can now manage a length without the float, and she is very proud of the certificate she brought home.\n\nI have joined a walking group that meets on Sunday mornings. They are a friendly lot, and we always end up at the same cafe for tea and a slice of cake afterwards."
  - from: "\"Marco Silva <marco-silva@freemail.example>\""
    date: 2026-10-13T10:00:00Z
    body: "It was good to hear your news on the phone. I am glad the new job is going well, and that the journey is shorter than you expected once you found the right train.\n\nWe are looking forward to seeing you at the end of the month. The spare bed is made up, and I have found the board game we used to play, so be ready for a rematch.\n\nIt was lovely to catch up at the weekend. The garden looked wonderful, and I have been meaning to ask what you planted along the fence; the colours were really striking.\n\nThe choir concert went well. We were nervous about the new piece, but it came together on the night, and the church was nearly full, which was lovely to see."
expected:
  labels: {category: private, priority: low, requires_action: false, requires_reply: true, payment_related: false, deadline_mentioned: true, sender_type: friend, fraud_risk: none, phishing: false, private_individual: true}
  rule: requires_reply
  safety: {}
---
Hello! I'm turning forty next month and having a small party, and I hope you can come.

{{pad}}

It's on the Saturday after next at our house; let me know if you can make it.
