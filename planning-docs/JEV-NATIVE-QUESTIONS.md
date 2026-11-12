# Jev with Jev-designed questions

Status: draft 1, 2026-11-12. Operator asked (2026-11-12) for a Jev re-test whose questions follow
TypeSafe's own guidance, kept apart from the Claude-style schema, and compared with it. Branch
`jev-native` (worktree `../ecf-jev-native`, off `acc7c40`). Eval only: no service path changes,
preset D stays held back (OD-516). New OD number: OD-NEW, assigned at commit.

## 1. Why

Jev was shelved (OD-516) and failed OD-538's stage 1 again with 1.14 at 4,000 characters (SPEC
§21.2: 268 and 264 of 394 against Opus 340 and 333; −16.5 and −18.3). In both tests Jev was asked
the questions written for Claude: `systemone.questions` (`src/ecf_server/systemone.py:57`) turns
each schema field into one question with its description as the instruction and plain-string
criteria; only `fraud_risk` (asked as a choice, OD-512) and `requires_action` (three questions,
OD-514) were adapted.

TypeSafe's docs (read 2026-11-12, copies in `state-archive/jev-native/tsdocs-2026-11-12/`) ask for
the opposite shape:

| Guidance (page) | What ecf sends today |
|---|---|
| "Decompose the questions… probably the most important concept in this guide" (/concepts/how-to-build-with-system-one §4) | `category` is one 19-option choice whose instruction holds a precedence rule; `priority` one score; `fraud_risk` one choice over four levels whose definitions are lists of conditions |
| Literal reading: "state the exact condition… Put boundary cases in the criteria"; indirection costs accuracy (/model-jaggedness #1, #4) | Descriptions written for an LLM: "genuine or not", "No when…", "(…computed, not judged)", precedence orders |
| Contrastive criteria: `what`, `not_for`, `examples` (/primitives/choice "Structured instructions and criteria"; /primitives/advanced) | One-line strings |
| Companion Noul instead of `other` (/patterns/companion-noul; /primitives/choice tip) | `other` competes inside the choice |
| Noul: one condition, a yes is the high value, `true`/`false` criteria for subtle boundaries (/primitives/noul "Writing a Noul question") | Booleans with no criteria; several conditions in one description |
| State: content in `state`, judgments in questions; an object with named fields (/concepts/state) | One string: the preamble (instructions), the signals block, the email between token lines |
| Adversarial content: "be explicit in the criteria" (/model-jaggedness #6) | The preamble only |

### 1.1 Where 1.14 lost (aggregate, from `8c5573c0` against Opus `4cf8a43e`)

Rule errors 79 against 32. Most are a route to `fraud_guard` that the label doesn't want:
`fraud_weak` 15, `account_security` 7, `regulatory` 7, `requires_reply` 6, `appointment` and
`travel_finance` 4 each. On the labelled fields Jev rated `fraud_risk` `none` or `low` as `medium`
38 times (Opus: 7 `low`→`medium`), which `fraud_guard` reads. The `medium` level's text ("Any
request to move money, data, credentials or access on this email's say-so… A request to call a
number or follow a link to secure an account counts too") read literally covers genuine sign-in
alerts and agency notices. Other losses: `requires_action` false→true 80 (Opus 38; marketing to
`otherwise` 6), `requires_reply` false→true 32 (Opus 22), `priority` high→urgent 76 (Opus 27;
`priority` isn't in `correct`). These are error types, used to choose where wording needs
`not_for` clauses; no card was tuned on (§6.3 states the guard).

## 2. Advice recorded (operator asked)

- **Claude:** not changed. C's misses on the matched test are few (rule 5, `requires_action` 6,
  `priority` 9); an LLM follows a precedence rule; more, smaller fields cost more output tokens;
  any schema edit moves C's gate key (SPEC §9.3, D3). Contrastive wording might help Claude too
  (unverified for ecf); that would be its own later plan.
- **Expectation for Jev** (speculation): the gain comes from `fraud_risk` (the literal `medium`
  reading, §1.1) and `category`; Jev alone perhaps +3 to +10 points; OD-538 needs about +12 or
  more against Opus. Our own evidence tempers it: OD-514's split lowered Jev-alone accuracy (219
  to 213).

## 3. Design

### 3.1 Two question styles, kept apart

- **`claude`** (default): today's request, byte for byte: `jev.questions` over the effective
  schema, state format `s1`, `jev.as_asked`. Its digests and `SHIPPED_*` test pins don't move.
- **`native`**: `src/ecf_server/data/jev_questions_v3.yaml`, hand-written, with composers that map
  the answers back to every schema field. `schema_v3.yaml` is untouched. Native answers the
  default schema only (an install's extension fields have no native questions: a run with an
  extended schema and `--jev-questions native` is refused).

### 3.2 The question file

- `questions`: the request's questions, each `noul`, `choice` or `score` with structured
  `instructions` and `criteria` (TypeSafe accepts string, object, array, /primitives/advanced).
  One directive is expanded by the loader and never sent as written: `kinds: from <choice>` puts
  the named choice's options (name and `what`) in the instruction object, for the companion Noul.
- `fields`: one composer per schema field:
  - `noul`: the question's probability; yes at p >= 0.5 (`systemone.NOUL_TRUE`).
  - `any`: the largest probability of its questions (OD-514's fold).
  - `choice`: the question's choice; its probabilities are the field's.
  - `cascade`: steps in order, each `[value, noul question]`; the first step at p >= 0.5 gives
    its value; else `else`: a value, or `{choice, companion, none_value}`: the choice's answer,
    or `none_value` when the companion Noul is under 0.3 (/primitives/noul: "0.3 for no").
- **Probabilities reported per field** (what `jev_mapping.derive` reads for bands and bars):
  `noul` and `any` as today (`{"true": p}`); `choice` as Jev's; `cascade` the probability at the
  deciding stage: the deciding Noul's p for a step value; for the `else` choice, the choice's own
  probabilities; for `none_value`, 1 − the companion's p; for an `else` value, 1 − the largest
  step p. The band file (`jev_mapping.json`) is unchanged; native's digest includes it.
- **Precedence as code:** `category`'s steps follow the schema's own order (a sign-in or security
  alert; money: a bill or payee change, a payment made, money coming in, an account we hold; then
  the topic; `other` last), so the order Jev was asked to apply in text is applied by code.
- **fraud_risk** as a cascade of three questions, one per level above `none`: a known pattern
  (`high`), a request trusted only on the email's say-so (`medium`, with `not_for` covering a
  genuine sign-in alert, a usual supplier's bill and an agency's own filing process), any doubt
  (`low`).

### 3.3 State formats

- `s1`: today's (`jev.state`).
- `s3`: a JSON object: `notice` (the untrusted-data text, adapted to name the fields instead of
  delimiter lines), `signals` (the true signals' wording, a list, possibly empty), `email`
  (`from`, `subject`, `date`, `attachments` (name, type, size), `body`: the same excerpt). The
  body is a JSON string value, so the email can't close its own field; the delimiter token isn't
  needed and isn't sent. Caps, redaction and the size estimate are as `s1`.
- Arms isolate one change each: native with `s1`, native with `s3`.

### 3.4 Code

- `src/ecf_server/jev_native.py` (no transport; imports `ecf.schema`, `systemone`): `load()`
  (cached; checks the file against the compiled schema: every field one composer, every enum and
  level value reachable from a step, choice option or `else`, every referenced question present,
  no unused question, question ids unique, choice options all described, score levels 2-10),
  `questions()`, `compose(answers) -> systemone.Answer` (validated as strictly as today: a
  missing or unmappable part fails the case), `digest()`.
- `jev.py`: a `Style` (`claude` | `native`) and a state format (`s1` | `s3`) threaded through
  `request_body`, `Client.classify`, `_answer`, `prompt_digest`, `questions_digest`, `gate_key`
  and `state_format_id`; defaults reproduce today's values exactly.
- `evaljev.py`, `evalrun.Options`, `ecf/cli.py`: `--jev-questions claude|native` and
  `--jev-state s1|s3` on `ecf eval run --classifier-backend jev`; either off its default marks
  the run measuring (`options.measuring`, never for the gate); `summary.jev.options` records
  both. `remap` and `jev_sweep` work on native results (they read recorded probabilities).
- `scripts/jev_questions.py`: `lint` (warnings citing their doc rule: a Noul with "and"/"or"
  joining conditions or a negated yes; a score with numeric-only levels or outside 2-10; a choice
  over 255 options or with an undescribed option; numbers or date comparison asked of the model;
  `true` and `false` criteria that overlap word for word), `show [--field F]` (Claude-style
  beside native, per field), `orders` (the reversed-option native file for the option-order
  check, /model-jaggedness #8), `request --card ID` (the request body for one card, offline).
- `preset D`, the service drain and `jev_dispatch` keep the Claude-style path; nothing in the
  service reads the native file.

## 4. Tests

- The `claude` style's request body, prompt digest, questions digest, gate key and state format
  id equal today's (the pinned `SHIPPED_*` values unchanged).
- `load` refuses: a field with no composer or two, an unreachable value, a missing or unused
  question, an extension field, an unknown composer, a bad `kinds` directive.
- `compose`: each composer on fixtures; a cascade's first yes wins; the `else` choice; the
  companion at 0.3; a missing part, a non-numeric probability, an unknown choice key each fail
  the case; probabilities reported per §3.2.
- `category` precedence: a security alert that is also a bill composes to `account_security`; a
  bill that is also a receipt to `invoice` (the schema's order).
- `s3`: valid JSON; the body is a string; redaction applied; the estimate refuses as `s1` does.
- Native and `s3` digests differ from the defaults; a native run is `measuring`.
- The lint's checks each on a seeded bad question; the shipped file lint-clean or each warning
  decided in this plan.

## 5. Runs (pre-registered)

- **Conditions:** one commit, served by `accept` from the worktree; frozen set at its current
  version (public + private half); `jev-1.14.0`; the 4,000 window; redaction on; signals on;
  rules only (no actor); concurrency 1.
- **Stage A** (Jev alone, two runs each, interleaved A B C A B C): J-claude (today's questions),
  J-native-s1, J-native-s3. One key check before the first run.
- **Primary figure:** `correct` (rule + safety + the fixed items). **Pass for Stage B:** a native
  arm whose both runs beat both J-claude runs by Newcombe lower bound > 0, with 0 unsafe and
  fraud-guard recall 100% in both runs and 0 injection cards followed.
- **Report:** per arm `correct`, unsafe, recall, injections; per field Claude-style against
  native (`category`, `rule`, `priority`, `requires_action` recall and precision,
  `requires_reply`, `fraud_risk` under and over, `sender_type`, the five items); rule confusions;
  determinism (10 repeats); cost; latency.
- **Generalisation check:** the best native arm and J-claude once each on `tests/eval/heldout3`
  (30 blind cards, labels confirmed 2026-10-15, never used for wording).
- **Stage B** (only on a Stage A pass; needs the operator for `/ecf-eval`): OD-538 as recorded:
  D with the native arm's classifications and the Opus actor against fresh Opus C runs, two per
  arm, −5 Newcombe over all four pairs; then the second set.
- **Cost:** native asks 26 questions (today 15). Input tokens are billed (/models); whether the
  state is billed once or per question isn't stated ("Jev ingests the `state` once", /models,
  is about context; unverified for billing). The first native request's `usage` gives the
  figure; at 26× the state, a run is still about US$0.30, so all Stage A runs stay under US$3.

## 6. Risks and guards

1. **Overfitting to the frozen set:** wording was chosen from aggregate error types (§1.1), not
   cards; heldout3 is the check; a native gain that vanishes on heldout3 is reported as such.
2. **Injection:** `s3` drops the delimiter lines; the injection cards and `notice` decide;
   any unsafe case fails the arm.
3. **Cascade bias:** a high-recall early step can swallow later values (e.g. `cat_security` on
   every phishing lure that mentions a password); the per-field report shows it.
4. **Band fitted on Claude-style:** the bands and bars came from 1.13 Claude-style runs; native
   reports per-field probabilities per §3.2 so they apply, and `jev_sweep` over native runs
   shows whether they still fit (reported, not changed in this plan).
5. **Question count and limits:** 26 questions per request; the per-request limit (64k) counts
   the state once plus every question, the per-question limit (32k) the state plus one question
   (/models "Context length"); `jev.estimate` checks both before any call.

## 7. Files

New: `src/ecf_server/data/jev_questions_v3.yaml`, `src/ecf_server/jev_native.py`,
`scripts/jev_questions.py`, `tests/test_jev_native.py`, this plan. Changed: `jev.py`,
`evaljev.py`, `evalrun.py` (Options), `ecf/cli.py`, `tests/test_jev*.py`. SPEC §7.9 and §21.2,
CHANGELOG, OD row at the end. Unchanged: `schema_v3.yaml`, Claude's agents, rules, policy,
`jev_mapping.json`, `jev_dispatch.py`.
