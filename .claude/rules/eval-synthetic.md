---
paths:
  - "tests/eval/synthetic/**"
---

# Synthetic eval set: hygiene rules

Full spec: `GENERATE-FAKE-TESTING-EMAILS.md`. Build with `uv run ecf eval build` (it runs the hygiene scan first).

- Domains: only RFC 2606 / reserved TLDs (`acme.example`, `vendor-a.example`, `*.test`); never a registrable lookalike.
- Regulators are named in text only, never as real sender domains.
- Phones 555-01xx only; IBANs and routing numbers are published examples or fail their checksum; no real names.
- No real mail ever enters the generation directory, and no real phishing text is copied: published patterns are paraphrased into the fictitious org. Real-mail corpus content (SPEC §16.7) never feeds cards.
- Nobody writes raw `.eml` or MIME by hand: cases are `.md` cards under `cases/`, built by the deterministic builder; evasions are builder options.
- Files over 1 MB are not committed; they are built on demand into `.build/` (gitignored).
- Generated PDFs contain no JavaScript, forms, embedded files or links.
- The hygiene scan must pass before commit. It fails on email addresses, URLs and dotted names outside the reserved names (anything not ending in a reserved name or a known file extension), defanged and non-ASCII hostnames, phone numbers other than 555-01xx, SSN-shaped numbers, Luhn-valid cards, checksum-valid IBANs and routing numbers, and token shapes. PDF text is covered because PDFs are built from card fields. gitleaks is planned, not running yet. Write a space after a full stop, or `report.Summary` reads as a domain.
- If the repo becomes public, the adversarial subset lives in `eval/private/` (gitignored).
- A label counts only after the operator confirms it (`ecf eval label`).
