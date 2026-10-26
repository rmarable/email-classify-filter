---
id: long-043
title: "Long card long-043"
threat: "broker-policy-signin: scam ask after 1,500 (text)"
control: "the classifier's fraud_risk on the body alone"
why: "window tuning step 4 (plan draft 8)"
failure_looks_like: "fraud_risk none, not escalated"
review: "a long email whose scam ask sits after 1,500; confirm the labels fit the whole email"
author: claude
profile: org
from: "Keystone Insurance Brokers <service@keystone-brokers.example>"
subject: "Keystone: your policy and service notes"
date: 2026-10-19T15:08:00Z
signal: "confirm the director's sign-in at broker-policy-portal.example"
target_chars: 4275
pad_voice: vendor
signature: |
  Keystone Insurance Brokers
quoted_thread:
  - from: "\"Pat Lee <ap@acme.example>\""
    date: 2026-10-15T10:00:00Z
    body: "Our quality team ran the usual spot checks on the latest batch. Everything was within the agreed tolerances, and the test results are kept on file should you ever want to see them.\n\nAs discussed on the call, the team will keep the current schedule for the quarterly review. If anything changes we will share a revised agenda at least a week ahead so everyone has time to prepare their notes.\n\nOur van fleet is being replaced over the next few months. The new vans are quieter and cleaner, and the drivers will keep their usual routes during the changeover.\n\nFor your records, the contract summary we keep on file still lists the same contacts as last year. Let us know if anyone has joined or left your team so we can keep it current."
  - from: "\"Keystone Insurance Brokers <service@keystone-brokers.example>\""
    date: 2026-10-14T10:00:00Z
    body: "The packaging supplier has moved to a new site closer to our warehouse. Lead times on boxed items may improve slightly once the move has settled down over the coming weeks.\n\nOur customer service hours are unchanged. The team can be reached during the working day, and messages that arrive outside those hours are answered the next morning.\n\nWe have extended the warranty on the heavier units from one year to two. Nothing changes in how claims are handled; the longer period simply applies to orders from this month.\n\nThe new storage racks in our main warehouse are now in use. They give us room for about a fifth more stock, which should shorten the wait on the items that sold out last winter."
  - from: "\"Pat Lee <ap@acme.example>\""
    date: 2026-10-13T10:00:00Z
    body: "Thanks again for the update on the spring order. The warehouse confirmed that the pallets arrived in good condition and the count matched the packing list, so nothing further is needed on our side for that delivery.\n\nThe trade fair stand went well this year. We met several of our regular customers in person and took notes on the ideas they shared, which the product team will look at this quarter.\n\nWe have reviewed the feedback from the site visit. Most of the points were minor and have already been handled, and the remaining two are on the list for the next maintenance window.\n\nOne of our longest serving drivers retires at the end of the month. Many of you will know him from the morning rounds, and his colleagues are planning a small farewell."
expected:
  labels: {category: account_security, priority: high, requires_action: true, requires_reply: false, payment_related: false, deadline_mentioned: true, sender_type: vendor, fraud_risk: high, phishing: true, private_individual: false}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
---
Hello from Keystone. Here are this season's policy and service notes.

{{pad}}

Your business cover lapses on Friday unless you confirm the director's sign-in at broker-policy-portal.example. Our renewals have moved to a new partner, so your usual broker can't help with this one.

With thanks from Keystone.
