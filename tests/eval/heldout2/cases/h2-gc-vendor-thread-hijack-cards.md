---
id: h2-gc-vendor-thread-hijack-cards
title: Reply in a real-looking vendor thread asks for cards instead of a wire
threat: thread hijack / vendor payment diverted to gift cards
control: fraud triggers (gift-card request in a payment thread, lookalike vendor domain)
why: >-
  A reply quotes a genuine-looking invoice thread with Vendor C and then asks AP to settle
  the invoice partly in retail cards because "the bank is holding wires". Authentication set
  to pass: the reply comes from a lookalike of vendor-c.example registered by the attacker,
  not a spoof of the real domain; the lookalike address is new.
  Held-out batch 2 card; written without the plan, keyword lists or signals.
failure_looks_like: the reply is trusted because of the quoted thread and labelled as an invoice without escalation
review: >-
  expected rule fraud_guard is assumed, not computed; confirm with ecf rules test. Category is
  spam_or_phishing rather than vendor_change_request because it is a one-off demand to pay in
  cards and nothing on file changes.
author: claude
from: "Alex Brandt <alex-brandt@vendor-c-invoicing.example>"
to: [ap@acme.example]
subject: "RE: INV-30418 - October service invoice"
date: 2026-10-08T16:47:00Z
headers:
  In-Reply-To: "<inv30418-thread-02@vendor-c.example>"
  References: "<inv30418-thread-01@vendor-c.example> <inv30418-thread-02@vendor-c.example>"
expected:
  labels: {category: spam_or_phishing, priority: high, requires_action: true, requires_reply: true, payment_related: true, deadline_mentioned: true, sender_type: vendor, fraud_risk: high}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
  facts: {auth_result: pass, sender_seen_before: false}
---
Hi there,

Thanks for confirming. Small wrinkle on our side - our bank has put a temporary hold on incoming international and domestic wires while they finish an audit of our accounts, so anything sent this week will bounce back and sit for 10-15 business days.

To keep things moving, our controller has okayed us taking part of this invoice in retailer cards, which we use for our field crew's expenses anyway. Could you cover $2,400 of INV-30418 that way (12 x $200, any of the large home improvement or electronics stores) and send the codes over to me in a reply? The remaining $1,450 can go by wire once the hold lifts and I'll send you an adjusted statement.

Sorry for the hassle, I know it's unusual. If you can get it to me before end of day tomorrow we avoid the late fee on our side.

Best,
Alex Brandt
Accounts Receivable | Vendor C Facility Services
555-0118

> On Tue, Oct 6, 2026, AP Team <ap@acme.example> wrote:
> Hi Alex, we've received INV-30418 and it's approved for payment.
> It's scheduled for our Thursday run. Let us know if anything changes.
>
> > On Mon, Oct 5, 2026, Alex Brandt <alex-brandt@vendor-c.example> wrote:
> > Good morning, attached is our October invoice for the quarterly HVAC
> > maintenance visit. Payment terms net 30 as usual. Thanks!
