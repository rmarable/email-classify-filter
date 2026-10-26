---
id: long-002
title: "Long card long-002"
threat: "romance-customs: scam ask after 1,500 (quote)"
control: "the classifier's fraud_risk on the body alone (rules alone don't reach fraud_guard)"
why: "window tuning step 4 (plan draft 8)"
failure_looks_like: "fraud_risk none, not escalated"
review: "a long email whose scam ask sits after 1,500; confirm the labels fit the whole email"
author: claude
profile: freemail
from: "Jamie Holt <jamie-holt@freemail.example>"
subject: "Re: my letter"
date: 2026-10-22T14:42:00Z
signal: "cover the customs fee at freight-clearance-help.example"
target_chars: 4275
pad_voice: personal
signature: |
  Jamie
quoted_thread:
  - from: "\"Jamie Holt <jamie-holt@freemail.example>\""
    date: 2026-10-18T09:00:00Z
    body: "My belongings are held at the port and the agent says I can't travel until they're released. Could you cover the customs fee at freight-clearance-help.example? I will repay you the day I arrive, and then we can finally meet.\n\nLet me know if anything is unclear."
  - from: "\"Jamie Holt <jamie-holt@freemail.example>\""
    date: 2026-10-15T10:00:00Z
    body: "The tomatoes on the windowsill are finally ripening. It has taken all summer, but the first few were delicious, and there are plenty more still turning red.\n\nThe hedge finally got its trim on Saturday. It took most of the morning, and the garden waste bin is full, but the front of the house looks much tidier for it.\n\nDad has been busy in his workshop building a bird table. It is a little lopsided, but the robins do not seem to mind, and he watches them from the kitchen every morning.\n\nThank you again for the recipe. I made the soup on Sunday and everyone went back for seconds, even my brother, who usually refuses anything with lentils in it."
  - from: "\"Jamie Holt <jamie-holt@freemail.example>\""
    date: 2026-10-14T10:00:00Z
    body: "The kittens next door have discovered our garden. They chase leaves across the lawn every afternoon, and I confess I spend far too long watching them from the window.\n\nThe allotment has done well this year. We have more courgettes than we know what to do with, so do let me know if you would like some when we next see you.\n\nThe fox has been visiting the garden again. We see it most evenings just after dark, trotting along the fence and stopping to look at the house before it goes.\n\nWe had a power cut on Tuesday evening and ended up playing cards by candlelight. It was rather fun, though the children were less keen once they realised the internet was off."
  - from: "\"Jamie Holt <jamie-holt@freemail.example>\""
    date: 2026-10-13T10:00:00Z
    body: "We spent a rainy afternoon at the museum in town. The new gallery about the old railway was excellent, and the children loved the model trains in the last room.\n\nI took up watercolours again after many years. My first few attempts were muddy, but the evening class has helped, and I am quite pleased with a painting of the harbour.\n\nThe dog has been enjoying the cooler mornings. We have started walking the long way round the lake, and she always stops to say hello to the ducks by the old boathouse.\n\nI have started baking bread at the weekends. The first loaf was rather dense, but they are getting better, and the kitchen smells wonderful while it is in the oven."
expected:
  labels: {category: private, priority: high, requires_action: false, requires_reply: false, payment_related: true, deadline_mentioned: false, sender_type: person, fraud_risk: high, phishing: true, private_individual: true}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
---
Did you read my letter below? I keep thinking about our plans.

{{pad}}

Thinking of you.
