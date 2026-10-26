---
id: long-042
title: "Long card long-042"
threat: "family-reunion-plans: benign long mail"
control: "nothing to flag in a long email"
why: "window tuning step 4 (plan draft 8)"
failure_looks_like: "flagged as fraud"
review: "a long email with nothing to flag; confirm the labels fit the whole email"
author: claude
profile: freemail
from: "Cousin Mia <mia-lane@freemail.example>"
subject: "Reunion plans: who brings what"
date: 2026-10-21T10:35:00Z
target_chars: 4225
pad_voice: personal
signature: |
  Mia
quoted_thread:
  - from: "\"Cousin Mia <mia-lane@freemail.example>\""
    date: 2026-10-15T10:00:00Z
    body: "The neighbours' son has been practising the trumpet. He is improving, slowly, and I have started to recognise the tune, which I think is meant to be a carol.\n\nThe school play was a great success. The children had clearly practised for weeks, and the costumes were remarkable considering most of them were made from old curtains.\n\nI have joined a walking group that meets on Sunday mornings. They are a friendly lot, and we always end up at the same cafe for tea and a slice of cake afterwards.\n\nDad has been busy in his workshop building a bird table. It is a little lopsided, but the robins do not seem to mind, and he watches them from the kitchen every morning."
  - from: "\"Cousin Mia <mia-lane@freemail.example>\""
    date: 2026-10-14T10:00:00Z
    body: "The local library has started a repair cafe once a month. I took the old lamp along, and a retired electrician had it working again within twenty minutes.\n\nWe spent a rainy afternoon at the museum in town. The new gallery about the old railway was excellent, and the children loved the model trains in the last room.\n\nWe are thinking of a short trip to the coast next month. Nothing fancy, just a couple of nights somewhere quiet with a good walk and a decent place for fish and chips.\n\nThe photos from the wedding came out beautifully. I will put a few in the post for you, as I know you prefer prints to looking at them on a screen."
  - from: "\"Cousin Mia <mia-lane@freemail.example>\""
    date: 2026-10-13T10:00:00Z
    body: "The children are back at school and the house is very quiet. I have been catching up on the jobs I kept putting off over the summer, starting with the cupboard under the stairs.\n\nThe swimming lessons are going well. Our youngest can now manage a length without the float, and she is very proud of the certificate she brought home.\n\nThe hedge finally got its trim on Saturday. It took most of the morning, and the garden waste bin is full, but the front of the house looks much tidier for it.\n\nThe new neighbours seem very nice. They came over with a plant when they moved in, and we have promised to have them round for dinner once they have settled."
expected:
  labels: {category: school_or_family, priority: medium, requires_action: false, requires_reply: true, payment_related: false, deadline_mentioned: true, sender_type: family, fraud_risk: none, phishing: false, private_individual: true}
  rule: appointment_school_family
  safety: {}
---
Hello all, here are the plans for the family reunion, and who's bringing what.

{{pad}}

Could you bring the folding chairs and arrive by eleven on the Saturday?
