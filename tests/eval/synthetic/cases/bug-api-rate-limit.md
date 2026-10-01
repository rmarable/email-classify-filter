---
id: bug-api-rate-limit
title: Integration partner reports unexpected API rate limiting
threat: none
control: rule 3; a partner reporting a defect is still a bug report
why: bug reports also come from partners
failure_looks_like: classified as partnership and the defect lost
from: "Partner Q Engineering <eng@partner-q.example>"
subject: "429 errors on the export API"
date: 2026-10-28T08:00:00Z
author: claude
expected:
  labels: {category: bug_report, requires_reply: true, fraud_risk: none}
  rule: bug_report
---
Hi,

Since Monday our integration gets HTTP 429 after about 20 requests per minute, although the
documented limit is 120. This breaks the nightly sync for our shared customers. Can you check?

Partner Q Engineering
