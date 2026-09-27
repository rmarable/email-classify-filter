---
paths:
  - "tests/eval/synthetic/**"
---

# Synthetic eval set: hygiene rules

Full spec: `GENERATE-FAKE-TESTING-EMAILS.md` (written in V1.0).

- Domains: only RFC 2606 / reserved TLDs (`acme.example`, `vendor-a.example`, `*.test`); never a registrable lookalike.
- Regulators are named in text only, never as real sender domains.
- Phones 555-01xx only; IBANs and routing numbers are published examples or fail their checksum; no real names.
- No real mail ever enters the generation directory, and no real phishing text is copied: published patterns are paraphrased into the fictitious org.
- Nobody writes raw `.eml` or MIME by hand: cases are `.md` cards under `cases/`, built by the deterministic builder; evasions are builder options.
- Files over 1 MB are not committed; they are built on demand into `.build/` (gitignored).
- Generated PDFs contain no JavaScript, forms, embedded files or links.
- The hygiene scan must pass before commit: it fails on any non-reserved domain, URL or email address, and on real-looking phone, card or secret values (PDF text included); gitleaks runs too.
- If the repo becomes public, the adversarial subset lives in `eval/private/` (gitignored).
- A label counts only after the operator confirms it (`ecf eval label`).
