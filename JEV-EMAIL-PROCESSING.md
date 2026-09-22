# Jev-Based Email Processing

An email classification and filing pipeline. Emails are classified by
**TypeSafe AI's Jev** (a hosted "System One Model" API — see
[typesafe.ai](https://typesafe.ai/blog/introducing-system-one-models-and-jev)),
with output strictly constrained to a predefined JSON schema by Jev's own
typed-decision API. Classification results drive actions (labeling,
archiving, moving) against a real mailbox via IMAP or the Gmail API.

> **Privacy note (read this first):** this variant sends email content
> (subject/sender/body) to Jev's cloud API for classification. That is a
> different privacy posture than the local-model variant
> (`LOCAL-EMAIL-PROCESSING.md`), where classification never leaves the
> machine. Here, email content is seen by both your mail provider (Gmail/
> IMAP host) **and** TypeSafe AI. If that tradeoff isn't acceptable, use
> the local-model doc instead — the two designs share the same ingestion,
> rule engine, and action-dispatcher layers and differ only in the
> classifier component (§4.2/4.3 below).

---

## 1. Goals

- Classify inbox messages against a schema (`category`, `priority`, to be
  extended) using Jev's hosted, schema-constrained classification API.
- Keep the schema versioned and easy to extend (new fields, new enum
  values) without rewriting the pipeline.
- Take configurable actions on classified mail (initially: print/log only;
  later: Gmail label + archive, or IMAP folder move).
- Take advantage of Jev's stated properties: fast (claimed 70ms-500ms
  end-to-end), cheap (input ~$0.042/MTok, output free), calibrated
  confidence per field, and guaranteed schema adherence (no type errors,
  per TypeSafe's claims).

## 2. Non-goals (for now)

- Training or fine-tuning anything. Jev is used as-is via its API; prompt/
  schema design plus a sender-based rule table is expected to be
  sufficient (see §6).
- Full email client replacement. This is a batch/background classifier +
  actor, not a mail UI.
- Multi-account support. Start with a single Gmail account
  (`rodney.marable@gmail.com`).
- Any attempt to redact/sanitize content before sending to Jev in this
  version — if that's wanted later, it's an addition to §4.2, not a
  redesign.

## 3. Architecture

Ingestion is backend-agnostic: both IMAP and the Gmail API implement the
same `MailboxSource` interface and hand the rest of the pipeline a plain
`EmailMessage` — everything downstream (classifier, rule engine, action
dispatcher) doesn't know or care which backend supplied it. The only
change from the local-model design is that the classifier box is now a
network call to Jev instead of a local model process.

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
                  │   Jev Classifier (cloud)   │   <- network call, replaces
                  │   (TypeSafe AI API,         │      the local model
                  │    schema-defined outputs,  │
                  │    calibrated confidence)   │
                  └────────────┬────────────────┘
                                │  EmailClassification
                                │  (+ per-field confidence)
                                ▼
                  ┌─────────────────────────┐
                  │   Rule Engine               │
                  │   (known-sender lookup      │
                  │    short-circuits the API    │
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

**Data-exposure property (explicit, since it's the main design change from
the local variant):** email content leaves the machine and is sent to
Jev's API for every message not short-circuited by the rule engine (§4.4).
TLS protects that call in transit; it does not stop TypeSafe's servers
from processing plaintext content to produce the classification. See §7.

## 4. Components

### 4.1a Ingestion abstraction (IMAP + Gmail API)

Unchanged from the local-model design — same interface, same two
implementations:

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

- **`GmailApiSource`** — OAuth, `users().messages().list/get/modify`,
  actions expressed as label add/remove (see §4.6a).
- **`ImapSource`** — `imapclient`/`imaplib`, works against any IMAP
  provider, actions expressed as folder moves / flag changes (see §4.6b).

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

Defined as a Pydantic model, and *also* mirrored as a Jev schema
definition (Jev requires possible outputs/structure to be
[defined in advance](https://docs.typesafe.ai/) on their side, e.g. via
their console/schema registry — confirm exact mechanism against current
Jev docs when implementing, since this is API-surface detail not covered
in the announcement post).

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
changes are a single-file diff. If Jev's API requires its own
schema-registration step (separate from your Pydantic model), keep that
registration in the same module so the two never drift apart.

### 4.2 Jev API client

Replaces the local model runtime. Per TypeSafe's announcement:

- **Input:** unstructured state (here: subject + sender + body, packed
  into a "state" payload) plus a set of predefined `questions` (the
  schema fields).
- **Output:** typed, structured values — one per question — each with a
  calibrated confidence score. No free-text generation; Jev "can't
  hallucinate" outputs outside the defined schema, per TypeSafe's claims.
- **Cost (per the announcement):** input ~$0.042/MTok; output tokens
  free. Cheap enough that per-email classification cost is negligible at
  personal-inbox volume.
- **Speed (per the announcement):** claimed 70ms–500ms end-to-end,
  vs. seconds for a typical LLM call — parallel sampling rather than
  autoregressive generation.
- **Auth:** API key via TypeSafe's console (`console.typesafe.ai`) —
  exact SDK/endpoint shape to be confirmed against current docs when
  implementing (the public announcement doesn't specify the full REST/SDK
  contract).

```python
def classify_email(subject: str, sender: str, body: str) -> EmailClassification:
    state = f"From: {sender}\nSubject: {subject}\nBody: {body[:1500]}"
    response = jev_client.query(
        state=state,
        schema=EmailClassification,  # exact API shape TBD — see Jev docs
    )
    # response includes both values and per-field confidence
    return EmailClassification.model_validate(response.values)
```

**Confidence scores:** unlike the local-model design, Jev returns a
calibrated confidence per field. Worth using this rather than discarding
it — e.g. route low-confidence classifications to a "needs review" bucket
instead of silently filing them (see §4.4/§9).

### 4.3 Classification function — differences from local variant

Same signature and role as the local design's `classify_email()`, but:
- No prompt engineering with few-shot examples in the traditional sense —
  Jev's interface is state + predefined questions, not a freeform prompt,
  so "prompting" here mostly means writing clear, descriptive schema field
  names/descriptions (per the announcement's side-by-side demo, which
  notes descriptive, human-readable keys were chosen specifically to make
  output legible).
- No local validation-failure branch needed for schema mismatches — Jev
  guarantees schema conformance by construction (per TypeSafe's claims);
  still worth a defensive `try/except` around the API call itself for
  network/auth failures, timeouts, etc.

### 4.4 Rule engine (known-sender shortcut)

Same rationale as the local design, with an added benefit here: it also
reduces API cost and network dependency, not just latency.

```python
KNOWN_SENDERS: dict[str, EmailClassification] = {
    "deals@someshoesite.com": EmailClassification(category="marketing", priority="low"),
}

def classify(subject, sender, body) -> EmailClassification:
    if sender in KNOWN_SENDERS:
        return KNOWN_SENDERS[sender]
    return classify_email(subject, sender, body)  # falls through to Jev
```

This table should be persisted (JSON/SQLite) and easy to append to as
patterns are discovered — e.g. after a manual override, or after noticing
a repeat sender consistently comes back with high Jev confidence on the
same category.

### 4.5 Action dispatcher

Unchanged from the local design — backend-agnostic, delegates to whichever
`MailboxSource` is active:

```python
def take_action(msg: EmailMessage, result: EmailClassification, source: MailboxSource):
    print(f"[{result.category} / {result.priority}] {msg.subject}")  # stub
    # Later: look up a matching rule and call source.apply_action(...)
```

```yaml
rules:
  - if: {category: marketing, priority: low}
    action: file_low_priority
    target: "Marketing"   # Gmail: label name / IMAP: folder name
```

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
- List: `SELECT INBOX` then `SEARCH`.
- Get: `FETCH <id> (BODY[])`, parse via `email.message_from_bytes`,
  extract headers and decode multipart MIME body.
- Action semantics: true folders — `COPY` + `STORE \Deleted` (or native
  `MOVE`) into the target folder.
- Works against any IMAP provider — iCloud Mail, Fastmail, self-hosted,
  or Gmail accessed via IMAP instead of the Gmail API.

Both backends normalize into the same `EmailMessage` shape (§4.1a) before
anything touches the classifier, so switching mailbox backends is a
config change, not a pipeline rewrite — and swapping the classifier
between this Jev variant and the local-model variant is *also* just a
config/component swap, since both consume `EmailMessage` and produce
`EmailClassification`.

## 5. Data flow / idempotency

- Track processed message IDs locally (SQLite or a flat JSON file) so
  re-runs don't reprocess the same messages and don't re-spend Jev API
  calls unnecessarily.
- Support a **dry-run mode**: classify and log intended actions without
  calling `apply_action`. Review before going live.
- Batch processing should be resumable — write results incrementally, not
  held only in memory, so a crash mid-run doesn't lose progress or force
  re-paying for already-classified messages.
- Consider caching Jev responses per message ID so a re-run (e.g. after a
  rule-engine change) doesn't re-call the API for messages already
  classified.

## 6. Do we need to train anything?

No. Jev is used purely via its inference API; there's no fine-tuning step
in this design. Schema/question design (clear field names, per §4.3) plus
the known-sender rule table (§4.4) are expected to be sufficient, same
reasoning as the local-model variant.

## 7. Privacy model (explicit)

| Data path | Who sees email content? |
|---|---|
| Gmail API / IMAP (list/get/modify) | Your mail provider (already has it — your account) |
| Jev API (TypeSafe AI, cloud) | **TypeSafe AI** — every message not short-circuited by the rule engine |
| Local model (Ollama/MLX) | N/A in this variant — see `LOCAL-EMAIL-PROCESSING.md` for the design where classification never leaves the machine |

This design's privacy property is: **email content is sent to Jev's
servers for classification.** TLS secures that call in transit but does
not change what Jev's servers can see — encryption in transit isn't the
same thing as keeping content private from the provider being called.
If avoiding third-party exposure of email content is the priority, prefer
the local-model design instead; this Jev variant trades that off for
Jev's claimed speed/cost/calibration properties.

## 8. Alternative / additional mailbox backend: Apple Mail (AppleScript)

Same as the local-model design — a third possible `MailboxSource`
(`AppleMailSource`) via AppleScript, for accounts already managed in
Mail.app, if IMAP-via-Mail.app is preferred over talking IMAP directly.
Not needed for the Gmail-primary use case.

## 9. Open items / next steps

- [ ] Confirm Jev's actual API contract (REST endpoint, SDK, auth flow,
      exact schema-registration mechanism) against current TypeSafe docs
      — the public announcement post describes behavior and pricing but
      not the full API surface.
- [ ] Decide how to use per-field confidence scores: threshold for
      auto-filing vs. routing to a "needs review" bucket.
- [ ] Implement `MailboxSource` ABC + `GmailApiSource` + `ImapSource`
      (§4.1a) so backend choice is a config change, not a code change.
- [ ] Decide persistence layer for processed-message tracking (SQLite vs.
      flat file), keyed on `(backend, msg_id)`, and cache Jev responses
      to avoid re-billing on reprocessing.
- [ ] Decide config format for action rules and how a rule's `target`
      maps differently per backend (label vs. folder).
- [ ] Write MIME body-decoding helper shared between Gmail API and IMAP.
- [ ] Build dry-run reporting (classification decisions + confidence
      scores, before any `apply_action` calls are made).
- [ ] Extend schema: candidates are `requires_human_review`,
      `sentiment`, `requires_reply`.
- [ ] Build out `KNOWN_SENDERS` table interactively.
- [ ] Decide whether label/folder creation happens automatically or
      requires manual pre-creation.
- [ ] Decide whether v1 targets Gmail API only, IMAP only, or both.
- [ ] Revisit the privacy tradeoff in §7 explicitly before running this
      against a real inbox — confirm this is the intended design vs. the
      local-model variant.
- [ ] Decide rule-engine vs. Claude-based dispatcher (§10) — and if
      Claude-based, build the skill that reads `rules.md` + Jev output
      and picks an action from the fixed vocabulary.
- [ ] Implement the activity log (§10.5) before any unattended/scheduled
      run — this is a hard prerequisite for multi-mailbox automation, not
      a later polish item.
- [ ] Build the log review surface (queries listed in §10.5) — at minimum
      a script; a small dashboard if this gets used regularly.

## 10. Claude-based dispatcher (optional extension)

An alternative to the pure rule-engine dispatcher (§4.4/§4.5): replace the
YAML rule lookup with a Claude Code skill that reads the Jev classification
plus a natural-language `rules.md` and picks an action. This does **not**
let Claude freely operate the mailbox — it still selects one action from a
fixed vocabulary (`file`, `archive`, `flag`, `leave`), and the actual
mailbox mutation still goes through the same `MailboxSource.apply_action`
code path. What changes is the reasoning step between classification and
action.

### 10.1 Why

A YAML condition tree struggles with exceptions and overrides — e.g.
"archive marketing unless it's a brand I've ordered from recently" or
"treat security alerts as high priority regardless of what the classifier
said." These are natural to express in prose and awkward as nested
conditionals. Claude reads `rules.md` (which the user edits directly, no
code changes needed) and applies that judgment per batch.

### 10.2 `rules.md` (example)

```markdown
# Email Filing Rules

## Marketing
- Low priority marketing → archive to "Marketing" label.
- Exception: if sender is a brand ordered from in the last 90 days,
  leave in inbox instead.

## Notifications
- Shipping/delivery notifications → archive to "Notifications".
- Security alerts (password changes, new sign-ins) → always leave in
  inbox and flag as high priority, regardless of Jev's classification.

## Uncertain cases
- If Jev's confidence on category or priority is below 0.7 → leave in
  inbox, log to the review queue, take no action.
```

### 10.3 Multi-mailbox support

The design generalizes to multiple mailboxes (e.g. shared/service inboxes
like `info@domain.com`, `requests@domain.com`), each with its own
`MailboxSource` instance and its own `rules.md`, since triage logic
plausibly differs per mailbox:

```yaml
mailboxes:
  - name: info
    backend: gmail_api
    account: info@domain.com
    rules: rules/info.md
  - name: requests
    backend: gmail_api
    account: requests@domain.com
    rules: rules/requests.md
```

**Idempotency key** becomes `(backend, account, msg_id)` — message IDs
aren't globally unique across accounts, so the local/personal-inbox
design's `(backend, msg_id)` key needs the account dimension added.

### 10.4 Scheduling

Running this on an interval (e.g. every 10 minutes) is a scheduling
concern, not a pipeline concern — the batch-processing function itself
stays the same; a scheduler (cron/launchd locally, or a managed scheduled
task) just invokes it repeatedly. No architectural change beyond adding
`(backend, account, msg_id)` idempotency so repeated runs don't
reprocess or re-bill already-classified messages.

**Unattended operation is a bigger trust step than one-off/reviewed
runs**, especially for shared/customer-facing mailboxes — a wrong
auto-archive on a real customer request is a more costly mistake than
mis-filing a personal newsletter. This raises the bar on §10.5 below.

### 10.5 Activity logging (required, not optional)

Every processed message — regardless of mailbox, whether an action was
taken, or whether it was a dry run — must produce a log entry sufficient
to audit that the action taken actually matched what Jev returned and
which rule fired. This is the primary safety mechanism for unattended
operation (§10.4), and it is a hard requirement, not a nice-to-have.

**Log format:** structured, one JSON object per line (JSONL) — easy to
grep, tail, or load into a dataframe for review; append-only.

**Per-message log entry:**

```json
{
  "timestamp": "2026-09-21T20:10:00-04:00",
  "mailbox": "info",
  "account": "info@domain.com",
  "backend": "gmail_api",
  "message_id": "18f2a...",
  "subject": "50% off today only!",
  "sender": "deals@someshoesite.com",
  "jev_classification": {
    "category": "marketing",
    "category_confidence": 0.97,
    "priority": "low",
    "priority_confidence": 0.91
  },
  "rule_matched": "Marketing > Low priority marketing",
  "rule_source": "rules/info.md",
  "action_decided": "archive",
  "action_target": "Marketing",
  "action_executed": true,
  "dry_run": false,
  "error": null
}
```

**Requirements this format satisfies:**
- Every entry ties the final action back to (a) exactly what Jev
  returned, including confidence, and (b) which rule/section of
  `rules.md` the dispatcher (Claude or rule engine) matched — so a
  reviewer can answer "why did this happen" without guessing.
- `action_executed: false` + populated `error` covers failed API calls
  (rate limits, auth expiry, etc.) distinctly from `dry_run: true`
  (intentionally not executed) — these must not be conflated in review.
- `dry_run` as its own field (not a separate log file) means dry-run and
  live entries can be reviewed side by side, e.g. to validate a new
  `rules.md` change before flipping it live.

**Review surface:** at minimum, a way to query the log for:
- Everything actioned in the last N hours, per mailbox.
- Everything below the confidence threshold (should all show
  `action_decided: null` / `leave`).
- Everything where `action_executed: false` (needs manual follow-up).
- A diff between two runs' decisions for the same message ID, useful
  when iterating on `rules.md`.

Given the volume from a 10-minute polling interval across multiple
mailboxes, a flat JSONL file is fine to start; if volume grows, this is a
natural fit for SQLite so the review queries above can be indexed rather
than grepped.

## 11. Relationship to the local-model design

This document is a variant of `LOCAL-EMAIL-PROCESSING.md`: ingestion
(§4.1a, §4.6), the rule engine shape (§4.4), and the action dispatcher
(§4.5) are identical. The only substantive change is the classifier
component (§4.2/4.3) and its consequence for the privacy model (§7). A
future implementation could support both classifiers behind a config flag
(`classifier.backend: jev` vs. `classifier.backend: local`), same as the
mailbox backend is config-selected.
