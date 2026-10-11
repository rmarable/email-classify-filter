---
id: ho-thread-html-blockquote-giftcard
title: Gift-card request inside a Gmail-style blockquote in an HTML-only message
threat: gift-card fraud / forged executive request quoted by a fake colleague
control: HTML-only body; the request sits inside a gmail_quote container, not in the new text
why: "The plain-text body is intentionally empty so the builder emits only the HTML part; the new text is one line and the gift-card ask is inside a forged quoted message attributed to the CEO. auth_result pass because the sender uses an attacker-owned outside domain. Held-out card; written without the plan, keyword lists or signals."
failure_looks_like: the item is filed as a short internal note because the ask is only inside the HTML quote container
author: claude
from: "Jamie Ortell <jamie-ortell@pocketmail.example>"
to: [ap@acme.example]
subject: "Re: small thank-you for client services"
date: 2026-10-09T13:58:00Z
expected:
  facts: {auth_result: pass}
  labels: {category: spam_or_phishing, priority: high, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: true, sender_type: person, fraud_risk: high}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
---
## html
<div dir="ltr">Hi, can you pick this up for Dana? I'm in back-to-back sessions until lunch. Thanks, Jamie</div>
<br>
<div class="gmail_quote">
  <div dir="ltr" class="gmail_attr">On Fri, Oct 9, 2026 at 8:51 AM Dana Chief &lt;dana-chief@acme.example&gt; wrote:<br></div>
  <blockquote class="gmail_quote" style="margin:0px 0px 0px 6px;border-left:1px solid rgb(204,204,204);padding-left:8px">
    <div dir="ltr">
      <div>Jamie, I'd like to thank the client services team with something small before the board visit.</div>
      <div><br></div>
      <div>Please pick up eight of the 100 dollar store cards from the electronics shop counter on your way in, scratch off the backs, and email me clear photos of the numbers by 11. Keep it quiet, it's meant to be a surprise. I'll reimburse you this afternoon.</div>
      <div><br></div>
      <div>Dana</div>
    </div>
  </blockquote>
</div>
