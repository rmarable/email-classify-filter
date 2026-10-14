# v3.0.0 step 2: Jev live check, then adapter, key, pin, failures and logging (build plan)

- **Status:** Draft 5, 2026-10-14, **approved by the operator 2026-10-14**. Draft 4 (approved 2026-10-14, committed
  `4d13ab1`) amended for the removal of Gemma and Ollama (operator decision 2026-10-14, R-D11 of
  the removal plan; ADR 0031 to come): a Jev eval run has no actor (preset D's actor is measured
  through `/ecf-eval --preset D`); comparisons are Jev vs C; the Ollama, `EXCLUSIVE`, battery and
  `--no-actor` clauses go; corpus runs no longer exist (removal R-D5), so P5 holds by
  construction plus a guard; step 4 dispatches in a new module (`pipeline.py` is removed). 2a and
  2c, built and committed (`3617fc4`), are unchanged apart from `systemone.ask` going in the
  removal. §2.2 still records `main` at `bc4468b`, before the removal.
- Draft 4 history: Draft 3 (Phase R P1-P32 applied) plus the operator's review
  of draft 3: A (L10 isolation vs L9 determinism), B (first-case refusal message), C (rescore
  keeps `incomplete_reason` and `format`), and the small points (early-stop count, `accept`'s
  current tree, a spent waiting budget keeps a partial result, `systemone` line). The 15 choices
  in §12 are accepted as written (operator, 2026-10-14), including the two partial deviations
  noted there (P4 prefix note, P18 cancel).
  - **Next:** 2a-2c are built on `jev-step2` (based on
    `4d13ab1`); S4 has merged (`f88971b`), so `jev-step2` is **rebased on `main`** and merged
    with its own macOS gate before the removal starts (removal P7). 2d is written after the
    removal merges (order: S4, `jev-step2`, the removal, then 2d; removal R-D2).
  - **What the removal changes in 2a/2c's code (removal P7, done on the removal branch):**
    `test_systemone.py` stops using `local_schema`; the AST import test's module list drops
    `pipeline` and `modelq` (deleted; `find_spec` would return None); the import-linter
    contract text names the step-4 dispatch module instead of `pipeline`; `systemone.PREAMBLE`
    is inlined byte-identical, so Jev's prompt digest doesn't move.
  - Operator decisions: D1-D12 and the Phase R decisions P1-P32, both 2026-10-14
    (`state-archive/jev-step2/operator-decisions.md`).
  - Findings: `state-archive/jev-step2/review-findings.md`, with reviewer detail in `safety.md`,
    `measurement.md`, `cost-design.md` and `performance.md`.
  - Earlier drafts (1-3) are kept in the session scratchpad, not the repo.
- **Base:** `main` at `bc4468b` (working tree, with uncommitted CHANGELOG, SPEC and planning-doc
  edits). S4 refers to the uncommitted `../ecf-s4` tree (branch `s4-user-layer`, 49 files). Code
  line numbers are from `main`. SPEC and planning-doc line numbers may move once those edits are
  committed. "Unverified" means not checked in code or docs.
- **Scope:**
  - PLANNING (`planning-docs/v3.0.0-PLANNING.md`, draft 4): §4.3 and §5 step 2, plus what §4.4
    (step 3) needs from step 2.
  - Schema doc ("SCH", `v3.0.0-OPERATOR-SCHEMA.md`, draft 3): §4 and §7.
  - Preset D, "Jev classifier + Claude actor": its eval path is here; its service path is step 4.
- **Behind the eval only:** nothing in the service's classifier path calls Jev in step 2.
  - TypeSafe receives only the live check's invented text and the frozen synthetic set.
  - Corpus (real-mail) runs no longer exist after the removal (R-D5); the service still refuses
    an eval request that names a corpus session (P5).
  - Real mail first goes to TypeSafe through a preset D address, in step 4.
- **Numbers:**
  - OD-503: step 2 design (D2-D12, P1-P32).
  - OD-504: payload (D1).
  - OD-505: reserved for live-check decisions.
  - All three are reserved in `CLAUDE-STATE.md` (P30, done).
  - No new ADR; ADR 0028 is amended. The next free ADR stays 0031.
- **Parked (not raised):** Jev's base model (OD-494), and whether request content reaches
  ClickHouse or Cloudflare.

## 1. What step 2 is and is not

**Is:**
- **2a:** the per-provider key routes and `ecf models api-key ... --provider`.
- **2b:** the live check (39 calls, run by the operator in Terminal).
- **2c:** `jev.py`: the HTTPS transport, pin, error classes, `Retry-After` tiers, backoff, AIMD and
  circuit breaker objects, probe and `set_key`.
- **2d:** eval wiring:
  - `ecf eval run --classifier-backend jev`;
  - `/ecf-eval` with preset D;
  - result format version and rescore preservation.
- Docs, CHANGELOG, ODs and the §13 corrections.
- A two-agent diff review before step 3's runs.

**Is not:**
- **The service path (step 4):** preset D on an address, the dispatch point (P9), the `addresses`
  rebuild, gate keys in `items.pinned_models` and `gate`, the `model_calls` migration, and the
  breaker as the Jev-down switch. Also step 4: the probe schedule, Slack notices, the `ecf init`
  step, step-up on adding a D address (D3), and `jev.lock` in `release.json` and `upgrade_check`
  (P32).
- **Step 3's figures:**
  - timeout, retry, waiting-budget and breaker numbers;
  - middle-band bounds and the confidence bar;
  - `score` vs `choice`;
  - state format and preamble placement;
  - the 16 KB schema budget and P11;
  - concurrency above 1.
- **Middle band and confidence-gated hiding:** steps 3-4. Step 2 records the probabilities only.
- **User fields to Jev:** decided in step 4, after step 3 measures them (D9). Jev evals use
  `schema: default`.
- **A Claude-eval `--no-redact`:** not built (P2).

## 2. Facts checked

### 2.1 TypeSafe docs (docs.typesafe.ai, all read 2026-10-14)

| Fact | Page |
|---|---|
| `POST https://api.typesafe.ai/v1/systemone`; `Authorization: Bearer <API_KEY>`; `Content-Type: application/json` | /api |
| Body `state` (string, object or array), `model`, `questions` (map), all required; "Keys are not sent to the model." | /api |
| `noul`: `instructions`, optional `criteria` {`true`, `false`}. `choice`: `criteria` option → description (or null), at most 255. `score`: `criteria` ordered low → high, 2-10 levels | /api, /primitives/choice, /primitives/score |
| "The option names and their descriptions are both sent to the model" (choice) | /primitives/choice |
| Response `model` (e.g. `jev-1.13.0`), `answers` keyed by question id, `usage` {`input_tokens`, `output_tokens`}. Per answer type: noul → `noul`; choice → `choice`, `probabilities`, `confidence`; score → `score` (expected value), `legend`, `probabilities` keyed by level index as a string, `confidence` | /api, /sdk/python/api/types/responses |
| Errors 401, 422 ("the body details the offending field"), 429, 529; no 400 listed; no error-body schema | /api |
| "When you receive a 429 ... or 529 ..., retry the request with exponential backoff" | /api ("Handling rate limits") |
| A request over either rate limit "returns 429"; the SDKs "honor the `retry-after` header when the response carries one" (so the header is conditional, not documented as always sent) | /models |
| The SDK maps 400, 401, 403, 404, 422, 429 and 5xx; 529 isn't named | /sdk/python/api/exceptions |
| Request ID: the response header `x-typesafe-request-id` | /sdk/python/api/types/responses, /sdk/python/api/exceptions, /sdk/javascript/api/interfaces/WithResponse |
| SDK retries: `max_retries` 2; statuses 408, 429, 500-599; backoff 0.5 s doubling to 5 s, jitter 0.25; honours `Retry-After` and `retry-after-ms`; `timeout` 30 s total | /sdk/python/api/retries |
| SDK constants: `DEFAULT_TIMEOUT` 10.0 s per HTTP operation; `DEFAULT_MODEL` `jev-latest`; `TYPESAFE_*` env vars | /sdk/python/api/constants |
| SDK `debug` logs request and response bodies, unredacted | /sdk/python/usage |
| SDK key rules: whitespace stripped; empty, internal-whitespace, control and non-ASCII keys rejected | /sdk/python/usage |
| `jev-1.13.0`; aliases `jev-latest`, `jev-preview` (→ 1.13.0); `model` reports the versioned ID; pin when thresholds are tuned | /models |
| `GET /v1/models` "currently lists the aliases. Versioned IDs ... are accepted whether or not they're listed." | /models |
| 64k tokens per request; 32k for state + longest question; 100K tokens/s and 80 requests/s, "dynamic" | /models |
| $0.042 per million input tokens; output free | /models |
| Other languages "handled but not equally well"; text only; state "an object for most requests" | /models, /concepts/state |
| "Every question is evaluated in parallel and in isolation against the same state"; "Adding questions barely changes the response time" | /introduction |
| Weaknesses incl. literal reading, dates as text, weak score calibration, a first-option lean, adversarial content not hostile by default, large or irrelevant state | /model-jaggedness/jev-1.13 |
| noul thresholds; middle values "can go to a person"; confidence is a spread statistic | /primitives/noul, /confidence |
| Key from console.typesafe.ai/keys; key format not documented | /introduction/quickstart |
| DPA, MCA, privacy policy; ZDR for enterprise only | /legal |

**Not in the docs:**
- over-size behaviour;
- whether a 429 always carries `Retry-After`;
- `x-ratelimit-*` headers;
- a quota or billing error code;
- latency;
- determinism;
- retirement policy;
- whether `GET /v1/models` needs the key;
- a key prefix;
- the SDK's `weight`.

### 2.2 Code (main `bc4468b` unless stated)

**`systemone.py`, the existing adapter:**
- questions are built at `:94-105`; ordinal criteria are names only (`:101`);
- the state is the preamble plus the delimited excerpt, at 2,400 bytes (`:43-50`, `:108-110`);
- the mapper runs at `:117-164`, with ties going up at `:157`, and drops `confidence` and
  `output_tokens`;
- HTTP 400 is a failed attempt (`:176-177`);
- it imports `classifier` at module level (`:40`, `:50`).

**S4's tree:** also edits `systemone.ask` (`without_extension`). Hunks are disjoint from
`questions()` (cost-design H1, verified by that reviewer).

**Remote HTTPS precedent:** `model_watch.http_client()` (`:82-85`) uses `timeout=10`,
`trust_env=False` and `follow_redirects=False`. `set_key` is at `:497-522`; its hints are at `:11`
and `:336-337`.

**Key route today:**
- `/v1/models/api-key` (`api.py:1253-1282`) reads only `key` (`:1261`) and calls
  `model_watch.set_key`.
- The CLI is at `cli_models.py:225-245`.
- `LocalClient.request` (`ecf/client.py:54-78`) does no version handshake.
- `/v1/status` reports `version` and `api_version` (`api.py:263-266`). `API_VERSION` is only
  reported; nothing enforces N/N-1. The enforced cross-version rule is `min_client`
  (`upgrade_check.py:149-150`).
- The CLI's socket timeout is 10 s (`ecf/client.py:15`).

**Secrets:**
- the regex is `secretstore/__init__.py:15-18`;
- `regrant.FIXED_NAMES` is `:28`, and `destroy` uses `regrant.names` (`destroy.py:331`);
- `open_store(..., interactive=False)` turns Keychain interaction off, so an untrusted read fails
  at once (-25293 → `SecretStoreNeedsYouError`; `select.py:65-70`, `keyring_store.py:26-31`);
- reads on the same interpreter are silent (SPEC §21.1, V1.0 row);
- `main` and `../ecf-s4` resolve to the same Homebrew `python3.12` (cost-design, checked).

**Security Notice:** `slack_admin.notice` (`:364`), passed in as at `addresses.py:315-317`.

**What each classifier gets:**
- Gemma gets the excerpt only (`message.py:38`, `:161-165`).
- Claude gets `from`, `subject`, `date`, the excerpt, attachment metadata and the signals
  (`claude_review.py:692-715`; eval: `claude_eval.py:393-402`). The eval's date is the card's
  `Date:` header; the service uses `internaldate` or `created_at`.
- 297 of 302 cards have no attachment.
- Redaction is body-only (`triggers.redact_injection`, `triggers.py:434-439`, OD-254), so
  subjects and display names are sent raw today.

**Eval:**
- the backends are listed at `evalrun.py:128-130`, `:594-603`;
- the corpus refusal covers only `not redact` and `NULL` (`:540-542`); the CLI corpus check
  (`cli.py:1277-1289`) passes any backend;
- the case loop is sequential (`:779-828`), and per-case call metrics are sliced by list position
  (`:784`, `:821`, `_metrics` `:1288-1294`);
- Ollama readiness is checked at `:550-559` and `:772`; `environment()` makes Ollama calls
  (`:775`, `:842`); the actor digest is at `:875`;
- `_hold` takes `modelq.EXCLUSIVE` for every case (`:713-718`) and checks `RUN.stop` once per case;
- `FATAL` is at `:126`; `production` is Gemma-only (`:843`);
- `schema_digest` is v2 for any non-null backend (`:855-856`);
- `complete=not stopped` (`:844`), and determinism runs only when not stopped (`:857`);
- `summarize` counts only scored cases (`:486-492`), so an unscored case would leave `unsafe`;
- `score_parts` scores a missing classification as unsafe;
- rescore copy lists are at `:1392-1410` and `:1532-1589`.

**`/ecf-eval`:**
- `Options` has no `redact` (`claude_eval.py:93-101`), and `_prepare` always redacts (`:394`);
- preset B's source is `_a_run` (`:280-311`): it selects by `claude_pins.local_key`, and an empty
  answer is a failure;
- `start` accepts B or C (`:333-340`).

**Results:**
- `INPUT_KEYS` (`results.py:34-37`) reads top-level `prompt_digest`, `input_format` and
  `excerpt_cap`; nothing writes the last two today;
- `ResultFile` and `CaseResult` are `extra="forbid"`, with no format field;
- the `_run_caveat` complete-flag text is at `cli.py:1702-1706`.

**Logging:**
- `CONTENT_KEYS` (`ecf/log.py:28-76`) includes `state`, `value` and `message`, but not
  `questions`, `answers`, `instructions`, `criteria`, `probabilities` or `legend`;
- `httpx` is held at WARNING (`:85-86`).

**Other:**
- `model_calls` CHECKs are at `0018_model_calls.sql:10-11`.
- The import guard test is `test_systemone.py:227-237`.
- `HIDE_ACTIONS` is at `rules.py:82` on `main`; S4's `rules.py` (+238) moves it.
- The service classify step is `classifier.classify_item` (`classifier.py:146`), called from
  `pipeline.py:18`.
- `httpx` 0.28.1 defaults to `keepalive_expiry=5.0`, and `timeout=30` applies per socket
  operation (performance review, checked locally).

## 3. Stage 2a: the key (repo code, before the live check)

ecf writes secrets only through the service (CLAUDE.md; SPEC §11.6). The key command is built
first, on `jev-step2`, and served by `accept` from `../ecf-jev`.

**Routes (P4):**
- `POST /v1/models/keys/anthropic` and `POST /v1/models/keys/typesafe`.
- Each takes `{"key": ...}` or `{"clear": true}`, is `Caller.CLI` only, and is never on MCP.
- **The old `/v1/models/api-key` route is removed in v3**:
  - a v2.2.x CLI gets 404 (not found);
  - an older service answers the new paths with 404, so no key is sent anywhere;
  - SPEC §15.1 gains one sentence (P19);
  - no `api_version` bump (P19).

**CLI (D2, P4):**
- `ecf models api-key set|clear --provider anthropic|typesafe`.
- `--provider` is required; there is no default and no alias.
- The flagless form is refused, with a message naming both providers.
- The list is fixed: a new provider needs an operator destination decision (OD-503).
- **Before any prompt, the CLI reads `/v1/status` and refuses when the service's `version` is
  older than its own** ("the service is older than this ecf; restart it from this version
  first").
  - Choice: compare with `packaging.version`; equal versions pass.
  - **Limit:** between version bumps, two worktrees report the same version (e.g. `main` and
    `../ecf-s4`), so this check can't tell them apart. The per-provider route is the guard that
    works then: an S4 or `main` service 404s.

**Prefix checks (P4):**
- `anthropic` requires `sk-ant-`.
- `typesafe` refuses `sk-ant-`.
- A TypeSafe prefix is added only if TypeSafe's docs document one (today they don't,
  /introduction/quickstart). The live check never inspects or records any part of the key, so it
  can't "note" a prefix (§12, choices).
- The key's characters are checked as the SDK does: no internal whitespace, no control or
  non-ASCII characters, at most 400; ends stripped.
- All checks run in the CLI before sending, and again in the service.

**Secret:**
- `jev-api-key` is added to the regex (`secretstore/__init__.py:16-17`) and to
  `regrant.FIXED_NAMES` (`regrant.py:28`).
- That covers `ecf service regrant` and `ecf destroy` (`destroy.py:331`) for both keys.
- Never exported. Listed in SPEC §11.6.
- Note: an older checkout's `destroy` doesn't know `jev-api-key`. `ecf destroy` from `../ecf-s4`
  would leave it; the plan's clean-up uses the `../ecf-jev` CLI.

**Validation, one call, no email content (P7, P12):**
- `typesafe`: a minimal `POST /v1/systemone` (state `ecf key check`, one `noul`). It is the same
  call as the breaker probe. Timeout 5 s, so the route stays under the CLI's 10 s socket timeout.
- `anthropic`: `model_watch.set_key` with its upstream timeout lowered to 5 s for this route.
- Then `store.set`, an audit row (`models.jev_key_set` / `models.api_key_set`, with
  `{"replaced": bool}` and never the key), and a Security Notice.

**Security Notice (D3, P12):**
- Posted on every set, change and clear of **either** key, through `slack_admin.notice`.
- Placeholder text: "The TypeSafe API key was set / replaced / removed (install `<name>`); a running
  Jev eval stops. If you didn't do this, clear it with `ecf models api-key clear --provider
  typesafe`." The Anthropic text names the model watch.
- Without Slack it goes the no-Slack Security Notice way (`alerts.py:13`; unverified for
  `accept`).

**Hints and docs:**
- `model_watch.py:11` and `:336-337`;
- `doctor.py:220`;
- the `ecf models status` line (`tests/test_model_watch.py:329`);
- `docs/admin-guide.md:223`;
- SPEC §7.6.

**Step-up (D3):** none for setting the key; step 4 adds step-up for adding a D address.

**How the key gets there (D12):** right after the go-ahead, the operator runs this in Terminal:

`ecf --install accept models api-key set --provider typesafe`

- The operator pastes the key at the hidden prompt.
- Path: terminal → CLI memory → Unix socket → service memory → Keychain
  `email-classify-filter/accept`, account `jev-api-key`.
- Never the repo, the environment, a file, a log or this conversation. `$TYPESAFE_API_KEY` is
  never read.

## 4. Stage 2b: the live check (real-service test, P11)

**Rules (CLAUDE.md):**
- The operator's go-ahead first.
- A throwaway script in `scratchpad/jev-livecheck/`, never in the repo.
- Results go in SPEC §21.1 (row) and §21.2 (figures).

**One sitting, one go-ahead (P11, D12, L5):**
1. Stop `accept` (on `main` at `20acf33` when this draft was written; on `../ecf-s4` if M2 has
   started) and start it from `../ecf-jev`. For the sitting, `accept` serves its mail from the
   unmerged `jev-step2` build; Jev isn't in its classifier path (§1).
2. `ecf --install accept status` shows the step-2 version and that the new route answers.
3. The key set (§3).
4. The script.
5. Switch `accept` back to the tree it was on (or to `../ecf-s4` if M2 needs it). That tree
   doesn't know the `jev-api-key` name; the item stays in the Keychain unread until step 3.

This fits before M2's first registration or between its two runs.

**The operator runs the script in Terminal, not through Claude's Bash** (P11, safety 5). Bash output
and tracebacks land in the transcript and in tool-result files.
- The script sets a `sys.excepthook` that prints only the exception type and the check number.
- The key sits in one headers dict that is never formatted.
- Before printing or writing anything, the script asserts that no output string contains the key.
- It writes a numbers-only result file in the scratchpad, which I then read.
- It ends with a one-screen pass/stop summary.

**Key read:**
- `open_store("accept", ..., interactive=False).get("jev-api-key")`, on the `accept` worktree's
  interpreter.
- A hash mismatch fails at once (-25293). The script then stops and says so; no dialog can
  appear.

**Content:** invented text written for the check, plus one invented email in card format. No
frozen-set or held-out card, no real mail.

**Transport:** one shared `httpx.Client` for the whole script, with `trust_env=False`,
`follow_redirects=False`, `User-Agent: ecf` (D8) and the adapter's timeouts (§5.1). Calls are
sequential except L11.

**Checks (39 calls):**

| # | Calls | Request | Records | Expect |
|---|---|---|---|---|
| L1 | 1 | `GET /v1/models` with the key | status; item fields; names; `jev-1.13.0` listed? | 200; aliases only |
| L2 | 1 | `POST`, `model: jev-1.13.0`, one question of each type | status; response keys; `model`; answer keys per type; score `probabilities` key form; `legend`; `confidence`; `usage`; `x-typesafe-request-id` present; **all response header names** (incl. `retry-after*`, `x-ratelimit-*`) | 200; `model` == pin |
| L3 | 1 | the same with `jev-latest` | `model` | a versioned ID |
| L4 | 1 | `model: jev-0.0.0` | status; body keys; any error-code field's value | undocumented (retired pin) |
| L5a | 1 | `POST` with key `invalid` | status; body keys | 401 |
| L5b | 2 | `GET /v1/models` with key `invalid`, then with no header | status | undocumented (informs the doc text; the probe is a POST either way) |
| L6 | 2 | a `score` with one level; a `choice` with no criteria | status; body keys; error-code value; whether the body quotes a 16-char run of the state (boolean) | 422 |
| L8 | 2 | the full v3 set (11 fixed fields, `category`, `sender_type`) on the invented email in D1's payload, as an `s1` string and an `s2` object | status; `usage.input_tokens` against the request's byte count (bytes → tokens; is state billed once?); wall ms; httpx body bytes | 200; object accepted |
| L9 | 10 | L8's `s1` request: 5 with fresh tokens, then 5 with one fixed token `T` (P11, M7) | wall ms; per field, identical answers? probabilities to 1e-6; answers within 0.05 of a threshold; **the per-field spread (max − min) of probabilities across the 5 fixed-token calls** | undocumented |
| L10 | 10 | 1 question vs 13, interleaved (1, 13, 1, 13 ...), **every call with L9's fixed token `T`**, so the state is byte-identical | wall ms (first call of each series apart); per field, the 1-question answer and probabilities against the 13-question ones; `usage.input_tokens` for each | isolation (/introduction); see the rule below the table |
| L11 | 6 | L8's request, one burst of 2 and one of 4 | statuses; wall ms | 200s (informs a later concurrency figure only) |
| L7a | 1 | state + longest question over 32k tokens (invented filler), **run last, never retried** | status; body keys; error-code value; time | undocumented; a 429 here is recorded (perf 2e) |
| L7b | 1 | state under 32k, total over 64k, run last | the same | undocumented |

- **Rate limits:** 429 and 529 are not provoked by bursts. L7a and L7b are single over-size
  requests (P11, L6 reworded). If any 429 occurs, its header names and error-code value are
  recorded.
- **Error bodies:** only the top-level keys, a code-like field's value (P7: quota detection) and
  the quotes-request boolean are recorded. Never messages.
- **Indicative only:** L9-L11 (n ≤ 10, one machine, one time). Step 3 sets every number.
- **L10's rule (draft 4, A):** isolation can only be told apart from non-determinism once L9 has
  measured the latter.
  - If L9's 5 fixed-token calls are identical (1e-6), any L10 difference beyond 1e-6 is a **stop**.
  - Otherwise, an L10 difference is a stop only if it exceeds L9's fixed-token spread for that
    field, or the mapped answer differs while all 5 L9 fixed-token answers for that field agreed.
    A difference within the spread is recorded as "isolation not separable from
    non-determinism" (SPEC §21.2), not a stop.
- **Stop and report:** the script stops at the first difference from §2.1 in any of these: a
  status, a field name or type, `model`, `usage`, the request-ID header, or **L10 isolation** (as
  ruled above).
  - I report the check number.
  - Nothing is worked around, and no adapter code is written against the difference until the
    operator decides (OD-505 if one is needed).
  - The undocumented items (L4, L5b, L7, L9, L11) are recorded, not stops.
- **Recorded:**
  - SPEC §21.1: a row with the date, the `jev` model ID, the httpx version and the macOS
    version.
  - SPEC §21.2: statuses, shapes, L4 and L7 behaviour, header names, the bytes→tokens ratio,
    whether state is billed once, isolation, determinism (fresh vs fixed tokens), latency.
  - No key, key prefix or length, request ID, or body anywhere.
- **Cost (P21):** about 165k input tokens if the over-size requests are billed, about $0.007 at
  $0.042 per million (/models, 2026-10-14). The key-check call adds a few tokens.
- **After:** the key stays in `accept` for step 3. Remove it with
  `ecf --install accept models api-key clear --provider typesafe` (posts a Security Notice).

## 5. Stage 2c: the adapter

### 5.1 Module, imports, transport (D4, P8, P9, P17)

**`src/ecf_server/jev.py`** holds:
- the HTTPS transport and the pin;
- the error classes;
- the `Retry-After` parser and pause tiers;
- the backoff, AIMD and circuit-breaker objects;
- the probe and `set_key`.

**Imports:**
- `jev.py` imports `systemone`'s question builder and mapper (D4).
- **Nothing moves out of `systemone.py` (P9).** The only step-2 edit there is `questions()`:
  ordinal `criteria` from `value_descriptions`, lines 94-105 (P8). `ask()` is untouched; S4 edits
  it.
- `Answer` is unchanged. `jev.py` reads `confidence` and `usage.output_tokens` from the raw body
  beside `map_answers`.

**Import direction (P9):**
- `jev` → `systemone`. The removal drops `systemone.ask` and its `classifier` and `ollama`
  imports, so `systemone` keeps only `questions`, `map_answers` and `PREAMBLE`, and the P9 cycle
  can't form. Until then: `jev` → `systemone` → `classifier`.
- A new import-linter contract forbids `ecf_server.classifier` from importing `ecf_server.jev`,
  indirect imports included.
- Step 4's dispatch goes in a new dispatch module, never inside `classifier`
  (`pipeline.py` is removed with the local model, ADR 0031).
- The AST test (`test_systemone.py:227`) keeps `classifier` and `actor` free of `systemone` and
  `jev` until step 4 (`pipeline` and `modelq` leave the list when the removal deletes them).

**Jev asks the effective v3 schema directly**, with no `local_schema`/`from_v2` (both leave the
asking path with the removal; `from_v2` stays for old results and labels).

**Plain HTTPS, not TypeSafe's SDK.** The SDK reads the env key, defaults to `jev-latest`, and its
debug level logs bodies.

**Client (P17):**
- `httpx.Client(base_url="https://api.typesafe.ai", timeout=httpx.Timeout(connect=5,
  read=TIMEOUT_S, write=10, pool=5), limits=httpx.Limits(max_connections=MAX_CONCURRENCY,
  max_keepalive_connections=MAX_CONCURRENCY, keepalive_expiry=60), trust_env=False,
  follow_redirects=False, transport=...)`.
- A per-try `monotonic()` deadline (placeholder `TIMEOUT_S`, 30 s) classes a slow reply as a
  timeout, since httpx's read timeout is per `recv`.
- One client per eval run. The probe shares it.

**Headers:** `Authorization: Bearer`, `Content-Type`, `Accept`, `User-Agent: ecf` (D8), plus
`Host` and `Content-Length`.

**Key (P12):**
- read by the `start_eval` route (`state.store()`) before the run thread starts, then passed in;
- a missing key is refused at the CLI;
- **re-read before each case and at each probe** (choice: "each case" closes the clear-during-a-
  healthy-run gap, since probes only happen in faults);
- if it is gone, the run stops at once in the `auth` class ("the TypeSafe key was removed");
- never in exception text, a log or a result.

### 5.2 Request (D1, OD-504, P10, P20, P24)

**`model`:** the pin.

**`questions`:** one per field of the effective schema, in schema order, keyed by field name.
Keys aren't sent to the model.
- Boolean → `noul`: `instructions` is the description, no `criteria` by default.
- Enum → `choice`: `criteria` maps value → description. **Value names are seen by the model.**
- Ordinal → `score`: level descriptions, low → high.

**`state`, format `s1`, in order:**
1. `systemone.PREAMBLE`.
2. The signals block (`signals.block`: the fixed OD-483 wording and tokens, as Gemma got it
   before the removal).
3. Between `<<<EMAIL t>>>` and `<<<END EMAIL t>>>` (a fresh random token each request), what Claude
   gets (D1):
   - `From:` display name and address;
   - `Subject:`;
   - `Date:`;
   - one `Attachment:` line each (name, type, size; the filter at `claude_review.py:707-709`);
   - then the excerpt.

**Header lines (P10):**
- Each value is collapsed to one line (control characters and newlines → a space) before its cap
  (subject and display name 300 characters each; 10 attachment lines; placeholders).
- With redaction on, subject and display name go through the same injection check as the body,
  and a match becomes `INJECTION_MARK`.
- The date is normalised to one format in both the eval and the service (choice: RFC 3339 UTC,
  `YYYY-MM-DDTHH:MM:SSZ`; an unparseable date is sent as `unknown`).
- SPEC §12.4 says header lines are sent as received apart from that.

**Excerpt (P10):**
- the stored excerpt as Claude gets it: `CLASSIFIER_CHARS` (1,500) characters, with no byte cut
  below that;
- bytes matter only for the size refusal.

**Format `s2`** (an object with `instructions`, `signals` and `email` {from, subject, date,
attachments, text}):
- built for step 3, selected by `--jev-state s2`;
- the injection set runs on both formats before either is chosen (P26);
- choice: `text` keeps the tokenised delimiters (safety 8).

**Preamble placement:** `--jev-preamble state|instructions` (P19 measurement).

**Size (P20, P24):**
- A test measures the request as httpx sends it (`httpx.Request(...).content`) and fails above
  **16 KB** (placeholder, about the rendered worst case of 11.9 KB + 25%), calibrated from L8.
- A second test checks the state's bytes equal preamble + signals + header lines + excerpt within
  a fixed slack, so duplication fails.
- **Refusal:** the token estimator is named: tokens ≤ UTF-8 bytes (an upper bound; the choice is
  conservative because CJK runs about 1-1.5 bytes per token).
  - The adapter refuses (a request error, no call) when the estimate for state + the longest
    question passes 30k, or the total passes 62k.
  - Calibrated against `usage` in L7 and L8; the ratio goes in SPEC §21.2.
  - On the shipped schema (about 2.2k tokens) it can't fire until user fields grow.

### 5.3 Pin

- `src/ecf_server/data/jev.lock` (wheel): `{"model": "jev-1.13.0", "source":
  "docs.typesafe.ai/models", "checked": "<date>"}`. It moves to a newer stable version at build if
  there is one. A test checks `^jev-\d+\.\d+\.\d+$`.
- Every reply's `model` must equal the pin; otherwise **the run stops at once** (P7).
- **A retired pin:** a 404 or 422 on the **first case** stops the run at once (P7). The message
  depends on what L4 recorded (draft 4, B):
  - the status, and the error-code value if L4 saw one, that L4 recorded for `jev-0.0.0` →
    "the pinned Jev version isn't served";
  - any other 404 or 422 on the first case (e.g. TypeSafe rejecting ecf's own payload) → "TypeSafe
    refused the first request (HTTP n); the run stopped before scoring". Same stop, no claim about
    the pin.
  - The recorded L4 status and code are a constant in `jev.py`, set from SPEC §21.2 after the
    live check; until then every first-case refusal gets the second message.
- **Model watch:** the listing can't show retirement. Step 4/5 records the aliases'
  `release_date`. `jev.lock` joins `release.json` and the upgrade pre-check in step 4 (P32,
  OD-381).

### 5.4 Failures (D11 as revised by P1, P6, P7, P18)

All numbers are placeholders, set in step 3 and recorded in SPEC §15.4.

| Class | What came back | Handling |
|---|---|---|
| Rate limit | 429 | `Retry-After` tiers (below); without a usable header, D11's full-jitter backoff (base ~1 s, cap ~60 s); AIMD halves concurrency (floor 1). A 429 body whose error code says quota or billing (format from the live check; unverified) → **stop at once**. A 429 on a request the adapter's own estimate flags as near a limit → request error (perf 2e) |
| Provider fault | 529, other 5xx, 408, timeout or deadline, connect or TLS error, 3xx (not followed) | full-jitter backoff (base ~1 s, cap ~30 s), up to ~4 tries per case; each fault feeds the breaker |
| Auth | 401, 403, the key removed | **stop the run at once**: "TypeSafe refused the key (HTTP n); replace it with `ecf models api-key set --provider typesafe`" |
| Pin | reply `model` ≠ pin; 404 or 422 on the first case | **stop at once**; message per §5.3 (the pin message only for L4's recorded status and code) |
| Request error | 400, 404, 413, 422 after the first case; the adapter's size refusal | no retry; the case is **scored as a failure** (P1) with `fault: request_error` and the status |
| Unmapped answer | 200 that doesn't map | no retry; a failed attempt (as today) |
| Faulted (exhausted) | a provider-fault case after its tries | **scored as a failure** (P1) with `fault: provider` |

**`Retry-After` (P7):**
- The parser takes delta-seconds, an HTTP-date and `retry-after-ms`; an absent or bad header → the
  jittered backoff.
- Every wait is **run-wide** (serial, so the run pauses), with three tiers:
  - **short** (≤ ~60 s): pause exactly that long + 0-10% jitter;
  - **medium** (≤ the run's remaining waiting budget): pause, and tell the operator on the
    progress line and in the log, with the resume time;
  - **long** (> budget): **stop at once** with "TypeSafe asked to wait N min; probably a quota or
    billing limit; re-run after HH:MM".
- **Proactive pacing:** if replies carry `x-ratelimit-*` headers (L2 records them; unverified),
  the adapter waits before a call that would pass the stated remaining budget. Without them, none.
- **Step 4:** a long `Retry-After` = Jev down at once; a short one = a brief pause.

**Waiting budget:**
- One run-level budget: backoff + `Retry-After` + breaker-open time + timed-out tries'
  seconds.
- Placeholder 10 min. When it is spent, the run **stops** like `ecf eval stop`: the cases scored
  so far are kept in a result file marked incomplete (`incomplete_reason: budget`), with no
  determinism repeats and `gate_passed` false (draft 4; replaces draft 3's "fails with no result"
  and D11's "open ~10 min in total", perf 2c).

**Circuit breaker:**
- Opens after ~5 consecutive faulted tries (rate limits and provider faults count).
- While open, no calls. A try refused by the open breaker isn't spent: the case waits (perf 3a).
- Cool-down doubles from 30 s to 5 min.
- **Probe:** the minimal POST (§3, P7), not `GET /v1/models`, so it fails on auth and on a
  rate-limited bucket.
  - Success half-opens the breaker at concurrency 1.
  - A half-open fault re-opens it with the next cool-down.
  - The ladder resets only after K consecutive successes (K ~10; choice, perf 2d).
  - The probe also re-reads the key.

**Concurrency (P6):**
- **The eval loop stays serial.** There is no worker pool.
- `--jev-concurrency` defaults to 1. In step 2 it accepts only 1, and a higher value is refused as
  "not in step 2" (choice).
- The AIMD object (halve on 429, +1 after K successes, floor 1, maximum `MAX_CONCURRENCY`
  placeholder 8) is built and tested in `jev.py`. Any parallel calls later live inside the Jev
  backend, keyed by case ID.
- The per-case metrics stay one call per case through `_classify_with`.

**Early stop and incomplete (P1, P7, P22):**
- The run **stops early** once faulted cases pass the bar: more than `floor(0.02 × n)`, so it
  stops at the 7th faulted case of 304 (`floor` = 6) and at the 4th of the 155-case
  `--fraud-only` set (`floor` = 3).
- A faulted card that is a fraud, must-escalate or injection card marks the run **incomplete**
  (`incomplete_reason: faulted_safety_card`). The run continues (choice) and its determinism
  repeats still run.
- `incomplete_reason` is one of `stopped`, `faulted`, `faulted_safety_card` or `budget`, with its
  own caveat line (`cli.py:1702-1706`).

**Interruptible (P18):**
- Every wait is `stop.wait(delay)` on the injected `RUN.stop`, checked again before each try.
- An in-flight try is bounded by its deadline and its result is discarded on stop. A true
  cancel of a sync httpx request from another thread isn't relied on (unverified as safe).
- Battery pause: none. A Jev run uses no local model, and the removal deletes the battery rules
  (draft 5).

**Ties:** a score tie goes to the higher level (`:157`). A choice outside the options is an
unmapped answer.

**Error text:**
- only the status, the class and a recognised error-code value (the last kept only if it is in an
  allow-list learned in the live check);
- never a body or message.

**Step 4 (D11):** the same breaker, per install, becomes the switch.
- Open for `jev_down_after_minutes` → Claude.
- The probe is the switch-back check.
- Auth counts as down; request errors and unmapped answers don't.

### 5.5 Logging and per-case figures (D7, P14, P21, P23, P27)

- **`jev.call`, one line per try:**
  - `class`, `status`, `http_ms`, `wait_ms`, `concurrency`, `input_tokens`, `output_tokens`, `try`,
    `n_questions`, and `request_id` (`x-typesafe-request-id`, **service log only**, D7);
  - never the keys `state`, `value` or `message` (P27), and no content.
- **Breaker events:** `jev.breaker_open`, `jev.breaker_half_open` and `jev.breaker_closed`.
  **Pause events:** `jev.pause`, with tier, seconds and resume time.
- **`CONTENT_KEYS`** gains `questions`, `answers`, `instructions`, `criteria`, `probabilities`,
  `legend` and `choice`.
- **Per case (result file):**
  - `classifier_ms` is **the successful try's HTTP time only** (request sent → response read);
  - `wait_ms` (backoff, `Retry-After`, open time);
  - `queue_ms` (waiting for a concurrency slot; 0 at concurrency 1);
  - `tries`, `concurrency`, `fault`, `confidence`, `input_tokens`, `output_tokens`;
  - **no request ID.**
- **`summary.jev`:**
  - latency over `classifier_ms` per concurrency level, each with its n (P14);
  - end-to-end per case;
  - retries by class, 429s, pauses by tier, breaker trips, waiting seconds;
  - faulted cases by type (diagnostic, P1);
  - the concurrency of each determinism repeat's first pass and repeat (P23);
  - `input_tokens_billed`: the sum of `usage` over every 200 in the run, repeats and probes
    included (P21).
  - The cost line is computed from `input_tokens_billed` at "$0.042 per million input tokens,
    /models, read <date>", with what it excludes (non-200 replies, whose billing is unknown).
- **`model_calls`:** step 4. No `request_id` column (D7).

### 5.6 Exactly what is sent (SPEC §12.4, OD-504)

Over TLS to `api.typesafe.ai:443`.

**Classification:** `POST /v1/systemone`.
- Headers as in §5.1.
- `model` (the pin).
- `questions`: every schema field's description and its value or level names and descriptions.
- `state`:
  - ecf's fixed untrusted-data preamble;
  - the true derived signals in fixed wording (OD-483);
  - the sender's display name and address, subject and date (normalised), and each attachment's
    name, type and size, each on one line. With redaction on, a subject or display name that
    trips fraud trigger 10 is replaced by ecf's marker;
  - the prepared body excerpt, up to 1,500 characters. Redaction removes only what fraud trigger
    10 matched (OD-254). With `--no-redact` (synthetic `--fraud-only` runs only, P2), nothing is
    removed.

**Key check and probe:** the minimal POST, state `ecf key check`, one `noul`.

**Also visible to TypeSafe:** IP address, times, the account behind the key, call pacing.

**Nothing else.** In steps 2 and 3 only invented and synthetic text is sent. Retention,
sub-processors and the agreement are stated at ship (PLANNING §4.8; the ClickHouse/Cloudflare
question is parked).

### 5.7 Gate key and digests (P13, P31)

**`jev.gate_key(schema, rules)` = `jev:<pin>+<questions digest>+<state format id>+<mapping
digest>`.**
- **Questions digest:** the rendered questions (type, instructions, criteria; order included) for
  the **11 fixed fields** (7 safety, 3 workflow, `priority`, S4 D12) and any field a hide-capable
  rule (`HIDE_ACTIONS`) reads in the **effective rule set** (fixed + user + default; in step 2 the
  starter set, since D9 refuses user fields).
- **State format id:** covers the preamble, template, header and excerpt caps, header
  normalisation, `excerpt.VERSION`, the signals wording and preamble placement.
- **Mapping digest (P13):** per-field noul thresholds, the score tie rule, and (later) middle-band
  bounds.
- Recorded as `summary.gate_key` and a Jev run's `eval_runs.digest`, pair `jev/hosted`. No gate
  reads it until step 4.
- **Step 4 TODO (P31):** recompute with user rules, and S4's two `preset == "C"` checks
  (`config.py`, S4 diff lines 444 and 1127) for D.

**`prompt_digest`:** the preamble, the full question JSON (every field), caps, the state format,
the signals wording, the mapping digest and the pin.

**Top-level summary keys (P13):**
- `prompt_digest`;
- `input_format` (the state format id);
- `excerpt_cap` (character and header caps);
- `schema_digest`: `schema.digest` (v3) for `jev`; old Gemma and `systemone:` results keep the
  v2 digest they recorded (readable, not rescorable, removal R-D12). It is also copied in
  `summary.jev`.
- C runs write `input_format` too (`claude-v1`), so Jev-vs-C prints "different inputs".

## 6. Stage 2d: eval wiring (written after the removal merges, P8, D10, removal R-D2)

**`ecf eval run --classifier-backend jev`.** Real code changes by function (P15):
- **`Options.backend`:** `jev`; `decision_pin` returns the Jev pin.
- **`start`:** the key is passed in from the route (P12). After the removal the runner has no
  Ollama readiness check, client or model queue to skip (removal R-D6).
- **`_run`:** a Jev run classifies only; there is no actor in `ecf eval run` (D's actor is
  measured through `/ecf-eval --preset D`). `actor_digest` is None; the stop and runtime-cap
  checks stay; `environment()` records no model server.
- **`_classify_with`:** a second except-arm for `jev.JevError`. `FATAL` gains the Jev vocabulary
  (`auth`, `pin`, `quota`, `long_wait`). A spent waiting budget isn't fatal: it stops the run
  with a partial result (§5.4).
- **`summarize`:** faulted cases are scored (P1); `incomplete_reason` (P22).
- **`schema_digest`:** backend-aware (P13).
- **Refusals:**
  - **P5:** the removal deletes corpus runs (R-D5). `evalrun.start` still refuses any request
    naming a `corpus_session` with "corpus runs aren't available; real mail goes to Jev only
    through a preset D address (step 4)". A service test posts `backend: jev` with a
    `corpus_session` to the socket.
  - **P2, revising D6:** `--no-redact` with `jev` is allowed only with `--fraud-only` on the
    synthetic set (`_check_options`, `:525-526`, which held the same rule for Gemma). The Claude
    eval gets no `--no-redact`.
- **Every field answered and the same signals block (OD-486):**
  - Jev is asked the effective v3 schema;
  - the mapper fails a case on a missing field;
  - `sent` goes into the state;
  - `--no-signals` is allowed for `jev` (R3);
  - the Gemma-only switches are gone with the removal.
- **Measurement options:** `--jev-state s1|s2`, `--jev-preamble state|instructions` and
  `--jev-concurrency 1`. A non-default value makes the run `for_gate` false.
- **Schema (D9):** a Jev run is refused unless the effective schema is the default.
- **Determinism:** the 10 repeats as today, with their concurrency recorded (P23). Probabilities
  are compared to 1e-6.
- **`gate_passed`:** false for Jev in step 2. The run still reports `unsafe` and
  `fraud_guard_recall`, with faulted cases counted as failures (P1). Step 4 makes D gate-eligible.

**`/ecf-eval --preset D` (D5, P15):**
- `claude_eval.Options.preset` accepts `D`. `start` accepts D and refuses `--classifier-model`.
  The `cli.py` `--preset` help gains D.
- **Source-run query (new code, not `_a_run`):** `eval_runs` rows whose `metrics` have
  `backend == "jev"`, the pin, `prompt_digest`, `set_version`, `schema_digest` (v3),
  `complete == true` and `for_gate` not false, newest first, with the result file present.
  - Faulted source cases keep their failure (an empty answer is scored as a failure, as preset B
    did before the removal). This matches P1.
- Comparison-only (an `eval-...` key) until step 4 adds D to `claude_pins.PAIR`. The
  `schema_digest`, `prompt_digest`, `input_format` and `excerpt_cap` are the source run's.

**`ecf eval compare`:**
- Prints the input differences through the top-level keys (P13).
- Also prints the faulted count and `incomplete_reason` per side.
- Jev vs C is the comparison. An old Gemma result can still be compared as history (removal
  R-D12).

**Rescore (P3):**
- Both rescore paths (`evalrun.py:1392-1410`, `:1532-1589`) keep every per-case field and summary
  key they don't recompute: `fault`, `tries`, `wait_ms`, `queue_ms`, `concurrency`, `confidence`,
  the tokens, `summary.jev`, `gate_key`, `prompt_digest`, `input_format`, `excerpt_cap`,
  **`incomplete_reason` and `format`** (draft 4, C: both paths pass only `complete` through,
  `evalrun.py:1409`, `:1584`).
- The corpus rescore (`rescore`, `:1627`) isn't touched: corpus runs with Jev are refused (P5).
- A test rescores a Jev-shaped fixture, including an incomplete one (`incomplete_reason:
  faulted_safety_card`): every field is kept and the scores are identical.

**Result format version (P16):**
- `ResultFile` gains `format: int`: files without it read as 1; step 2 writes 2.
- An older client refuses with "this result was written by a newer ecf (format 2); run `uv run
  ecf` from the newer checkout or upgrade".
- The CHANGELOG line says step-3 comparisons use the worktree's `ecf`.

**`ecf eval report` (P25):** prints `summary.jev` (latency per concurrency with n, tokens,
faults, pauses).

**Left for step 3:** noise runs, margin, held-out cards, thresholds, the 16 KB schema budget, P11,
concurrency above 1, every D11 and P7 number.

**Costs (P21):**
- About 2.7-3k input tokens per case, so a 300-case run is about 0.8-0.9M tokens, **about
  $0.035-0.04**.
- Twenty step-3 runs: **under $1**.
- If L8 shows state billed once per question (13×), about $0.5 per run.
- Retries, probes and repeats are counted in `input_tokens_billed`.

## 7. Privacy at this step (P2, P5, P28)

- **What reaches TypeSafe in steps 2-3:** only synthetic text.
- **Real mail can't reach TypeSafe:**
  - corpus runs don't exist after the removal, and the service refuses a corpus session (P5);
  - step 2 has no D address.
- **SPEC §12.4:**
  - the Jev line becomes "Built for the eval only (v3.0.0 step 2): what the eval sends", with
    §5.6's list (OD-504);
  - the Slack/Anthropic line (1664) and the derived-signals line (1676) gain "and, in the eval
    only, TypeSafe";
  - the no-payload key list matches §5.5.
- **CLAUDE.md:**
  - its privacy bullet's "decided, not built; what it receives is set when it is built" becomes
    "built for the eval only (synthetic text; OD-504)";
  - CLAUDE.md edits need the operator's OK (rule 3).
- **The full statement at ship (step 7):** retention, sub-processors, agreement, consent.
- **S4's apply-time line** gains TypeSafe in step 4 (D9).
- **A stated limit (P2):** C's injection robustness is measured redacted only. Jev's can be
  measured unredacted on synthetic `--fraud-only` runs.

## 8. Tests

**No real calls:**
- every `jev.Client` in tests uses `httpx.MockTransport`;
- an autouse fixture in `tests/conftest.py` makes `jev`'s default transport raise;
- the live check is the only real call;
- fixtures in `tests/fixtures/jev/` hold invented answers and fake request IDs, no key, no real
  mail, and no stored state.

**Adapter (`tests/test_jev.py`):**
- **Request shape:**
  - the request keys;
  - ordinal level descriptions;
  - choice value → description;
  - state order and token delimiters.
- **Header lines (P10):**
  - newlines and control characters collapse;
  - a forged `Attachment:`/`Subject:` line via a display name is impossible;
  - subject and display-name injection is replaced with redaction on and kept with it off;
  - the date is normalised (eval and service paths give the same string).
- **Excerpt:** cut by characters only; a CJK excerpt keeps all 1,500 characters.
- **Size (P20, P24):**
  - the httpx body is ≤ 16 KB;
  - a state-composition test catches duplication;
  - the estimator refuses at the threshold with no call.
- **Each class in §5.4:**
  - 429 with delta-seconds, an HTTP-date, `retry-after-ms`, a bad value, and none;
  - the short, medium and long tiers (the long tier stops with its message);
  - proactive pacing from fake `x-ratelimit-*` headers;
  - a quota error code stops the run;
  - a 429 on a flagged request is a request error;
  - 529, 500, 503, 408, a timeout, the deadline (a trickling fake reply), a connect error and 302
    (not followed; the Bearer never reaches `Location`) are retried up to the limit;
  - 401 and 403 stop;
  - the key removed stops;
  - a pin mismatch stops;
  - 404 or 422 on the first case stops, and later ones are request errors scored as failures;
  - the first-case message: the pin message only for L4's recorded status and code, the
    "refused the first request" message otherwise (B);
  - unmapped answers fail.
- **Breaker:**
  - it opens after N;
  - a refused try isn't spent;
  - the cool-down doubles to its cap;
  - the probe is the minimal POST;
  - half-open → close after K;
  - the ladder doesn't reset on a single success;
  - a spent waiting budget stops the run with a partial result (`incomplete_reason: budget`).
- **AIMD:** halve, +1, floor, max; `--jev-concurrency 2` is refused in step 2.
- **Interruptible:** stop during a cool-down returns in under 1 s on the fake clock; stop during a
  pause likewise; an in-flight result after stop is discarded.
- **Leaks and environment:**
  - the key never appears in logs, exception text or results;
  - `request_id` is in the log and absent from the result;
  - `n_questions` is logged unredacted;
  - `User-Agent` is exactly `ecf`;
  - `trust_env` and `follow_redirects` are false; `TYPESAFE_*` env vars are ignored.
- **Lock and digests:**
  - the lock is a versioned ID;
  - the gate key changes for a hide-capable field and not for a label-only one, covers `priority`,
    and changes with the mapping;
  - `prompt_digest` changes with each input.
- **Imports:** the import-linter contract "classifier never imports jev" and the extended AST test.

**Key (2a):**
- the per-provider routes;
- an older service's 404 means nothing is sent (a fake old service);
- a test that the old `/v1/models/api-key` path is gone;
- the CLI refuses on an older `/v1/status` version;
- `--provider` is required, the flagless form refused, an unknown provider refused;
- prefixes: `anthropic` without `sk-ant-` is refused, `typesafe` with `sk-ant-` is refused, both
  before any call;
- bad characters are refused;
- validation uses the minimal POST with a 5 s timeout and no double store on a slow reply;
- `anthropic` stores `models-api-key` exactly as today (the existing tests pass with the flag);
- regrant and destroy cover both;
- audit without the key;
- a Security Notice on set, replace and clear for both providers;
- the CLI needs a terminal; the routes are CLI-only;
- the hints use the new form.

**Eval (2d):**
- a 5-case Jev run on the fake runs end to end (classifier only, no model server);
- per-case metrics belong to their case;
- signals and `--no-signals`;
- a request naming a corpus session is refused **through the socket** (P5);
- `--no-redact` is allowed with `--fraud-only` only;
- a non-default schema is refused;
- faulted cases are scored as failures and listed;
- a faulted safety card → incomplete; the 7th faulted case of 304 stops the run and the 6th
  doesn't; a spent budget → a partial result with `incomplete_reason: budget`;
- auth and pin errors fail the run;
- the backend-aware `schema_digest`; top-level `prompt_digest`, `input_format` and `excerpt_cap`
  give `compare` warnings;
- rescore preservation (P3);
- `format: 2` and the old-client message (P16);
- `/ecf-eval --preset D`'s source query (pin, digests, complete) and its faulted-case path;
- `report` prints `summary.jev`.

**Gate:** the full macOS suite with 0 skipped before merge.

## 9. Docs, CHANGELOG, ODs

**SPEC:**
- §7.6: the `--provider` form and per-provider routes.
- New §7.9 "Jev classifier (eval only)".
- §11.6: secret names.
- §12.4 (§7).
- §15.1: the routes, plus the sentence "`/v1/models/api-key` is removed in v3.0.0; a v2.2.x
  client's `ecf models api-key` is refused, and the v3 CLI refuses an older service" (P19).
- §15.2: the audit events.
- §15.4: the Jev row, by class, all values placeholders.
- §16.2: the drivers.
- §16.5: one line on Jev runs; faulted cases scored as failures.
- §21.1 and §21.2: the live check.
- OD table: OD-503 and OD-504. The OD-493 row reads "built for the eval only (step 2,
  OD-503/504)" (P28).

**CHANGELOG** (`## Unreleased`), one line each:
- `ecf models api-key` needs `--provider` (the flagless form and v2.2.x clients are refused);
- the TypeSafe key, and Security Notices for both keys;
- the Jev eval backend;
- preset D in `/ecf-eval`;
- signals sent to decision models in the eval;
- result format 2 (use the worktree's `ecf` for step-3 files);
- the live-check result.

**Other docs:**
- **`docs/admin-guide.md:223`:** the new form.
- **README, SECURITY.md, DISCLAIMER.md:** at ship.
- **CONTRIBUTING:** the suite never calls TypeSafe.
- **ADR 0028 (P28):** amended to "Built for the eval only (step 2)". This answers its Payload
  (OD-504), Key and **Pin** items. Vendor terms and Consent stay open; Lineage is parked.
- **CLAUDE.md privacy bullet (P28):** §7, with the operator's OK.

**ODs:**
- **OD-503:** D2-D12 as revised by P1-P32. The main points: per-provider routes, a fixed provider
  list, prefix checks, Notices for both keys, `jev.py` with the classifier-never-imports-jev
  contract, the serial eval, failure classes, `Retry-After` tiers, the minimal-POST probe, faulted
  cases scored as failures, the corpus local-only rule, `--no-redact` with `--fraud-only` only,
  result format 2, and the 11-field gate key with the mapping digest.
- **OD-504:** D1's payload with P10's header handling.
- **OD-505:** live-check decisions, if any.

**Doc corrections:** §13, applied **after S4 merges** (P8).

## 10. Sequencing and branches (D10, P8)

**S4's real overlap** (`../ecf-s4`: 49 files, +1885/-318):
- Shared files: `systemone.py` (S4 edits `ask`; step 2 edits `questions()` only),
  `claude_eval.py` (S4: `Claim`/`_claim_records`/`_invalid`; 2d: `Options`, `start`, the D query),
  `ecf/eval/results.py` (S4: `ClaimRecord`; 2d: `ResultFile.format`, `CaseResult` fields),
  `classifier.py` (S4 only), `claude_pins.py` (S4 only), `tests/conftest.py` (S4:
  `extensions_off`; step 2: the transport guard).
- Possibly shared: `evalrun.py` (S4's plan cites `:622-628`, `:1148-1169`; 2d edits elsewhere)
  and `rules.py` (S4 moves `HIDE_ACTIONS`).
- Also the two planning docs: S4 edits PLANNING §4.5 and §4.9 and SCH's header and §2, next to K1b,
  K6b and K7.
- The hunks are disjoint (cost-design H1), so the merge is mechanical.
- 2a's files are not in S4's diff: `secretstore/`, `regrant.py`, `api.py`'s model routes,
  `cli_models.py`, `model_watch.py`, `ecf/doctor.py`.

**Branches:**
- **`jev-step2`** in `../ecf-jev`: 2a, 2b (the live check, from this branch's `accept`) and 2c.
  - Choice for the operator: merge `jev-step2` before S4 (its hunks are disjoint and it has its
    own macOS gate), or hold it for one merge after S4.
- **2d** on `jev-step2d`, branched from `main` after the removal merges (draft 5; was: written
  now, merged after S4).
- The §13 doc edits go in after S4 merges.
- The two-agent diff review runs on the whole step-2 diff before step 3.

**`accept`:** the one-sitting sequence in §4.

## 11. Review sizing

- **Phase R: done** (four agents, P1-P32, all decided 2026-10-14). This draft applies them.
- **The live-check script:** I read it for the key-handling rules before the operator's
  go-ahead. The operator runs it.
- **Diff review before step 3's first gate run:** two read-only Fable agents (safety and
  correctness; performance) on the merged step-2 diff. Findings are fixed or decided before any
  step-3 run.

## 12. Decided (operator, 2026-10-14)

### D1-D12, as revised by Phase R

- **D1:** Jev gets what Claude gets (OD-504), with P10's header handling. §5.2, §5.6.
- **D2 (amended; P4, P19):** `ecf models api-key set|clear --provider anthropic|typesafe`.
  - `--provider` is required from a fixed list; the flagless form is refused.
  - Per-provider routes; the old route is removed in v3; the CLI checks the service version first.
  - Prefix checks. No `api_version` bump.
  - §3.
- **D3 (P12):** no step-up to set the key; step-up on adding a D address (step 4); a Security
  Notice on every set, change and clear, **for both keys**. §3.
- **D4 (P8, P9):**
  - `jev.py` imports `systemone`'s builder and mapper, and nothing moves out of `systemone.py`.
  - The only `systemone` edit is `questions()`.
  - "Classifier never imports jev" is an import-linter contract; step 4's dispatch goes in a
    new module (draft 5: `pipeline` is removed).
  - §5.1.
- **D5 (P15):** preset D in `/ecf-eval` in 2d, comparison-only, with its own source-run query.
  §6.
- **D6 (revised by P2):** Jev may run `--no-redact` on synthetic cards only, with `--fraud-only`;
  refused on a corpus. The Claude eval gets no `--no-redact`. §6, §7.
- **D7:** the request ID goes in the service log only. §5.5.
- **D8:** `User-Agent: ecf`. §5.1.
- **D9:** user fields on D decided in step 4; Jev evals use `schema: default`. §6.
- **D10 (P8):** 2a-2c now; 2d written on its branch now, merged after S4. §10.
- **D11 (amended; revised by P1, P6, P7, P18):**
  - handling by class;
  - `Retry-After` tiers, run-wide, with a waiting budget;
  - proactive pacing if TypeSafe sends rate-limit headers; quota detection;
  - the minimal-POST probe;
  - stops at once on auth, pin, first-case 404 or 422, long waits and the key removed;
  - faulted cases **scored as failures**;
  - early stop past `floor(0.02 n)` (the 7th of 304);
  - a spent waiting budget stops with a partial, incomplete result (draft 4);
  - AIMD and the breaker as objects, with a serial eval;
  - the breaker becomes the Jev-down signal in step 4.
  - §5.4.
- **D12 (P11):** the key set right after the go-ahead, then the live check in the same sitting,
  run by the operator in Terminal. §3, §4.

### P1-P32

| P | Decision | Where |
|---|---|---|
| P1 | faulted case scored as a failure; `faulted` diagnostic; faulted safety card → incomplete | §5.4, §6 |
| P2 | Jev `--no-redact` with `--fraud-only`, synthetic only; no Claude `--no-redact` | §6, §7 |
| P3 | rescore keeps fields; test | §6 |
| P4 | per-provider routes; old route removed; version check; prefixes | §3 |
| P5 | corpus local only, enforced in the service; socket test | §6, §8 |
| P6 | serial; `--jev-concurrency` 1; AIMD in `jev.py` | §5.4 |
| P7 | `Retry-After` tiers, pacing, quota stop, minimal-POST probe, stops at once, early stop | §5.3, §5.4 |
| P8 | real S4 overlap; `questions()` only; K edits after S4; 2d branch | §5.1, §10, §13 |
| P9 | dispatch in a new module (draft 5; was `pipeline` or a new module); import-linter contract; nothing moves | §5.1 |
| P10 | header lines; injection check on headers; excerpt in characters; date normalised | §5.2 |
| P11 | live check in Terminal, 39 calls, one client, L8 billing, L9 fresh + fixed, L10 isolation, one sitting | §4 |
| P12 | key read in the route, re-read, missing → stop, 5 s validation, Notice for both keys | §3, §5.1 |
| P13 | backend-aware `schema_digest`; top-level keys; mapping digest | §5.7 |
| P14 | `classifier_ms` = the successful try; `wait_ms`, `queue_ms`; n per concurrency | §5.5 |
| P15 | real `evalrun` touches; D query; (draft 5) no Ollama or `EXCLUSIVE` left to skip | §6 |
| P16 | result `format` | §6 |
| P17 | `httpx.Timeout`, deadline, keepalive 60 s | §5.1 |
| P18 | interruptible waits; in-flight discarded | §5.4 |
| P19 | refuse old clients; SPEC §15.1 sentence; no bump | §3, §9 |
| P20 | 16 KB bound on the httpx body | §5.2 |
| P21 | costs stated; `input_tokens_billed` | §4, §5.5, §6 |
| P22 | `incomplete_reason`; integer rule | §5.4 |
| P23 | repeat concurrency recorded | §5.5 |
| P24 | estimator named and calibrated | §5.2 |
| P25 | `report` prints Jev | §6 |
| P26 | injection set on both formats before choosing | §5.2 |
| P27 | `n_questions`; avoid `state`/`value`/`message` | §5.5 |
| P28 | stale privacy text | §7, §9, §13 |
| P29 | K list fixes | §13 |
| P30 | ODs reserved (done) | header |
| P31 | effective rule set; step 4 TODO | §5.7 |
| P32 | `jev.lock` in the upgrade check (step 4); cost from `usage` | §5.3, §5.5 |

### Choices the decisions don't settle (mine, for the operator to confirm)

Accepted as written (operator, 2026-10-14). Two are partial deviations from a decision: 2 (P4
said the live check would note a prefix) and the P18 note in §5.4 (an in-flight call isn't
cancelled; its result is discarded and its deadline bounds it).

1. **The version check is unreliable between bumps.** Same-version worktrees can't be told apart;
   the per-provider 404 is the working guard then (§3).
2. **No TypeSafe key prefix check unless the docs document one.** The live check can't note a
   prefix without inspecting the key.
3. **The key is re-read before each case, not only at probes.** Otherwise a clear during a healthy
   run wouldn't stop it.
4. **`--jev-concurrency` accepts only 1 in step 2.**
5. **The breaker ladder resets only after K successes,** and a refused try isn't spent.
6. **A faulted safety card marks the run incomplete but doesn't stop it;** determinism repeats
   still run.
7. **No battery pause for a Jev run** (moot after the removal: no battery rules remain).
8. **Date format:** RFC 3339 UTC; unparseable dates become `unknown`.
9. **`s2` keeps the token delimiters inside `text`.**
10. **The size estimator is tokens ≤ bytes;** the refusal limits are 30k and 62k.
11. **L9 is 5 fresh + 5 fixed tokens** (keeps 39 calls; the measurement review asked for 10 + 5).
12. **Quota detection relies on an error-code field the live check may never see;** until then
    it is inactive.
13. **The Notice for the Anthropic key on first set and on clear too**, not only on change.
14. **C runs write `input_format: claude-v1`.**
15. **2d branch name `jev-step2d`.** Whether `jev-step2` merges before or after S4 is open.

## 13. Doc corrections step 2 makes (P8: after S4 merges; P28, P29)

Find texts are quoted from `main`'s working tree. Line numbers are approximate, and S4 moves some.
**Draft 5:** the removal's R0 rewrites PLANNING (draft 5) and SCH first, so each K edit is
re-checked against those texts when 2d is written; K1 and K4b already read as below.

| # | File, place | Find (verbatim) | Replace with | Source |
|---|---|---|---|---|
| K1 | PLANNING §4.3 Adapter (~243) | "`systemone.py` gains a hosted transport (plain HTTPS to api.typesafe.ai, the existing httpx pattern in `ollama.py:353-359`), beside the Ollama one." | "A new module `ecf_server/jev.py` holds the hosted transport (plain HTTPS to api.typesafe.ai, the remote httpx pattern in `model_watch.py:82-85`), the pin, the error classes and the circuit breaker that becomes the Jev-down signal; it imports `systemone`'s question builder and answer mapper, and nothing moves out of `systemone.py`. `classifier` never imports `jev` (an import-linter contract); step 4 dispatches in a new module (D4, P9, OD-503; ADR 0031)." | D4, P9 |
| K1b | PLANNING §4.9 (~558) | "**Jev** gets the same questions through `systemone.py` (step 2)" | "**Jev** gets the same questions through `jev.py`, which uses `systemone`'s question builder (step 2)" | D4 |
| K2 | PLANNING §4.3 Pin (~258) | "Jev joins the weekly model watch." | "`GET /v1/models` lists aliases only and versioned IDs are accepted unlisted (/models, read 2026-10-14), so the weekly watch can't see a pinned version retire; step 4/5 records the aliases' release dates, and a retired pin shows up as call errors (the eval stops on the first case)." | /models; P7 |
| K2b | PLANNING §4.3 Switch back (~281) | "while down, the service probes Jev every 5 minutes with `GET /v1/models` (no" ... "The first success clears **down**" | "Jev-down is the adapter's circuit breaker open for `jev_down_after_minutes`; its probe, a minimal POST with no email content (the key-check call), runs at each cool-down end (30 s doubling to 5 min) and is the switch-back check; a long `Retry-After` is down at once (OD-503, D11, P7). *Service behaviour lands in step 4.*" | D11, P7, P29 |
| K3 | PLANNING §4.3 Logging (~294) | "`model_calls` gains a backend column, the Jev model ID and TypeSafe's `request_id`;" | "`model_calls` gains a backend column and the Jev model ID (step 4); TypeSafe's request ID (the `x-typesafe-request-id` response header, /sdk/python/api/types/responses, read 2026-10-14) goes in the service log only (D7);" | SDK; D7 |
| K3b | SCH §4 Transport (~185) | "`request_id` and `usage` only." | "the `x-typesafe-request-id` header (service log only) and `usage` only." | D7 |
| K4 | SCH §4 Errors (~167-170) | "**Errors:** 401, 422 (validation), 429, 529 (overloaded); no 400 listed, and over-limit behaviour isn't documented (/api). The adapter maps 422 to a failed attempt and 429/529 to a provider fault (item waits); ecf's current over-context handling looks for HTTP 400 (`systemone.py:163`). Over-size behaviour is found by a live test." | "**Errors:** /api lists 401, 422, 429 and 529; TypeSafe's SDK also maps 400, 403, 404 and other 5xx (/sdk/python/api/exceptions, read 2026-10-14). Over-limit behaviour isn't documented and is found by the step-2 live check. Handling by class (OD-503): 429 rate limit (`Retry-After` tiers); 529, 5xx, timeouts and connection errors provider faults; 401 and 403 stop; 400, 404, 413 and 422 request errors (not retried, scored as failures; on the first case, a pin error that stops the run)." | SDK; D11, P1, P7, P29 |
| K4b | PLANNING §4.3 Failures (~264-267) | "422 (validation) and an" / "answer that doesn't map are failed attempts, never a default. ecf's current over-context" / "handling looks for HTTP 400 (`systemone.py:163`); the hosted mapping follows the live check." | "400, 404, 413 and 422 are request errors (not retried) and an answer that doesn't map is a failed attempt; both are scored as failures, never a default (OD-503, P1). The Ollama decision-model path and its HTTP 400 handling are removed (ADR 0031)." | P1, P29, ADR 0031 |
| K5 | PLANNING §4.3 Failures (~268) | "Score ties and choices outside the options resolve toward more risk (as `systemone.py` does" / "today)." | "Score ties resolve toward more risk; a choice outside the options is a failed attempt (`systemone.py`, `map_answers`)." | code |
| K6 | PLANNING §4.5 Gate keys (~349) | "where the questions digest covers the 7 fixed safety items, the 3 fixed workflow fields and" | "where the questions digest covers the 11 fixed fields (7 safety items, 3 workflow fields and `priority`, fixed for good by S4 D12), a mapping digest (thresholds, tie rule), and" | S4 D12; P13 |
| K6b | SCH §7 Jev gate key (~293) | "field read by a rule that can hide mail (`HIDE_ACTIONS`, `rules.py:62`), the Jev pin and" | "field read by a rule that can hide mail (`HIDE_ACTIONS` in `rules.py`), `priority` (fixed, S4 D12), the mapping digest, the Jev pin and" | S4 D12; P29 (no line number) |
| K7 | SCH §2 (~68) | "Jev never sees field or" ... "value names, only descriptions (TypeSafe agent skill, `SKILL.md`, read 2026-10-13)." | "Jev doesn't see field names (question keys aren't sent, /api) but does see value names with their descriptions (\"The option names and their descriptions are both sent to the model\", /primitives/choice, read 2026-10-14)." | /api; /primitives/choice |
| K8 | PLANNING §4.3 Key (~259) | "(an `ecf models api-key`" ... "step and an `ecf init` step, validated by one call)" | "(`ecf models api-key set --provider typesafe` through its own route, and an `ecf init` step in step 4, validated by one minimal call with no email content; a Security Notice on every set, change or clear; OD-503)" | D2, D3, P4 |
| K8b | SCH §12 (~426, ~430) | "`ecf init` (or `ecf models api-key`) asks for it" / "(`ecf models api-key` and the `ecf init` step are PLANNING §4.3's names, not built.)" | "`ecf init` (or `ecf models api-key set --provider typesafe`) asks for it" / "(`ecf models api-key --provider typesafe` is built in step 2; the `ecf init` step in step 4.)" | D2 |
| K9 | PLANNING §4.3 Payload (~249) | "the same prepared, redacted, capped excerpt the classifiers get today" | "what the Claude classifier gets: the sender's display name and address, the subject, the date (normalised), attachment metadata, each on one line and checked for injection with redaction on, and the prepared, redacted excerpt (1,500 characters) (OD-504, D1, P10)" | D1, P10 |
| K10 | PLANNING §4.4 Prompt digest (~304) | "**Prompt digest:** SHA-256 of the preamble, the question JSON, the excerpt cap and the model ID;" | "**Prompt digest:** SHA-256 of the preamble, the full question JSON, the excerpt and header caps, the state format, the signals wording, the mapping digest and the pinned model ID; written at the top level of the summary with `input_format` and `excerpt_cap`;" | P13, P29 |
| K11 | SPEC §12.4 Jev line (~1677) | "**Decided, not built: TypeSafe Jev**" ... | "**Built for the eval only (v3.0.0 step 2, OD-503/504): TypeSafe Jev**", plus §5.6's list; real mail only through a preset D address (step 4) | P28 |
| K11b | SPEC §12.4 (~1664, ~1676) | "to Anthropic during `/ecf-review` and `/ecf-eval`." / derived signals "... add no destination" | add "and, in the eval only, to TypeSafe (synthetic text)" to each | P28 |
| K12 | CLAUDE.md, Hard constraints, Privacy | "decided, not built; what it receives is set when it is built" | "built for the eval only, synthetic text (OD-503/504); what it receives: SPEC §12.4" (operator OK needed) | P28 |
| K13 | ADR 0028, "Open before building" | the Payload, Pin and Key items | "Answered (step 2): Payload OD-504; Pin `jev.lock`, checked per reply, in the gate key; Key `jev-api-key` via `ecf models api-key --provider typesafe`." Vendor terms and Consent stay open; Lineage is parked | P28 |
| K14 | SPEC OD table, OD-493 row | "decided, not built;" | "built for the eval only (step 2, OD-503/504);" | P28 |

After the live check, each undocumented item (L4, L5b, L7, L9, L10) gets one line in SCH §4 and
PLANNING §4.3 pointing to SPEC §21.2.

## 14. Not verified

- **Answered by the live check, or never:**
  - whether a 429 carries `Retry-After`;
  - whether TypeSafe sends `x-ratelimit-*`;
  - the form of a quota error code;
  - over-size and retired-pin replies;
  - whether `GET /v1/models` needs the key;
  - latency, determinism and isolation;
  - billing of state.
- Whether error bodies quote the request (L6).
- That `Client.close()` or an abandoned in-flight httpx call is safe across threads (not relied
  on, §5.4).
- The no-Slack path for the D3 Notice on `accept`.
- The 16 KB bound and the byte/token figures: from the performance review's local render; L8
  calibrates them.
- S4 line numbers (`config.py` 444, 1127; `rules.py` `HIDE_ACTIONS`): from the cost-design review
  of an uncommitted tree.
- Parked: Jev's base model; ClickHouse and Cloudflare.

## 15. Requirements coverage

| Requirement (source) | Where |
|---|---|
| §4.3 (a) fixed versions, (b) `model` reports the version, (c) per-question instructions | §2.1; L2, L3; §5.2 |
| §4.3 endpoint, auth, fields, limits | §2.1; L1-L11 |
| §4.3 precondition: a live response confirms (b), finds the over-size reply, differences reported | §4 |
| §4.3 adapter: plain HTTPS, not the SDK | §5.1; K1 |
| §4.3 Jev replaces the classify step only (P4 of PLANNING) | §1; §5.1 import contract; step 4 |
| §4.3 payload | §5.2, §5.6 (OD-504); D9; K9 |
| §4.3 ordinal levels carry descriptions | §5.2 |
| §4.3 a test bounds the request size | §5.2, §8 (16 KB, httpx body) |
| §4.3 exact payload in SPEC §12.4 | §5.6, §7, K11 |
| §4.3 pin in a lock file, checked every reply, mismatch refused | §5.3 |
| §4.3 a disappearing pin fails closed; `ecf doctor` | §5.3 (eval stops on the first case); doctor in step 4 |
| §4.3 Jev joins the model watch | §5.3, K2: not covered in step 2 (step 4/5, with its limit) |
| §4.3 a pin change restarts gates | §5.7 (pin in the key); restart in step 4 |
| §4.3 key, written by the service, validated by one call; regrant, destroy, backups, env | §3 |
| §4.3 key in `ecf init` | not covered: step 4 |
| §4.3 AWS Secrets Manager in M1 | not covered: M1 note |
| §4.3 failures | §5.4; K4, K4b |
| §4.3 ties and choices | §5.4; K5 |
| §4.3 timeout and retry from measured p99, §15.4 row | §5.1, §5.4 placeholders; §5.5 latency definition; §9 |
| §4.3 Jev down: per-call timeout | §5.1 (deadline) |
| §4.3 Jev down: switch, queue, card, probe, notices, audit, OD | §5.4 breaker and probe built; the rest in step 4; K2b |
| §4.3 confidence recorded; middle band; hiding bar; never route down | §5.5; steps 3-4 |
| §4.3 logging, scrubber | §5.5; K3 |
| §4.4 through `ecf eval run`, every field, same signals | §6 |
| §4.4 prompt digest; compare checks inputs | §5.7, §6; K10 |
| §4.4 noise, comparisons, margin, held-out | not covered: step 3 |
| §4.4 safety gates; injection set; preamble placement | §6 (faulted cases as failures, P1; `--no-redact` with `--fraud-only`, P2); §5.2 |
| §4.4 latency and cost from `usage` | §5.5, §6 |
| §4.4 measurement list | §6 options; the rest in step 3 |
| §4.4 run without signals | §6 |
| §4.4 result format version | §6 (P16) |
| §4.5 preset D | §6 (eval); step 4 |
| §4.5 gate key | §5.7; K6, K6b |
| §4.8 privacy at ship | §7, §9; K11-K14 |
| §4.10 Jev capture point | §5.5 |
| §4.11 Jev-down Slack; classifier ID on cards | not covered: steps 4-5 |
| Step 2: live check first; differences reported first | §4, §10 |
| Step 2: behind the eval only | §1, §6 (P5) |
| Step 2: two-agent diff review before step 3 | §11 |
| Tests with no real calls | §8 |
| Docs, CHANGELOG, ODs | §9, §13 |
| D1-D12, P1-P32 | §12 |
