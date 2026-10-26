---
id: long-036
title: "Long card long-036"
threat: "sales-enquiry-thread: benign long mail"
control: "nothing to flag in a long email"
why: "window tuning step 4 (plan draft 8)"
failure_looks_like: "flagged as fraud"
review: "a long email with nothing to flag; confirm the labels fit the whole email"
author: claude
profile: org
from: "Leah Ford <leah-ford@retail-group.example>"
subject: "Re: range for next season"
date: 2026-10-23T11:48:00Z
target_chars: 4200
pad_voice: vendor
signature: |
  Leah Ford
  Buying, Retail Group
quoted_thread:
  - from: "\"Pat Lee <ap@acme.example>\""
    date: 2026-10-15T10:00:00Z
    body: "Following last week's meeting, the facilities group agreed to move the loading bay deliveries to the morning slot. Drivers have been told, and the gate staff have the updated rota.\n\nWe have added two new colours to the catalogue in response to requests from several customers. Samples are available on request, and the full range will be shown at the trade fair.\n\nThe holiday rota for our support desk is confirmed. There will always be someone on duty during working hours, though replies may take a little longer in the busiest week.\n\nThe packaging supplier has moved to a new site closer to our warehouse. Lead times on boxed items may improve slightly once the move has settled down over the coming weeks."
  - from: "\"Leah Ford <leah-ford@retail-group.example>\""
    date: 2026-10-14T10:00:00Z
    body: "Next quarter we will review the range of smaller items. A few slow sellers may be retired, and we will give plenty of notice before anything you order regularly is changed.\n\nWe have reviewed the feedback from the site visit. Most of the points were minor and have already been handled, and the remaining two are on the list for the next maintenance window.\n\nThe engineering team completed the routine checks on the packing line. No faults were found, and the line is running at its normal speed again after the short planned stop.\n\nThanks for taking part in the customer survey. The most common request was clearer delivery windows, and we are working with the carriers on a narrower time slot for each drop."
  - from: "\"Pat Lee <ap@acme.example>\""
    date: 2026-10-13T10:00:00Z
    body: "We tested the new labels in cold storage and they stay readable at low temperatures. That was a concern raised at the last review, so it is good to have the result on record.\n\nThe assembly guides are now available in larger print. They were redesigned after feedback from a care home that orders from us, and the new layout is easier for everyone to read.\n\nFor your records, the contract summary we keep on file still lists the same contacts as last year. Let us know if anyone has joined or left your team so we can keep it current.\n\nYour new contact on our sales team will introduce themselves next week. They have worked with similar customers for several years and will keep the same regular check-in that you have now."
expected:
  labels: {category: sales_inquiry, priority: medium, requires_action: true, requires_reply: true, payment_related: false, deadline_mentioned: false, sender_type: customer, fraud_risk: none, phishing: false, private_individual: false}
  rule: requires_reply
  safety: {}
---
Thanks for the details below. We're interested in stocking your range.

{{pad}}

Could you share your trade prices for the larger units and the lead times?
