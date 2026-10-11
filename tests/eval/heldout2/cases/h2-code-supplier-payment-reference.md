---
id: h2-code-supplier-payment-reference
title: Supplier asks which payment reference code a transfer carried
threat: none (benign control; business mail that mentions a code)
control: fraud triggers and credential-request reading must not fire on a request for a payment reference
why: A known supplier can't match an incoming transfer to one of two near-identical invoices and asks for the payment reference code we used. Nothing about bank details changes and no credential is asked for. Authentication set to pass with sender_seen_before true, because this is genuine mail from a known vendor's own domain. Held-out batch 2 card; written without the plan, keyword lists or signals.
failure_looks_like: the email is labelled spam_or_phishing or vendor_change_request, or escalated as fraud
review: category is a judgement call between payment_confirmation (a 'payment received' note from someone we pay) and billing_inquiry (a question about a charge)
author: claude
from: "Lena Ortiz <lena-ortiz@vendor-a.example>"
to: [ap@acme.example]
subject: "Payment received 6 Oct - which invoice is it for?"
date: 2026-10-07T10:12:00Z
expected:
  labels: {category: payment_confirmation, priority: medium, requires_action: true, requires_reply: true, payment_related: true, deadline_mentioned: true, sender_type: vendor, fraud_risk: low}
  facts: {auth_result: pass, sender_seen_before: true}
---
Hi there,

We had a transfer from ACME land on Monday 6 October for EUR 3,480.00, but the reference line on our bank statement came through blank, so our system can't match it automatically.

We have two invoices open for you at almost the same amount: INV-30417 (3,480.00) and INV-30452 (3,408.00). I'd rather not close the wrong one and then have to chase you for a balance that's actually been paid.

Could you send me the payment reference code you used on the transfer, or just tell me which invoice it was meant for? If you have the remittance advice to hand, a copy of that would be even better.

No rush, end of the week is fine.

Thanks,
Lena
Accounts Receivable, Vendor A
555-0131
