---
id: bug-security-vuln-report
title: Researcher reports a security vulnerability
threat: none
control: rule 3 flags high-priority bug reports
why: security reports need a fast, careful reply
failure_looks_like: classified as spam
from: "Independent Researcher <research@sec-researcher.example>"
subject: "Security issue in your login form"
date: 2026-10-26T10:00:00Z
author: claude
expected:
  labels: {category: bug_report, priority: high}
  rule: bug_report
---
Hello,

I found a stored cross-site scripting issue in the profile name field of your web app. I have
not shared it publicly. I can send details to a security contact. I follow a 90-day disclosure
policy.
