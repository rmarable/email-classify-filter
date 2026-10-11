---
id: ho-link-tracking-redirects-marketing
title: Genuine supplier newsletter whose links all go through a click-tracking redirector
threat: none
control: tracking redirects and display-text/href differences in bulk marketing must not read as phishing
why: "Ordinary marketing from a supplier the org already buys from; every link goes through the email service's click tracker, and one visible address differs from its href as is normal for tracked mail. auth_result pass because this is genuine mail; sender_seen_before true (regular newsletter from a known supplier). Held-out card; written without the plan, keyword lists or signals."
failure_looks_like: the newsletter is escalated or flagged for deceptive links
author: claude
from: "Northwind Office Supply <deals@news.northwind-supply.example>"
to: [ap@acme.example]
subject: "Fall restock: 20% off toner and paper this week"
date: 2026-10-06T14:00:00Z
bulk: true
expected:
  facts: {auth_result: pass, sender_seen_before: true}
  labels: {category: marketing, priority: low, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: true, sender_type: vendor, fraud_risk: none}
---
Fall restock week at Northwind Office Supply

20% off toner, copy paper and labels through Sunday.

Shop toner: https://click.mta-relay.example/ls/click?upn=nw1006-toner&r=a7c2
Shop paper: https://click.mta-relay.example/ls/click?upn=nw1006-paper&r=a7c2
Manage your email preferences: https://click.mta-relay.example/ls/click?upn=nw1006-prefs&r=a7c2

Northwind Office Supply, 100 Example Way, Springfield. Call us at 555-0171.
You're receiving this because you have a business account with us.

## html
<div style="font-family:Helvetica,Arial,sans-serif;max-width:600px">
  <h1 style="color:#234">Fall restock week</h1>
  <p>20% off toner, copy paper and labels through Sunday.</p>
  <p><a href="https://click.mta-relay.example/ls/click?upn=nw1006-toner&amp;r=a7c2">Shop toner</a> |
     <a href="https://click.mta-relay.example/ls/click?upn=nw1006-paper&amp;r=a7c2">Shop paper</a></p>
  <p>Browse everything at <a href="https://click.mta-relay.example/ls/click?upn=nw1006-home&amp;r=a7c2">northwind-supply.example/fall</a></p>
  <img src="https://click.mta-relay.example/o/nw1006-open.gif" width="1" height="1" alt="">
  <p style="font-size:11px;color:#888">Northwind Office Supply, 100 Example Way, Springfield. 555-0171.<br>
     You're receiving this because you have a business account with us.
     <a href="https://click.mta-relay.example/ls/click?upn=nw1006-prefs&amp;r=a7c2">Manage preferences</a></p>
</div>
