---
id: long-088
title: "Long card long-088"
threat: "family-holiday-driving: benign long mail"
control: "nothing to flag in a long email"
why: "window tuning step 4 (plan draft 8)"
failure_looks_like: "flagged as fraud"
review: "a long email with nothing to flag; confirm the labels fit the whole email"
author: claude
profile: freemail
from: "Uncle Tom <tom-lane@freemail.example>"
subject: "Holiday plans: who drives when"
date: 2026-10-23T16:08:00Z
target_chars: 4225
pad_voice: personal
signature: |
  Tom
quoted_thread:
  - from: "\"Uncle Tom <tom-lane@freemail.example>\""
    date: 2026-10-15T10:00:00Z
    body: "I finally got round to framing the map you gave us. It is hanging in the hall now, and visitors always stop to look for the places they know.\n\nThe new neighbours seem very nice. They came over with a plant when they moved in, and we have promised to have them round for dinner once they have settled.\n\nI took up watercolours again after many years. My first few attempts were muddy, but the evening class has helped, and I am quite pleased with a painting of the harbour.\n\nOur walk on Sunday took us past the old mill. They are turning it into flats, but the water wheel is being kept, which seems a nice way to remember what it was."
  - from: "\"Uncle Tom <tom-lane@freemail.example>\""
    date: 2026-10-14T10:00:00Z
    body: "My knee is much better now, thank you for asking. The physio exercises are dull but they work, and I managed the whole hill on our walk last week without stopping.\n\nDad has been busy in his workshop building a bird table. It is a little lopsided, but the robins do not seem to mind, and he watches them from the kitchen every morning.\n\nThe apple tree has given us a huge crop this year. We have made crumble, chutney and juice, and there are still boxes of apples in the garage waiting for a plan.\n\nThe book you lent me was wonderful. I stayed up far too late finishing it, and I keep thinking about the ending; we should talk about it next time we meet."
  - from: "\"Uncle Tom <tom-lane@freemail.example>\""
    date: 2026-10-13T10:00:00Z
    body: "The children are back at school and the house is very quiet. I have been catching up on the jobs I kept putting off over the summer, starting with the cupboard under the stairs.\n\nIt was good to hear your news on the phone. I am glad the new job is going well, and that the journey is shorter than you expected once you found the right train.\n\nThe allotment has done well this year. We have more courgettes than we know what to do with, so do let me know if you would like some when we next see you.\n\nWe watched the meteor shower from the back garden last night. It was cold, but we saw several bright ones, and the children talked about it all through breakfast."
expected:
  labels: {category: school_or_family, priority: medium, requires_action: false, requires_reply: true, payment_related: false, deadline_mentioned: false, sender_type: family, fraud_risk: none, phishing: false, private_individual: true}
  rule: appointment_school_family
  safety: {}
---
Hello all, here's the plan for the holiday trip, including who drives which day.

{{pad}}

Could you take the second day's drive and pick up Gran on the way?
