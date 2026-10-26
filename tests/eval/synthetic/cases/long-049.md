---
id: long-049
title: "Long card long-049"
threat: "buyer-courier-fee: scam ask after 1,500 (text)"
control: "the classifier's fraud_risk on the body alone"
why: "window tuning step 4 (plan draft 8)"
failure_looks_like: "fraud_risk none, not escalated"
review: "a long email whose scam ask sits after 1,500; confirm the labels fit the whole email"
author: claude
profile: freemail
from: "Drew Hollis <drew-hollis@freemail.example>"
subject: "Your sofa"
date: 2026-10-21T08:07:00Z
signal: "cover his fee at courier-collect-desk.example"
target_chars: 4200
pad_voice: personal
signature: |
  Drew
quoted_thread:
  - from: "\"Drew Hollis <drew-hollis@freemail.example>\""
    date: 2026-10-15T10:00:00Z
    body: "We had a power cut on Tuesday evening and ended up playing cards by candlelight. It was rather fun, though the children were less keen once they realised the internet was off.\n\nThe weather has finally turned. We lit the fire for the first time this season last night, and the cat has claimed the rug in front of it as her own.\n\nThe allotment has done well this year. We have more courgettes than we know what to do with, so do let me know if you would like some when we next see you.\n\nThe new neighbours seem very nice. They came over with a plant when they moved in, and we have promised to have them round for dinner once they have settled."
  - from: "\"Drew Hollis <drew-hollis@freemail.example>\""
    date: 2026-10-14T10:00:00Z
    body: "Thank you again for the recipe. I made the soup on Sunday and everyone went back for seconds, even my brother, who usually refuses anything with lentils in it.\n\nIt was lovely to catch up at the weekend. The garden looked wonderful, and I have been meaning to ask what you planted along the fence; the colours were really striking.\n\nThe hedge finally got its trim on Saturday. It took most of the morning, and the garden waste bin is full, but the front of the house looks much tidier for it.\n\nIt was good to hear your news on the phone. I am glad the new job is going well, and that the journey is shorter than you expected once you found the right train."
  - from: "\"Drew Hollis <drew-hollis@freemail.example>\""
    date: 2026-10-13T10:00:00Z
    body: "I finally got round to framing the map you gave us. It is hanging in the hall now, and visitors always stop to look for the places they know.\n\nThe swimming lessons are going well. Our youngest can now manage a length without the float, and she is very proud of the certificate she brought home.\n\nWe drove up to the lakes for a day last week. The leaves were just starting to change, and we had a picnic by the water before the clouds came in.\n\nThank you for the birthday card. It arrived on the day, and the picture on the front made me laugh; it looks just like the cottage we stayed in all those years ago."
expected:
  labels: {category: private, priority: high, requires_action: false, requires_reply: false, payment_related: true, deadline_mentioned: false, sender_type: person, fraud_risk: high, phishing: true, private_individual: true}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
---
Hello, I saw your sofa advert and it's exactly what we've been looking for.

{{pad}}

I'll give you more than your asking price. My courier will collect it, but you need to cover his fee at courier-collect-desk.example first; I'll return double with the full amount once he has it.

Thanks, and speak soon.
