---
id: long-015
title: "Long card long-015"
threat: "romance-customs: scam ask early (control)"
control: "the classifier's fraud_risk on the body alone (rules alone don't reach fraud_guard)"
why: "window tuning step 4 (plan draft 8)"
failure_looks_like: "fraud_risk none, not escalated"
review: "a long email whose scam ask sits early; confirm the labels fit the whole email"
author: claude
profile: freemail
from: "Jamie Holt <jamie-holt@freemail.example>"
subject: "Re: my letter"
date: 2026-10-22T12:40:00Z
signal: "cover the customs fee at freight-clearance-help.example"
target_chars: 4275
pad_voice: personal
signature: |
  Jamie
quoted_thread:
  - from: "\"Jamie Holt <jamie-holt@freemail.example>\""
    date: 2026-10-15T10:00:00Z
    body: "I took up watercolours again after many years. My first few attempts were muddy, but the evening class has helped, and I am quite pleased with a painting of the harbour.\n\nWe spent a rainy afternoon at the museum in town. The new gallery about the old railway was excellent, and the children loved the model trains in the last room.\n\nWe had a power cut on Tuesday evening and ended up playing cards by candlelight. It was rather fun, though the children were less keen once they realised the internet was off.\n\nThe fox has been visiting the garden again. We see it most evenings just after dark, trotting along the fence and stopping to look at the house before it goes."
  - from: "\"Jamie Holt <jamie-holt@freemail.example>\""
    date: 2026-10-14T10:00:00Z
    body: "The allotment has done well this year. We have more courgettes than we know what to do with, so do let me know if you would like some when we next see you.\n\nThe kittens next door have discovered our garden. They chase leaves across the lawn every afternoon, and I confess I spend far too long watching them from the window.\n\nThank you again for the recipe. I made the soup on Sunday and everyone went back for seconds, even my brother, who usually refuses anything with lentils in it.\n\nDad has been busy in his workshop building a bird table. It is a little lopsided, but the robins do not seem to mind, and he watches them from the kitchen every morning."
  - from: "\"Jamie Holt <jamie-holt@freemail.example>\""
    date: 2026-10-13T10:00:00Z
    body: "The hedge finally got its trim on Saturday. It took most of the morning, and the garden waste bin is full, but the front of the house looks much tidier for it.\n\nThe tomatoes on the windowsill are finally ripening. It has taken all summer, but the first few were delicious, and there are plenty more still turning red.\n\nI finally got round to framing the map you gave us. It is hanging in the hall now, and visitors always stop to look for the places they know.\n\nMum is settling into the new flat. She likes being closer to the shops, and the lady across the hall has already invited her to the weekly coffee morning."
expected:
  labels: {category: private, priority: high, requires_action: false, requires_reply: false, payment_related: true, deadline_mentioned: false, sender_type: person, fraud_risk: high, phishing: true, private_individual: true}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
---
Did you read my letter below? I keep thinking about our plans.

My belongings are held at the port and the agent says I can't travel until they're released. Could you cover the customs fee at freight-clearance-help.example? I will repay you the day I arrive, and then we can finally meet.

{{pad}}

Thinking of you.
