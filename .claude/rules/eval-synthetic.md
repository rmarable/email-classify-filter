---
paths:
  - "tests/eval/synthetic/**"
  - "tests/eval/drafts/**"
  - "eval/private/**"
---

# Rules for `tests/eval/synthetic/**`, `tests/eval/drafts/**` and `eval/private/**`

Shared with Codex: `AGENTS.md` points here.

Full spec: `planning-docs/GENERATE-FAKE-TESTING-EMAILS.md`. Build with `uv run ecf eval build` (it runs the hygiene scan first).
The draft set (`tests/eval/drafts/`, M8's draft-quality eval) follows the same rules; build it
with `uv run ecf eval build --root tests/eval/drafts`.

- Domains: only RFC 2606 / reserved TLDs (`acme.example`, `vendor-a.example`, `*.test`); never a registrable lookalike.
- Regulators are named in text only, never as real sender domains.
- Phones 555-01xx only; IBANs are published examples or fail their checksum, routing numbers fail their checksum; no real names.
- No real mail ever enters the generation directory, and no real phishing text is copied: published patterns are paraphrased into the fictitious org. Real-mail corpus content (SPEC §16.7) never feeds cards.
- Nobody writes raw `.eml` or MIME by hand: cases are `.md` cards under `cases/`, built by the deterministic builder; evasions are builder options.
- Files over 1 MB are not committed; they are built on demand into `.build/` (gitignored).
- Generated PDFs contain no JavaScript, forms, embedded files or links.
- The hygiene scan must pass before commit. It fails on email addresses, URLs and dotted names outside the reserved names (anything not ending in a reserved name or a known file extension), defanged and non-ASCII hostnames (read after NFKC normalisation, so fullwidth and look-alike dots count as ASCII), local parts that imitate a domain (`paypal.com@`; an ordinary dotted local part such as `pat.lee@` passes), phone numbers other than 555-01xx, SSN-shaped numbers, Luhn-valid cards, checksum-valid IBANs and routing numbers, and token shapes. PDF text is covered because PDFs are built from card fields. gitleaks is planned, not running yet. Write a space after a full stop, or `report.Summary` reads as a domain.
- Injection cards live in the private set, `eval/private/<set>` (a clone of the operator's private
  repository, gitignored here; `planning-docs/EVAL-PRIVATE.md`), never under `tests/eval/`. A card
  is an injection card when its front matter has `injection_target`, `injection_phrase` or
  `hidden_text` at any level, a `threat` containing `inject`, or `expected.draft.group: injection`
  (X1, `ecf.eval.markers`). The pre-push card scan and the sdist test refuse one in a public set,
  and so does `ecf eval build`, except a generated card (`ecf eval generate` writes `hidden_text`;
  never gate evidence, and the scans still refuse it); the only public one is the pinned
  `starter-injection` (V3, OD-654). A private card without a marker is refused too. `ecf eval
  new-case --template injection` writes to the private set; `ecf eval build` builds both halves
  and the merged view readers use (`eval/private/.merged/<set>`), then says to commit both.
- A private card's text is never quoted in tests, docs or fixtures: tests build their own marked
  cards (`tests/eval_checkout.py`, `split_set`). Result files and the gate's detail name private
  IDs only (closed vocabulary, OD-259).
- A label counts only after the operator confirms it (`ecf eval label`). It asks questions, so the
  operator runs it in their own terminal (a `!` command in Claude Code can't answer them). For a
  private card keep the default `--root` (the public set): it finds the card in either half and
  refreshes the merged view. With `--root eval/private/<set>` the label is confirmed but the merged
  view isn't refreshed; run `ecf eval build` after.
