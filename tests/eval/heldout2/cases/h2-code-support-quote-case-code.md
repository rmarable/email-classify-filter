---
id: h2-code-support-quote-case-code
title: Vendor support desk asks us to quote the case code in replies
threat: none (benign control; vendor support reply that mentions a case code)
control: credential-request reading must not treat a ticket or case code as a sign-in code
why: A SaaS vendor's support agent picks up our ticket, gives the case code and asks us to keep it in the subject when we reply. It answers the question and asks for a row count and a screenshot. Authentication set to pass with sender_seen_before true, because this is genuine mail from a vendor we already use. Held-out batch 2 card; written without the plan, keyword lists or signals.
failure_looks_like: the email is labelled spam_or_phishing or account_security, or given any fraud risk
review: category is a judgement call between notification and customer_request; we are the customer here, so bug_report (a customer's defect report) doesn't fit the sender's side
author: claude
from: "Vendor C Support <support@vendor-c.example>"
to: [it-admin@acme.example]
subject: "[Case 00482913] Export to CSV stops at 10,000 rows"
date: 2026-10-07T08:55:00Z
expected:
  labels: {category: bug_report, priority: medium, requires_action: true, requires_reply: true, payment_related: false, deadline_mentioned: true, sender_type: vendor, fraud_risk: low}
  facts: {auth_result: pass, sender_seen_before: true}
---
Hello,

Thanks for getting in touch. I'm Rowan from the Vendor C support team and I've picked up your request.

Your case code is 00482913. When you reply, please keep it in the subject line or quote it at the top of your message, so your reply comes straight back to this case instead of opening a new one. If you'd rather call us on 555-0172, the agent will ask you for it as well.

On the issue itself: the 10,000-row cut-off is a limit of the quick export button, not a problem with your data. For larger reports, use Reports > Scheduled exports, which has no row limit and emails you a download link when the file is ready.

If that doesn't cover what you need, could you tell me roughly how many rows the report has and which plan your workspace is on? A screenshot of the export settings would also help.

We'll keep the case open for 7 days while we wait for your reply.

Best regards,
Rowan
Vendor C Support
