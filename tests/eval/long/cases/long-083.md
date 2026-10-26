---
id: long-083
title: "Long card long-083"
threat: "community-newsletter: benign long mail"
control: "nothing to flag in a long email"
why: "window tuning step 4 (plan draft 8)"
failure_looks_like: "flagged as fraud"
review: "a long email with nothing to flag; confirm the labels fit the whole email"
author: claude
profile: freemail
from: "Elm Street Residents <newsletter@elm-street-residents.example>"
subject: "Elm Street news for the autumn"
date: 2026-10-22T13:58:00Z
target_chars: 4300
pad_voice: personal
signature: |
  Elm Street Residents' Group
quoted_thread:
  - from: "\"Elm Street Residents <newsletter@elm-street-residents.example>\""
    date: 2026-10-15T10:00:00Z
    body: "We drove up to the lakes for a day last week. The leaves were just starting to change, and we had a picnic by the water before the clouds came in.\n\nThe dog has been enjoying the cooler mornings. We have started walking the long way round the lake, and she always stops to say hello to the ducks by the old boathouse.\n\nWe are looking forward to seeing you at the end of the month. The spare bed is made up, and I have found the board game we used to play, so be ready for a rematch.\n\nOur walk on Sunday took us past the old mill. They are turning it into flats, but the water wheel is being kept, which seems a nice way to remember what it was."
  - from: "\"Elm Street Residents <newsletter@elm-street-residents.example>\""
    date: 2026-10-14T10:00:00Z
    body: "The allotment has done well this year. We have more courgettes than we know what to do with, so do let me know if you would like some when we next see you.\n\nThank you for the birthday card. It arrived on the day, and the picture on the front made me laugh; it looks just like the cottage we stayed in all those years ago.\n\nThe street party for the jubilee is being planned already. Someone suggested bunting all the way down the road, so I have offered to help sew it over the winter.\n\nThe tomatoes on the windowsill are finally ripening. It has taken all summer, but the first few were delicious, and there are plenty more still turning red."
  - from: "\"Elm Street Residents <newsletter@elm-street-residents.example>\""
    date: 2026-10-13T10:00:00Z
    body: "We have been clearing out the loft and found a box of old letters from Grandma. Some of them are very funny, and I will bring a few along for you to read.\n\nThe new neighbours seem very nice. They came over with a plant when they moved in, and we have promised to have them round for dinner once they have settled.\n\nThe children are back at school and the house is very quiet. I have been catching up on the jobs I kept putting off over the summer, starting with the cupboard under the stairs.\n\nWe spent a rainy afternoon at the museum in town. The new gallery about the old railway was excellent, and the children loved the model trains in the last room."
expected:
  labels: {category: marketing, priority: low, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: false, sender_type: company, fraud_risk: none, phishing: false, private_individual: true}
  rule: marketing
  safety: {}
---
Hello neighbours, here is the autumn newsletter from the residents' group.

{{pad}}

See you at the next meeting, and thank you to everyone who helped this season.
