# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Rules

1. Do not tell lies.
2. Do not make up facts; if something is unknown or unverified, say so.
3. Always confirm before writing files, committing, or pushing.

## Session state: `CLAUDE-STATE.md`

`CLAUDE-STATE.md` holds local working state: the current task, progress, and open questions. Read it at the start of each session, and update it as work progresses (rule 3 still applies). It is gitignored and must never be committed.

## Project state

This repo is mostly design, not yet an implementation. It contains three design docs and one throwaway sketch:

- `LOCAL-EMAIL-PROCESSING.md`: the primary design. Classification runs on-device (Ollama or MLX), so no third party sees email content.
- `JEV-EMAIL-PROCESSING.md`: a variant where the classifier is TypeSafe AI's hosted Jev API. It is identical to the local design except for the classifier component and the privacy model. It also describes an optional Claude-based dispatcher (§10) that reads a prose `rules.md`, plus multi-mailbox support.
- `POTENTIAL_CODE_SOURCES.md`: a survey of prior art. §6 holds the concrete library and pattern decisions that override parts of the design docs (see below).
- `email_classifier_sketch.py`: a minimal Ollama schema, classify, and stub-action loop. Mailbox ingestion and real actions are not wired in.

There is no package layout, dependency manifest, test suite, or linter config yet. When you add any of these, update this file.

## Running the sketch

Requires a local Ollama daemon with the model pulled:

```sh
pip install ollama pydantic
ollama pull mistral-small:7b   # MODEL_NAME in the sketch
python email_classifier_sketch.py
```

## Target architecture (read across the design docs)

Pipeline: `MailboxSource` → classifier → rule engine → action dispatcher → `MailboxSource.apply_action`.

- **`MailboxSource` ABC** (`list_messages`, `get_message`, `apply_action`) normalizes every backend into an `EmailMessage` dataclass. The planned implementations are `GmailApiSource` (labels; "filing" means add a label and remove `INBOX`) and `ImapSource` (real folders; COPY+delete or `MOVE`). `AppleMailSource` via AppleScript is a possible future option. Nothing downstream of ingestion should branch on backend.
- **Schema**: the `EmailClassification` Pydantic model lives in its own `schema.py`, so schema changes stay a single-file diff. It is versioned and extensible. Candidate fields are `requires_human_review`, `sentiment`, and `requires_reply`. If you use Jev, keep its schema registration in the same module.
- **Classifier** is pluggable (`classifier.backend: local | jev`), in the same way the mailbox backend is chosen by config.
- **Rule engine**: a known-sender lookup short-circuits the classifier call.
- **Action dispatcher**: config-driven rules with backend-neutral intent (for example, `file_low_priority` with target `Marketing`). Each `MailboxSource` translates a rule into a label or a folder operation.

## Decisions from `POTENTIAL_CODE_SOURCES.md` §6 (take precedence over the older design-doc text)

- Ollama path: use `instructor` with `EmailClassification` as `response_model`. It retries on validation failure, which replaces the sketch's raise-on-failure. Do not layer `outlines` on Ollama.
- MLX path: use `outlines`, which provides real grammar-level constraint through a logits processor.
- Known-sender rules: a TOML file, not an in-code `KNOWN_SENDERS` dict.
- `MailboxSource`: adopt capability flags (`TRUE_LABELS` vs `FOLDERS`, `LABEL_IS_MOVE`) instead of describing the label-vs-folder difference in prose.
- Action logging: a pending/applied/failed move-state pattern under the activity log. Record the behavior actually observed, not the behavior requested.

## Hard requirements

- **Activity log before any real or unattended run.** Write append-only JSONL with one entry per processed message, including dry runs. The field spec is in `LOCAL-EMAIL-PROCESSING.md` §5a and `JEV-EMAIL-PROCESSING.md` §10.5. Keep `dry_run: true` distinct from `action_executed: false` + `error`. Record `classification_source` (`model` vs `known_sender`).
- **Idempotency key** is `(backend, msg_id)` for one account and `(backend, account, msg_id)` for multiple mailboxes. Processing must be resumable, with results written incrementally.
- **Dry-run mode** classifies and logs without calling `apply_action`.
- **Privacy:** in the local variant, email content goes only to the mail provider and localhost. Never add a cloud call that carries email content to the local path. Local models give no calibrated confidence the way Jev does. Any confidence gating there needs its own mechanism; don't treat a self-reported confidence as trustworthy.
- **Model choice** excludes PRC-affiliated and Meta/X-affiliated labs (user preference). The candidates are Mistral Small 3, Mixtral 8x7B, Gemma 4 26B A4B, and Phi-4-mini.
- Credentials (OAuth tokens, IMAP app passwords) stay outside the repo in the keychain, env vars, or a local secrets file.
