# Potential Code Sources

Survey of existing GitHub repos and libraries relevant to the local/Jev
email classification pipeline (`LOCAL-EMAIL-PROCESSING.md`,
`JEV-EMAIL-PROCESSING.md`), done before building from scratch. Narrow
finding: nothing combines (a) genuinely arbitrary/user-defined schema,
(b) fully local inference, and (c) a real mailbox dispatcher with logging
— all three at once. This is a real gap, not an under-search — worth
building, but worth stealing specific patterns from what exists below.

---

## 1. Closest full-pipeline match: NoIdeaDeveloper/Email-Classifier

[github.com/NoIdeaDeveloper/Email-Classifier](https://github.com/NoIdeaDeveloper/Email-Classifier)
— MIT license, 0 stars (essentially unvetted/unused, but source code is
real and reads as carefully engineered, not a stub).

### What it is

Full IMAP → classify → dispatch → log pipeline, running entirely locally
via Ollama. CLI + FastAPI web UI. SQLite persistence, audit logging, undo
capability.

**Architecture:** two sequential Ollama calls per email —
1. **Spam classifier** (`spam_classifier.py`) — `is_spam` (bool),
   `confidence`, `reason`, `signals` (list of matched signals: failed
   SPF/DKIM/DMARC, Reply-To/Return-Path mismatch, urgency language, etc.)
2. **Category classifier** (`category_classifier.py`) — only invoked on
   ham; `category` (enum), `confidence`, `reason`

Both go through Ollama's `format=<json schema>` for guaranteed-parseable
output at `temperature=0`.

### Where it overstates "custom schema"

The schema is **fixed shape, configurable values only**. You can edit the
category *enum list* via a `CATEGORY_FOLDERS` env var
(`Work,Finance,Newsletter,Personal,Receipts,Travel,Support,Other` by
default), but adding a new field (e.g. our design's `requires_reply` or
`sentiment`) means editing `category_classifier.py`'s
`build_category_schema()` directly. There's no generic "define your
Pydantic model, get it enforced" layer — it's config-of-values, not
config-of-shape, unlike our `EmailClassification` Pydantic-model approach.

Also: IMAP-only, no Gmail API support. Spam/ham split is hardcoded and
not configurable at all.

### Three patterns worth adopting directly

**1. Crash-safe move logging (`apply.py`, `store.py`)** — better than our
own idempotency design. A `pending` row is written to SQLite *before* the
IMAP call, then `mark_move_applied` / `mark_move_failed` after:

```python
rowid = store.record_move(
    uid=uid, message_id=message_id, src_folder=src_folder,
    dest_folder=dest, reason=verdict.reason, status="pending",
)
try:
    imap.move(uid, dest)
except Exception:
    store.mark_move_failed(rowid)
    raise
store.mark_move_applied(rowid)
```

A crash mid-action leaves an auditable `pending` row instead of an
ambiguous or silently-lost state. This is a stronger pattern than our
JSONL append-log for the actual mailbox-mutation step specifically —
adopt this two-phase pattern for the action dispatcher's `apply_action`
implementation, on top of (not instead of) the broader activity log
(§5a / §10.5 in the design docs).

**2. Sender rules as hot-reloaded TOML (`rules.py`)** — essentially our
`KNOWN_SENDERS` shortcut, done better:

```toml
[domains]
"github.com" = "Newsletter"
"paypal.com" = "Finance"
"evil-spammer.example" = "spam"

[addresses]
"boss@acme.com" = "Work"
```

Re-read on every sync (no restart needed to pick up edits), and a
missing/malformed file degrades to "no rules" with a stderr warning
rather than crashing the pipeline. Cleaner than a hardcoded dict —
consider this file format (or the equivalent for our `rules.md` /
known-sender table) over an in-code dict.

**3. Hardened Ollama call wrapper (`_llm_base.py`)** — a solid reference
for our `classify_email()`:
- `format=schema` param + `temperature=0` for deterministic output
- Retry/backoff (exponential, capped) on transient errors — connection
  reset, timeout, 5xx — distinguished from hard failures (bad request,
  model not found) which raise immediately
- Strips markdown code fences (` ```json ... ``` `) some models wrap
  around JSON before parsing — a real failure mode worth guarding against
- Few-shot examples are built from a `corrections` table of user
  overrides, replayed into every classification call — a working
  implementation of our open item "build out `KNOWN_SENDERS` table
  interactively," generalized to the LLM prompt itself rather than just
  a sender bypass table

### Not yet verified

Only read the source directly for the five files above
(`category_classifier.py`, `spam_classifier.py`, `apply.py`, `rules.py`,
`_llm_base.py`, `store.py` schema). Did not run the test suite, did not
verify the web UI, did not check `imap_client.py`, `config.py`,
`parser.py`, `tui.py` in depth. Treat as a pattern reference, not a
vetted dependency.

## 2. Schema-flexible but no pipeline: ErolCitak/LangGraph_Examples

[github.com/ErolCitak/LangGraph_Examples](https://github.com/ErolCitak/LangGraph_Examples)
— accompanies a Medium article, "Building an LLM-Powered Email Classifier
and Responder with LangGraph, Outlines, and Pydantic."

- Fully local: `Qwen/Qwen2.5-3B-Instruct` via Hugging Face `transformers`,
  schema enforcement via [Outlines](https://github.com/dottxt-ai/outlines),
  output typed via Pydantic.
- **Schema is fixed in the example** — a 2-class `EmailClasses` enum
  (spam/genuine). The article notes multi-label extension as future work,
  but as shipped it's Pydantic-typed output with no runtime schema
  configurability — extending it means editing code, not config.
- **No mailbox integration at all.** Generates a response and stops;
  IMAP/SMTP integration is explicitly listed as future work in the
  article, not implemented.
- Format: a Jupyter notebook, not a packaged application.

**Verdict:** useful as a minimal reference for "Outlines + Pydantic +
local HF model" wiring, not as a starting point for the pipeline itself.

## 3. Structured-output libraries (building blocks, not full solutions)

These aren't email classifiers — they're the layer our `classify_email()`
would be built on, worth knowing about even though none of them ship a
mailbox integration.

### 3a. 567-labs/instructor — deep dive

[github.com/567-labs/instructor](https://github.com/567-labs/instructor)
— explored directly (cloned, read `docs/integrations/ollama.md`, the
`examples/classification/` directory, and `instructor/v2/core/retry.py`).
This is the strongest single building block found for our classifier
component, and should likely replace hand-rolling `call_ollama()` /
`_llm_base.py`-style wrapper entirely rather than just inspiring it.

**Why it fits directly:** it does exactly "pass a Pydantic model, get a
validated instance back," against Ollama as a first-class local backend:

```python
import instructor
from pydantic import BaseModel
from typing import Literal

class EmailClassification(BaseModel):
    category: Literal["work", "personal", "marketing", "notification", "other"]
    priority: Literal["low", "medium", "high"]

client = instructor.from_provider("ollama/mistral-small", mode=instructor.Mode.JSON)

result = client.chat.completions.create(
    messages=[
        {"role": "system", "content": "Classify this email..."},
        {"role": "user", "content": f"From: {sender}\nSubject: {subject}\nBody: {body}"},
    ],
    response_model=EmailClassification,
    max_retries=3,
)
# result is already a validated EmailClassification instance
```

This is our design's actual `EmailClassification` model passed directly
as `response_model` — no separate JSON-schema-building step, no manual
`.model_validate_json()` call. Unlike NoIdeaDeveloper/Email-Classifier's
approach (hand-build a JSON schema dict per classifier, §1 above), the
schema *is* the Pydantic model — genuinely swappable/extensible the way
our design intends, not config-of-values.

**Automatic re-ask on validation failure — better than our original
plan.** Our design's `classify_email()` sketch said "raise on validation
failure rather than guessing." Instructor does something better: reading
`instructor/v2/core/retry.py`, `ValidationError` and `JSONDecodeError`
are treated as *retryable* — on a schema violation, it automatically
re-prompts the model with the validation error message and retries (via
`tenacity`, up to `max_retries`), rather than either failing immediately
or silently accepting bad output. `examples/classification/classifiy_with_validation.py`
demonstrates this directly: a `field_validator` on the Pydantic model
raises `ValueError` for an out-of-enum classification code, and
instructor's retry loop feeds that error back to the model until it
self-corrects or retries are exhausted. This is a strictly better failure
mode than what we'd planned to hand-roll.

**Mode selection matters for local models:** the auto client picks
`TOOLS` mode for function-calling-capable models (llama3.1, qwen2.5,
mistral-nemo, etc.) and `JSON` mode otherwise — relevant when choosing
between Mistral Small 3 and other candidates from
`LOCAL-EMAIL-PROCESSING.md` §4.2, since mode support affects reliability.

**Timeout handling for Ollama specifically:** the docs call out that
`timeout` is a *total* budget across all retries, not per-attempt — worth
noting since a naive retry loop (like our original sketch's) could
otherwise multiply wait time on a slow local model under load.

**Scope caveat:** instructor is a general-purpose structured-output
library used across many providers (OpenAI, Anthropic, Gemini, Cohere,
etc., per its own `CLAUDE.md`) — it has no concept of email, mailboxes,
or dispatching. It's purely the classifier-call layer; everything else
in our design (ingestion, rule engine, action dispatcher, logging) is
still ours to build. Also worth noting: its own `CLAUDE.md` states tests
make real API calls with no mocking — a sign the library is taken
seriously by its maintainers, not directly relevant to us but a mark of
quality signal.

### 3b. dottxt-ai/outlines — deep dive

[github.com/dottxt-ai/outlines](https://github.com/dottxt-ai/outlines) —
explored directly (cloned, read the README's model-integration table, and
`src/outlines/models/ollama.py` / `mlxlm.py`). What ErolCitak's example
(§2) is built on. The key finding: **outlines' value is backend-dependent
in a way that matters directly for our two target runtimes.**

**The core API is genuinely simple** — pass any Python type, including a
Pydantic model, as the desired output shape:

```python
import outlines
from pydantic import BaseModel
from typing import Literal

class EmailClassification(BaseModel):
    category: Literal["work", "personal", "marketing", "notification", "other"]
    priority: Literal["low", "medium", "high"]

model = outlines.from_mlxlm(mlx_model, mlx_tokenizer)  # or from_ollama, from_transformers, etc.
result = model(prompt, EmailClassification)
```

**On MLX (`src/outlines/models/mlxlm.py`) — real added value.** The MLX
backend wraps `mlx_lm` directly and supplies a **logits processor** —
i.e. it controls token sampling at the logit level, so JSON schema,
regex, and full context-free-grammar (CFG) constraints are all genuinely
enforced during generation, not just requested. For
`LOCAL-EMAIL-PROCESSING.md`'s MLX path (§4.2), this is a real capability
add beyond what `mlx-lm` alone offers, and is the concrete library the
design doc gestured at when it mentioned "structured output constraining
via a library such as outlines" — now confirmed as the right choice,
specifically for MLX.

**On Ollama (`src/outlines/models/ollama.py`) — no meaningful upgrade
over what we already have.** Reading `OllamaTypeAdapter.format_output_type`
directly: Regex- and CFG-based constraints explicitly raise
`TypeError` — "Regex-based structured outputs are not supported by
Ollama. Use an open source model in the meantime" (same for CFG) — because
Ollama's API doesn't expose logit-level control the way a local
in-process model does. For JSON schema, outlines on Ollama is a thin
pass-through to Ollama's own native `format=<schema>` parameter — the
exact mechanism NoIdeaDeveloper/Email-Classifier (§1) and instructor
(§3a) already use directly. **Conclusion: for the Ollama runtime, adding
outlines on top of instructor buys nothing** — instructor already talks
to Ollama's JSON-schema mode natively, and outlines can't do more than
that against the same backend.

**Recommendation, sharpened:** use `outlines` specifically if/when
building against the MLX backend (genuine grammar-level constraint,
worth it), and skip it for the Ollama backend (instructor alone is
sufficient there, §3a). This is a backend-specific choice, not a
blanket "use outlines" decision — worth stating explicitly in
`LOCAL-EMAIL-PROCESSING.md` §4.2 rather than leaving both runtimes
described as roughly equivalent.

### 3c. ollama-instructor — verified, skip it

[pypi.org/project/ollama-instructor](https://pypi.org/project/ollama-instructor)
/ [github.com/lennartpollvogt/ollama-instructor](https://github.com/lennartpollvogt/ollama-instructor)
— checked directly (PyPI page + GitHub repo), not left as a guess.

**What it is:** a wrapper around the Ollama client adding Pydantic
schema validation, retry-on-failure, and logging. MIT licensed, 78
stars, single-maintainer (Lennart Pollvogt).

**Why it's redundant:** every feature it offers — Pydantic validation,
automatic retry, async support — `instructor` (§3a) already provides for
Ollama, more robustly (tenacity-based retry that re-prompts the model
with the actual validation error, not just a bare retry) and across many
more providers, not just Ollama.

**Additional concern beyond redundancy:** last release was **August
2025** — stale by over a year as of this writing (Sep 2026), from a
single maintainer with no visible recent activity. Compare to
`instructor`'s actively-developed, multi-provider codebase (visible v2
refactor in the source, §3a).

**Verdict: skip it.** No functional gap it fills that instructor doesn't
already cover for the Ollama path, plus a real maintenance-risk flag on
top.

## 4. Tutorials / blog posts (no linked repo, or not evaluated in depth)

Listed for completeness; not verified beyond the search snippet /
single-page fetch noted.

- [Sorting email inboxes with local LLMs](https://tech-couch.com/post/sorting-email-inboxes-with-local-llms)
  — tutorial, fully local via Ollama, does real IMAP filing actions, but
  **no formal schema constraint** — relies on prompting the model to
  reply with "ONLY the exact folder name" and string-matches the result,
  rather than JSON-schema/grammar-constrained generation. No GitHub repo
  linked.
- [Building a Gmail Auto Labeler With LLMs](https://murraycole.com/posts/gmail-auto-labeler-llm)
  — Gmail API + label automation; did not verify whether it's local or a
  hosted LLM API. Worth checking if Gmail-API-specific label automation
  code is wanted, independent of the classifier question.
- [Local AI Email Triage](https://localaimaster.com/blog/local-ai-email-triage)
  — not fetched in depth; flagged as unverified, possible further prior
  art.
- [dkautomation23/llm-doc-extractor](https://github.com/dkautomation23/llm-doc-extractor)
  — schema-driven extraction (YAML-defined fields), supports local Ollama
  via `--provider ollama`. Purely extraction, **no mailbox integration or
  dispatcher** — emails are mentioned only as an example document type
  alongside invoices/contracts, with no dedicated email-parsing code.

## 5. Dispatcher / dual-backend (IMAP + Gmail API) prior art

Searched specifically for a repo matching the action-dispatcher half of
our design: classify against a schema, then act on the email, working
against both IMAP and the Gmail API. Two real candidates found; neither
is a full match, but one has a directly reusable abstraction.

### 5a. organvm/universal-mail--automation — reusable pattern, not a dependency

[github.com/organvm/universal-mail--automation](https://github.com/organvm/universal-mail--automation)
— cloned and read `providers/base.py` directly, plus the repo's own
`CLAUDE.md` for context on the wider project.

**What's genuinely useful — `providers/base.py`:** a real, working
`EmailProvider` abstract base class with implementations for Gmail,
generic IMAP, macOS Mail.app (AppleScript), and Outlook — all conforming
to one interface. Two patterns worth adopting directly into our
`MailboxSource` design (`LOCAL-EMAIL-PROCESSING.md` / `JEV-EMAIL-PROCESSING.md`
§4.1a):

1. **`ProviderCapabilities` flags**, not a hardcoded assumption:
   ```python
   class ProviderCapabilities(Flag):
       TRUE_LABELS = auto()   # Gmail: multiple labels per message
       FOLDERS = auto()       # IMAP, Outlook: message lives in one place
       ARCHIVE = auto()
       BATCH_OPERATIONS = auto()
       # ...
   ```
   Plus a `LABEL_IS_MOVE: bool` flag on each provider distinguishing
   "applying a label physically moves the message out of inbox" (folder
   providers) from "labels are additive, message stays put" (Gmail).
   Our design's §4.6 currently just prose-describes this distinction —
   this is a real, checkable capability system worth adopting instead.

2. **Audit based on observed behavior, not assumed capability.**
   `apply_actions()` records whether a message was actually `moved`,
   `archived`, or `labeled` from what operations *actually ran* during
   execution — not from what the provider is nominally supposed to
   support. Directly relevant to our activity-logging requirement (§5a /
   §10.5 in the design docs): log what happened, not what was requested.

**Why not to depend on or fork this repo:** per its own `CLAUDE.md`, this
one file is a small piece of a very large, heavily over-engineered
personal system — payment/billing modules, a private "operator
dashboard," Ed25519-signed authorization receipts for sending mail, and
a large amount of idiosyncratic internal process jargon unrelated to
email processing. 1 star, single author, high complexity-to-value ratio
for what we need. Treat `providers/base.py` as a pattern reference read
once, not a dependency or a template to build the rest of the project
from.

**Critical gap: no LLM/schema classification at all.** Categorization
(`core/rules.py`) is pure regex pattern-matching against a `LABEL_RULES`
dict (sender/subject patterns → category), not a classifier calling any
model. It solves "dispatch based on a category" but has nothing
resembling Jev/local-model classification against a Pydantic schema —
that entire half of our design is absent here.

### 5b. haasonsaas/email-agent — doesn't fit either

[github.com/haasonsaas/email-agent](https://github.com/haasonsaas/email-agent)
— claims Gmail + IMAP + Outlook support (per its README, not verified as
deeply as 5a). Classification is CrewAI multi-agent orchestration — no
formal schema, no validated structured output. Requires a hosted OpenAI
API key (`OPENAI_API_KEY`), so it doesn't fit the local/schema-constrained
requirement either. 56 stars, 7 forks, minimal recent commit activity
(17 total commits).

### 5c. Conclusion

**The combination still doesn't exist anywhere found.** The
dual-backend (IMAP + Gmail API) dispatcher half has real prior art now —
specifically the capability-flags pattern from `providers/base.py`
(§5a) — worth adopting into our `MailboxSource` design. The
schema-constrained-local-LLM-classification half remains something to
build from `instructor` (§3a) / `outlines` (§3b), not something to find
pre-built. This reinforces §3's original finding: the specific
combination our design calls for is a genuine gap, not an under-search.

## 6. Recommendation

Don't fork NoIdeaDeveloper/Email-Classifier wholesale — its schema model
is too rigid for our design's goals (arbitrary, versioned
`EmailClassification` schema; backend-agnostic IMAP + Gmail API; pluggable
Jev/local classifier; Claude-based dispatcher option). But pull in,
concretely:

1. **Classifier library choice is backend-specific — not one library for
   both runtimes:**
   - **Ollama path:** use `567-labs/instructor` (§3a) in place of
     hand-rolling `call_ollama()` / `_llm_base.py`-style plumbing. It
     takes our actual `EmailClassification` Pydantic model as
     `response_model`, and its built-in retry-on-validation-failure
     (re-prompts the model with the error, via `tenacity`) is a better
     failure mode than our original "raise on validation failure" plan.
     Adding `outlines` on top buys nothing here (§3b) — Ollama's API
     doesn't support the regex/CFG constraints that make outlines
     valuable, so it's a thin pass-through to the same `format=schema`
     mechanism instructor already uses directly.
   - **MLX path:** use `dottxt-ai/outlines` (§3b) — its MLX backend
     supplies a real logits processor (genuine grammar-level constraint:
     JSON schema, regex, CFG), which is a real capability instructor
     doesn't offer for this runtime.
   - This changes `LOCAL-EMAIL-PROCESSING.md` §4.2/§4.3 from "write a
     call_ollama wrapper" / "outlines or similar" to two concrete,
     backend-specific library choices — worth reflecting in that doc
     directly, not just noting here.
2. The pending/applied/failed move-logging pattern from `apply.py` /
   `store.py` (§1), layered under our own activity log.
3. The TOML-based sender-rules file format from `rules.py` (§1), in
   place of an in-code `KNOWN_SENDERS` dict.
4. From NoIdeaDeveloper's `_llm_base.py` (§1), the parts instructor
   doesn't already cover: code-fence stripping (some models wrap JSON in
   ` ```json ` even when asked not to) may still be worth keeping as a
   defensive layer even with instructor, and the timeout-budget framing
   is already handled by instructor's own `timeout` parameter (§3a) —
   verify overlap before writing anything custom here.
5. **Adopt the `ProviderCapabilities` flag pattern from
   universal-mail--automation's `providers/base.py`** (§5a) into our
   `MailboxSource` ABC — `TRUE_LABELS` vs `FOLDERS`, plus a
   `LABEL_IS_MOVE` flag per backend — in place of prose-describing the
   Gmail-labels-vs-IMAP-folders distinction. Also adopt its
   observed-behavior audit logging (record what actually happened during
   execution, not what was requested) as a refinement to our own activity
   log spec (§5a / §10.5 in the design docs).
