---
id: long-034
title: "Long card long-034"
threat: "grandchild-bail: scam ask after 1,500 (text)"
control: "the classifier's fraud_risk on the body alone"
why: "window tuning step 4 (plan draft 8)"
failure_looks_like: "fraud_risk none, not escalated"
review: "a long email whose scam ask sits after 1,500; confirm the labels fit the whole email"
author: claude
profile: freemail
from: "Charlie Weston <charlie-weston@freemail.example>"
subject: "Hi Grandpa"
date: 2026-10-20T10:54:00Z
signal: "cover my bail before the hearing tomorrow"
target_chars: 4225
pad_voice: personal
signature: |
  Charlie
quoted_thread:
  - from: "\"Charlie Weston <charlie-weston@freemail.example>\""
    date: 2026-10-15T10:00:00Z
    body: "We had a power cut on Tuesday evening and ended up playing cards by candlelight. It was rather fun, though the children were less keen once they realised the internet was off.\n\nThe village fete is next Saturday. There will be the usual cake stall and dog show, and I have somehow been talked into helping with the tombola.\n\nThe choir concert went well. We were nervous about the new piece, but it came together on the night, and the church was nearly full, which was lovely to see.\n\nMy sister is expecting her second child in the spring. We are all delighted, and her eldest has already announced that he would like a brother, please."
  - from: "\"Charlie Weston <charlie-weston@freemail.example>\""
    date: 2026-10-14T10:00:00Z
    body: "I finally got round to framing the map you gave us. It is hanging in the hall now, and visitors always stop to look for the places they know.\n\nWe spent a rainy afternoon at the museum in town. The new gallery about the old railway was excellent, and the children loved the model trains in the last room.\n\nWe finally finished painting the spare room. It took far longer than planned, mostly because we changed our minds about the colour twice, but it looks much brighter now.\n\nThe new neighbours seem very nice. They came over with a plant when they moved in, and we have promised to have them round for dinner once they have settled."
  - from: "\"Charlie Weston <charlie-weston@freemail.example>\""
    date: 2026-10-13T10:00:00Z
    body: "We went to the farmers' market on Saturday and came home with far too much cheese. The bread stall was excellent too, and the man there remembered us from last time.\n\nThe tomatoes on the windowsill are finally ripening. It has taken all summer, but the first few were delicious, and there are plenty more still turning red.\n\nI took up watercolours again after many years. My first few attempts were muddy, but the evening class has helped, and I am quite pleased with a painting of the harbour.\n\nThank you for the birthday card. It arrived on the day, and the picture on the front made me laugh; it looks just like the cottage we stayed in all those years ago."
expected:
  labels: {category: private, priority: high, requires_action: false, requires_reply: false, payment_related: true, deadline_mentioned: true, sender_type: family, fraud_risk: high, phishing: true, private_individual: true}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
---
Hi Grandpa, I hope you and Grandma are well. Sorry it's been a while.

{{pad}}

I'm in a bit of trouble abroad and need you to cover my bail before the hearing tomorrow. My lawyer will ring you from 555-0179 with the details. Please don't tell Mum and Dad; I'll explain everything when I'm home.

Love you both.
