> Superseded by `SPEC.md` on 2026-09-27; kept for history. Not maintained.
> Moved here from the repository root; content below is unchanged.

# Local Email Processing

A privacy-preserving email classification and filing pipeline. Emails are
classified by a small LLM running **entirely on-device** (via Ollama or MLX
on Apple Silicon), with output strictly constrained to a predefined JSON
schema. Classification results drive actions (labeling, archiving, moving)
against a real mailbox via its API — currently targeting the Gmail API.

---

## 1. Goals

- Classify inbox messages against a schema (`category`, `priority`, to be
  extended) without sending email content to any third-party AI API.
- Keep the schema versioned and easy to extend (new fields, new enum values)
  without rewriting the pipeline.
- Take configurable actions on classified mail (initially: print/log only;
  later: Gmail label + archive, or Apple Mail folder move).
- Run on a MacBook Air (M5, 24GB unified memory) with no cloud LLM calls in
  the classification step.

## 2. Non-goals (for now)

- Training or fine-tuning a model. Prompting + a sender-based rule table is
  expected to be sufficient (see §6).
- Full email client replacement. This is a batch/background classifier +
  actor, not a mail UI.
- Multi-account support. Start with a single Gmail account
  (`rodney.marable@gmail.com`).

## 3. Architecture

Ingestion is backend-agnostic: both IMAP and the Gmail API implement the
same `MailboxSource` interface (see §4.1a) and hand the rest of the
pipeline a plain `EmailMessage` — everything downstream (classifier, rule
engine, action dispatcher) doesn't know or care which backend supplied it.

```
┌───────────────────────────┐        ┌───────────────────────────┐
│   IMAP                    │        │   Gmail API               │
│   (any provider:          │        │   (Gmail-specific:        │
│    iCloud, Fastmail,      │        │    labels, history API,   │
│    Gmail-via-IMAP, etc.)  │        │    richer metadata)        │
└─────────────┬─────────────┘        └─────────────┬─────────────┘
              │                                     │
              └───────────────┬─────────────────────┘
                               ▼
                  ┌─────────────────────────┐
                  │   MailboxSource            │   <- common interface
                  │   (list_messages,          │      (backend-agnostic)
                  │    get_message,             │
                  │    apply_action)            │
                  └────────────┬────────────────┘
                                │  EmailMessage
                                │  (subject, sender, body, ...)
                                ▼
                  ┌─────────────────────────┐
                  │   Local Classifier         │
                  │   (Ollama / MLX,            │
                  │    Mistral 7B or similar,   │
                  │    schema-constrained JSON) │
                  └────────────┬────────────────┘
                                │  EmailClassification
                                │  (validated against schema)
                                ▼
                  ┌─────────────────────────┐
                  │   Rule Engine               │
                  │   (known-sender lookup      │
                  │    short-circuits model      │
                  │    call when possible)       │
                  └────────────┬────────────────┘
                                │  final classification
                                ▼
                  ┌─────────────────────────┐
                  │   Action Dispatcher         │
                  │   (config-driven rules;      │
                  │    delegates back to the     │
                  │    active MailboxSource's    │
                  │    apply_action)              │
                  └────────────┬────────────────┘
                                │
                  ┌─────────────┴─────────────┐
                  ▼                           ▼
     IMAP: move message to folder   Gmail API: add/remove labels
     (\Mailbox\Marketing, etc.)     (Marketing/Low, archive, etc.)
```

**Key privacy property:** the only network calls that carry email content
are (a) to the mail provider itself via IMAP or the Gmail API — which
already has the content, since it's your own mailbox — and (b) to the
local model process on localhost. No email content is ever sent to a
third-party classification API (e.g. Jev, OpenAI, etc.).

## 4. Components

### 4.1a Ingestion abstraction (IMAP + Gmail API)

Define a common interface so the rest of the pipeline never branches on
backend:

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class EmailMessage:
    id: str                 # backend-native message id
    subject: str
    sender: str
    body: str
    raw: dict | None = None # backend-native payload, for backend-specific actions

class MailboxSource(ABC):
    @abstractmethod
    def list_messages(self, folder: str = "INBOX", max_results: int = 50) -> list[str]:
        """Return backend-native message IDs."""

    @abstractmethod
    def get_message(self, msg_id: str) -> EmailMessage:
        """Fetch and normalize a single message."""

    @abstractmethod
    def apply_action(self, msg_id: str, action: str, **kwargs) -> None:
        """Perform a backend-specific action, e.g. label, move, archive."""
```

Two implementations:

- **`GmailApiSource`** — OAuth, `users().messages().list/get/modify`,
  actions expressed as label add/remove (see §4.6a).
- **`ImapSource`** — `imapclient`/`imaplib`, works against any IMAP
  provider (iCloud, Fastmail, self-hosted, or Gmail-via-IMAP as a
  fallback), actions expressed as folder moves / flag changes (see
  §4.6b).

Config picks the backend at runtime:

```yaml
mailbox:
  backend: gmail_api   # or: imap
  gmail_api:
    account: rodney.marable@gmail.com
    scopes: ["https://www.googleapis.com/auth/gmail.modify"]
  imap:
    host: imap.mail.me.com
    account: someone@icloud.com
    # auth via app-specific password / keychain, not committed to repo
```

### 4.1 Schema (versioned, extensible)

Defined as a Pydantic model. Start minimal, grow over time.

```python
from pydantic import BaseModel
from typing import Literal

class EmailClassification(BaseModel):
    category: Literal["work", "personal", "marketing", "notification", "other"]
    priority: Literal["low", "medium", "high"]
    # Future fields (not yet implemented):
    # requires_human_review: bool
    # sentiment: Literal["positive", "neutral", "negative"]
    # requires_reply: bool
```

Design note: keep this schema in its own module (`schema.py`) so it can be
imported by both the classifier and the action dispatcher, and so schema
changes are a single-file diff.

### 4.2 Local model runtime

Two supported runtimes — pick one, or support both behind a config flag:

- **Ollama** — simplest to stand up. `ollama pull mistral-small:7b`, then
  call via the `ollama` Python package with `format=<json schema>` to
  hard-constrain output.
- **MLX** (`mlx-lm`) — Apple's native framework, likely faster/more
  efficient on M-series unified memory. Use `mlx-community` pre-converted
  models. Structured output constraining via a library such as
  [`outlines`](https://github.com/dottxt-ai/outlines) or
  [`llm-structured-output`](https://github.com/otriscon/llm-structured-output)
  (MLX-native schema constraining, includes "pre-emptive decoding" for
  speed).

**Model choice** (avoiding PRC-affiliated and Meta/X-affiliated labs per
user preference):

| Model | Lab | Notes |
|---|---|---|
| Mistral Small 3 (7B) | Mistral AI (France) | Recommended default — fastest in class, strong structured-output track record, ~50 tok/s on mid-range hardware at Q4_K_M |
| Mixtral 8x7B (MoE) | Mistral AI | More headroom for nuance; higher memory use |
| Gemma 4 26B A4B (MoE, 3.8B active) | Google | Apache 2.0, efficient, good for privacy-sensitive local workflows |
| Phi-4-mini (3.8B) | Microsoft | Smallest footprint, leaves most RAM free |

24GB unified memory comfortably fits any of the above at 4-bit quant with
room to spare.

### 4.3 Classification function

```python
def classify_email(subject: str, sender: str, body: str) -> EmailClassification:
    # Build prompt with category/priority definitions + few-shot examples
    # Call local model with format=SCHEMA_JSON (hard constraint)
    # Validate response against EmailClassification (Pydantic)
    # Raise on validation failure rather than guessing
    ...
```

Prompting notes:
- Include explicit category definitions in the prompt (not just enum
  names) — this alone resolves most ambiguous cases (e.g. promotional
  email vs. notification).
- Few-shot examples improve reliability further, especially for edge
  cases like the "daily shoe-site marketing email" case.
- Truncate body to a reasonable length (e.g. 1500 chars) — full email
  bodies are rarely needed for category/priority classification and cost
  latency.

### 4.4 Rule engine (known-sender shortcut)

For repeat senders where classification is always the same (e.g. a
marketing sender you never engage with), skip the model call entirely:

```python
KNOWN_SENDERS: dict[str, EmailClassification] = {
    "deals@someshoesite.com": EmailClassification(category="marketing", priority="low"),
}

def classify(subject, sender, body) -> EmailClassification:
    if sender in KNOWN_SENDERS:
        return KNOWN_SENDERS[sender]
    return classify_email(subject, sender, body)  # falls through to model
```

Benefits: instant, free, 100% consistent, and reduces model calls to only
genuinely novel senders. This table should be persisted (JSON/SQLite) and
should be easy to append to as patterns are discovered — e.g. after a
manual override or correction.

### 4.5 Action dispatcher

Currently a stub, backend-agnostic at this layer — it only knows
`EmailClassification` in, action-name out; the actual backend call is
delegated to whichever `MailboxSource` is active:

```python
def take_action(msg: EmailMessage, result: EmailClassification, source: MailboxSource):
    print(f"[{result.category} / {result.priority}] {msg.subject}")  # stub
    # Later: look up a matching rule and call source.apply_action(...)
```

Action rules are config-driven and backend-neutral in *intent*, translated
to backend-specific parameters by each `MailboxSource`:

```yaml
rules:
  - if: {category: marketing, priority: low}
    action: file_low_priority
    target: "Marketing"   # Gmail: label name / IMAP: folder name
```

`GmailApiSource.apply_action(msg_id, "file_low_priority", target="Marketing")`
adds the `Marketing` label and removes `INBOX`.
`ImapSource.apply_action(msg_id, "file_low_priority", target="Marketing")`
does an IMAP `COPY` + `STORE \Deleted` (or provider-supported `MOVE`) into
the `Marketing` folder.

### 4.6 Ingestion layer

#### 4.6a Gmail API backend

- Auth: OAuth (Desktop app credentials), scope
  `https://www.googleapis.com/auth/gmail.modify` (read + label, not full
  account access).
- List: `users().messages().list(userId="me", labelIds=["INBOX"], ...)`.
- Get: `users().messages().get(..., format="full")`, extract headers
  (Subject, From) and decode multipart MIME body.
- Action semantics: Gmail has no true folders — it has **labels** (a
  message can carry multiple). "Filing" an email means applying a label
  and, optionally, removing the `INBOX` label (equivalent to archiving).

#### 4.6b IMAP backend

- Auth: app-specific password or OAuth2-over-IMAP (provider-dependent),
  stored outside the repo (keychain / env var / local secrets file).
- List: `SELECT INBOX` then `SEARCH` (e.g. `UNSEEN`, or all, depending on
  reprocessing strategy).
- Get: `FETCH <id> (BODY[])`, parse via `email.message_from_bytes`,
  extract headers and decode multipart MIME body (same decoding logic can
  be shared with the Gmail backend if Gmail's raw format is fetched via
  `format=raw` instead of `format=full`).
- Action semantics: true folders. "Filing" an email means `COPY` to the
  target folder followed by marking `\Deleted` + `EXPUNGE` on the
  source (or a provider's native `MOVE` extension, e.g. Dovecot/Gmail
  IMAP both support `MOVE`).
- Works against any IMAP provider — iCloud Mail, Fastmail, self-hosted,
  or Gmail accessed via IMAP instead of the Gmail API (useful if API
  quota/OAuth setup is undesirable for a given account).

Both backends normalize into the same `EmailMessage` shape (§4.1a) before
anything touches the classifier, so switching backends is a config change,
not a pipeline rewrite.

## 5. Data flow / idempotency

- Track processed message IDs locally (SQLite or a flat JSON file) so
  re-runs don't reprocess the same messages.
- Support a **dry-run mode**: classify and log intended actions without
  calling the Gmail API's `modify` endpoint. Review before going live.
- Batch processing should be resumable — write results incrementally, not
  held only in memory, so a crash mid-run doesn't lose progress.

## 5a. Activity logging (hard requirement)

Every processed message — regardless of whether an action was taken, or
whether it was a dry run — must produce a log entry sufficient to audit
that the action taken actually matched the classification result. This is
a hard requirement, not a later polish item: it's the mechanism for
verifying the pipeline is doing what it's supposed to, especially before
trusting it against a real inbox unattended.

**Log format:** structured, one JSON object per line (JSONL) —
append-only, easy to grep/tail/load into a dataframe for review.

**Per-message log entry:**

```json
{
  "timestamp": "2026-09-21T20:10:00-04:00",
  "backend": "gmail_api",
  "message_id": "18f2a...",
  "subject": "50% off today only!",
  "sender": "deals@someshoesite.com",
  "classification": {
    "category": "marketing",
    "priority": "low"
  },
  "classification_source": "model",
  "rule_matched": "Marketing / Low priority",
  "action_decided": "file_low_priority",
  "action_target": "Marketing",
  "action_executed": true,
  "dry_run": false,
  "error": null
}
```

**Notes specific to the local-model design:**
- `classification_source` records whether the result came from the local
  model (`"model"`) or the known-sender rule table (`"known_sender"`,
  §4.4) — useful for auditing how often the shortcut is actually firing
  vs. falling through to the model.
- Unlike the Jev variant (`JEV-EMAIL-PROCESSING.md` §10.5), a local
  instruct model via Ollama/MLX doesn't natively return a calibrated
  confidence score the way Jev claims to. If confidence-gating is wanted
  here too (e.g. "only auto-file when the model is clearly sure"), that
  needs its own mechanism — e.g. asking the model to self-report a
  confidence field in the schema and treating it skeptically, or running
  classification twice and checking agreement — rather than assuming a
  trustworthy built-in score. Document this explicitly rather than
  silently treating local-model output as equally calibrated to Jev's.

**Requirements this format satisfies:**
- Every entry ties the final action back to exactly what the classifier
  returned and which rule matched, so a reviewer can answer "why did this
  happen" without guessing.
- `action_executed: false` + populated `error` covers failed API calls
  distinctly from `dry_run: true` (intentionally not executed) — these
  must not be conflated in review.
- `dry_run` as its own field means dry-run and live entries can be
  reviewed side by side, useful when validating a rule change before
  going live.

**Review surface:** at minimum, a way to query the log for everything
actioned in a given window, everything where `action_executed: false`
(needs manual follow-up), and a diff between two runs' decisions for the
same message ID (useful when iterating on rules). A flat JSONL file is
fine to start; move to SQLite if volume makes grep-based review unwieldy.

## 6. Do we need to train the model?

No. This is empirically expected to be solvable with:
1. A well-specified prompt (explicit category definitions, few-shot
   examples), and
2. The known-sender rule table for repeat cases (§4.4).

Fine-tuning would only be worth considering if a large, idiosyncratic
taxonomy consistently confused a well-prompted base model — unlikely for a
personal inbox with a handful of categories.

## 7. Privacy model (explicit)

| Data path | Who sees email content? |
|---|---|
| Gmail API (list/get/modify) | Google (already has it — your account) |
| Local model (Ollama/MLX, on-device) | No one — stays on your machine |
| Jev / any cloud classification API | **Excluded from this design** — considered and rejected; TLS only protects transit, not the fact that the provider's servers process plaintext content to classify it |

This design's privacy property is specifically: **no third party beyond
Google itself (which already has the mail) ever sees email content.**
Classification logic and decision-making happen entirely locally.

## 8. Alternative / additional backend: Apple Mail (AppleScript)

For an iCloud (or other IMAP-backed) account already set up in Apple
Mail.app, a third option beyond the `ImapSource`/`GmailApiSource` pair is
scripting Mail.app directly via AppleScript, rather than talking IMAP
ourselves:

```applescript
tell application "Mail"
    move theMsg to mailbox "Marketing" of theAccount
end tell
```

This would be a third `MailboxSource` implementation
(`AppleMailSource`), useful mainly if you want Mail.app's own
account/rules UI to stay the system of record rather than connecting
directly via IMAP. Not needed for the Gmail-primary use case, and IMAP
already covers iCloud/other providers without going through Mail.app —
documented here only as a future option if it's ever preferable to let
Mail.app manage the connection.

## 9. Open items / next steps

- [ ] Implement `MailboxSource` ABC + `GmailApiSource` + `ImapSource`
      (§4.1a) so backend choice is a config change, not a code change.
- [ ] Decide persistence layer for processed-message tracking (SQLite vs.
      flat file) — needs to key on `(backend, msg_id)` since IDs aren't
      comparable across backends.
- [ ] Decide config format for action rules (YAML sketched above) and how
      a rule's `target` maps differently per backend (label vs. folder).
- [ ] Write MIME body-decoding helper — shareable between Gmail API
      (`format=raw`) and IMAP (`FETCH BODY[]`) since both ultimately hand
      you an RFC 822 message.
- [ ] Build dry-run reporting (e.g. CSV/table of classification decisions
      before any `apply_action` calls are made), backend-agnostic.
- [ ] Extend schema: candidates are `requires_human_review`,
      `sentiment`, `requires_reply`.
- [ ] Build out `KNOWN_SENDERS` table interactively (e.g. a CLI command to
      approve/correct a classification and persist it as a rule).
- [ ] Benchmark Mistral Small 3 vs. Gemma 4 26B A4B on a sample of real
      (redacted) emails for classification accuracy before committing to
      one model long-term.
- [ ] Decide whether label/folder creation happens automatically or
      requires manual pre-creation (Gmail label vs. IMAP `CREATE`).
- [ ] Decide whether v1 targets Gmail API only, IMAP only, or both from
      the start — architecture supports both, but implementation order
      still needs picking.
- [ ] Implement the activity log (§5a) before running this unattended or
      against a real inbox at volume — this is a hard prerequisite, not a
      later polish item.
- [ ] Decide if/how to add a confidence signal to local-model output
      (§5a) if confidence-gating is wanted, since it isn't native the way
      Jev's is.
- [ ] Build the log review surface (queries listed in §5a) — at minimum a
      script to query the JSONL log.

## 10. Reference: rough starter script

See `email_classifier_sketch.py` (delivered separately) for a minimal
working sketch of the schema + classify + stub-action pipeline using
Ollama. This is the starting point to build out in Claude Code —
Gmail ingestion and the real action dispatcher are not yet wired in.
