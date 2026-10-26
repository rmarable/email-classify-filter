---
id: long-022
title: "Long card long-022"
threat: "family-letter: benign long mail"
control: "nothing to flag in a long email"
why: "window tuning step 4 (plan draft 8)"
failure_looks_like: "flagged as fraud"
review: "a long email with nothing to flag; confirm the labels fit the whole email"
author: claude
profile: freemail
from: "Aunt Rosa <rosa-lane@freemail.example>"
subject: "Family news"
date: 2026-10-22T10:43:00Z
target_chars: 4175
pad_voice: personal
signature: |
  Aunt Rosa
quoted_thread:
  - from: "\"Aunt Rosa <rosa-lane@freemail.example>\""
    date: 2026-10-15T10:00:00Z
    body: "I have started baking bread at the weekends. The first loaf was rather dense, but they are getting better, and the kitchen smells wonderful while it is in the oven.\n\nThe kittens next door have discovered our garden. They chase leaves across the lawn every afternoon, and I confess I spend far too long watching them from the window.\n\nWe drove up to the lakes for a day last week. The leaves were just starting to change, and we had a picnic by the water before the clouds came in.\n\nMy sister is expecting her second child in the spring. We are all delighted, and her eldest has already announced that he would like a brother, please."
  - from: "\"Aunt Rosa <rosa-lane@freemail.example>\""
    date: 2026-10-14T10:00:00Z
    body: "The local library has started a repair cafe once a month. I took the old lamp along, and a retired electrician had it working again within twenty minutes.\n\nThe swimming lessons are going well. Our youngest can now manage a length without the float, and she is very proud of the certificate she brought home.\n\nWe have been clearing out the loft and found a box of old letters from Grandma. Some of them are very funny, and I will bring a few along for you to read.\n\nThe fox has been visiting the garden again. We see it most evenings just after dark, trotting along the fence and stopping to look at the house before it goes."
  - from: "\"Aunt Rosa <rosa-lane@freemail.example>\""
    date: 2026-10-13T10:00:00Z
    body: "I finally got round to framing the map you gave us. It is hanging in the hall now, and visitors always stop to look for the places they know.\n\nOur walk on Sunday took us past the old mill. They are turning it into flats, but the water wheel is being kept, which seems a nice way to remember what it was.\n\nThe apple tree has given us a huge crop this year. We have made crumble, chutney and juice, and there are still boxes of apples in the garage waiting for a plan.\n\nIt was good to hear your news on the phone. I am glad the new job is going well, and that the journey is shorter than you expected once you found the right train."
expected:
  labels: {category: private, priority: low, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: false, sender_type: family, fraud_risk: none, phishing: false, private_individual: true}
  rule: otherwise
  safety: {}
---
Hello love, here is all the family news since my last letter.

{{pad}}

Give my love to everyone, and write when you can.
