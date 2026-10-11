---
id: ho-thread-genuine-reply-old-invoice
title: Genuine vendor reply that quotes an old invoice with remittance details
threat: none
control: quoted historical bank details must not make a closing reply look like a payment-change request
why: "A known vendor confirms an invoice is paid; the quoted original invoice carries the bank details it was always paid to. auth_result pass because this is genuine mail from the vendor's own domain; sender_seen_before true (established thread partner). Held-out card; written without the plan, keyword lists or signals."
failure_looks_like: the reply is escalated as fraud because of bank wording that appears only in the quoted invoice
author: claude
from: "Vendor D Billing <billing@vendor-d.example>"
to: [ap@acme.example]
subject: "RE: Invoice INV-D-3302 (August services)"
date: 2026-10-05T15:47:00Z
headers:
  In-Reply-To: "<inv-d-3302-q@acme.example>"
  References: "<inv-d-3302@vendor-d.example> <inv-d-3302-q@acme.example>"
expected:
  facts: {auth_result: pass, sender_seen_before: true}
  labels: {category: payment_confirmation, priority: low, requires_action: false, requires_reply: false, payment_related: true, deadline_mentioned: false, sender_type: vendor, fraud_risk: none}
---
Hi,

Thanks for checking. The transfer you sent on 28 September matches INV-D-3302 in full,
so nothing is outstanding on this one and I've marked it closed on our side. Your
September invoice will go out next week as usual.

Best,
Avery Lund
Billing, Vendor D
555-0126

On Fri, Oct 2, 2026 at 11:05 AM Accounts Payable <ap@acme.example> wrote:
> Hi Avery, can you confirm INV-D-3302 shows as paid? Our transfer went out on
> 28 September.
>
> Thanks, ACME AP
>
> On Tue, Sep 1, 2026 at 9:15 AM Vendor D Billing <billing@vendor-d.example> wrote:
>> Hello,
>>
>> Please find invoice INV-D-3302 for August services.
>>
>> Amount due: 3,150.00 USD
>> Due date: 30 September 2026
>>
>> Remit by bank transfer to:
>> Account name: Vendor D LLC
>> IBAN: GB82 WEST 1234 5698 7654 32
>> Reference: INV-D-3302
>>
>> Questions about this invoice? Reply to this email or call 555-0126.
>>
>> Vendor D Billing
