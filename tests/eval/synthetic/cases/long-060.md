---
id: long-060
title: "Long card long-060"
threat: "friend-wedding-invite: benign long mail"
control: "nothing to flag in a long email"
why: "window tuning step 4 (plan draft 8)"
failure_looks_like: "flagged as fraud"
review: "a long email with nothing to flag; confirm the labels fit the whole email"
author: claude
profile: freemail
from: "Ellie Grant <ellie-grant@freemail.example>"
subject: "We're getting married!"
date: 2026-10-20T11:39:00Z
target_chars: 4225
pad_voice: personal
signature: |
  Ellie
quoted_thread:
  - from: "\"Ellie Grant <ellie-grant@freemail.example>\""
    date: 2026-10-15T10:00:00Z
    body: "The neighbours' son has been practising the trumpet. He is improving, slowly, and I have started to recognise the tune, which I think is meant to be a carol.\n\nThe weather has finally turned. We lit the fire for the first time this season last night, and the cat has claimed the rug in front of it as her own.\n\nWe drove up to the lakes for a day last week. The leaves were just starting to change, and we had a picnic by the water before the clouds came in.\n\nThe dog has been enjoying the cooler mornings. We have started walking the long way round the lake, and she always stops to say hello to the ducks by the old boathouse."
  - from: "\"Ellie Grant <ellie-grant@freemail.example>\""
    date: 2026-10-14T10:00:00Z
    body: "We are looking forward to seeing you at the end of the month. The spare bed is made up, and I have found the board game we used to play, so be ready for a rematch.\n\nOur walk on Sunday took us past the old mill. They are turning it into flats, but the water wheel is being kept, which seems a nice way to remember what it was.\n\nThe allotment has done well this year. We have more courgettes than we know what to do with, so do let me know if you would like some when we next see you.\n\nThank you for the birthday card. It arrived on the day, and the picture on the front made me laugh; it looks just like the cottage we stayed in all those years ago."
  - from: "\"Ellie Grant <ellie-grant@freemail.example>\""
    date: 2026-10-13T10:00:00Z
    body: "The street party for the jubilee is being planned already. Someone suggested bunting all the way down the road, so I have offered to help sew it over the winter.\n\nThe tomatoes on the windowsill are finally ripening. It has taken all summer, but the first few were delicious, and there are plenty more still turning red.\n\nWe have been clearing out the loft and found a box of old letters from Grandma. Some of them are very funny, and I will bring a few along for you to read.\n\nThe new neighbours seem very nice. They came over with a plant when they moved in, and we have promised to have them round for dinner once they have settled."
expected:
  labels: {category: private, priority: medium, requires_action: false, requires_reply: true, payment_related: false, deadline_mentioned: true, sender_type: friend, fraud_risk: none, phishing: false, private_individual: true}
  rule: requires_reply
  safety: {}
---
We have some news: we're getting married next summer, and we'd love you to be there.

{{pad}}

Please let us know by the end of March whether you can come.
