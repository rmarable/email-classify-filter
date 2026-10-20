# `ecf eval generate` and `ecf eval grade`: rate ecf with other models

Draft 15 (2026-10-20): draft 14 plus the operator's decision that the DEV run may use `claude -p` on the operator's own Claude plan sign-in (OD-542; Facts: Anthropic terms). Draft 14 (2026-10-20): draft 13 plus the approved realism rubric and pass mark, `planning-docs/RUBRIC.md` (OD-541): four questions (three 1-4 scales, one yes/no), seven pass rules, a near-miss rule (20 fresh seeds for both models), refusals at most 2 overall and 1 per group, at most 3 check drops; the intended group shown as context. Draft 13 (2026-10-20): draft 12 plus how the realism rubric is made: three independent drafts (Codex, a Mistral model, Claude), the operator combining parts of each (operator decision 2026-10-20; `planning-docs/RUBRIC-GENERATION.md` draft 2). Draft 12 (2026-10-20): draft 11 plus the `gpt-6.1-sol` canary on Plus, which passed (Facts). Draft 11 (2026-10-20): draft 10 plus step 3 built (the `codex` adapter, the Mistral grader through the service, the model list and family checks) and the operator's three decisions on it (OD-540), and the Codex code-mode fact (Facts). Draft 10 (2026-10-20): draft 9 plus the `claude` adapter (step 2, built; the DEV run waits for the rubric). Draft 9 (2026-10-20): draft 8 plus two corrections to the Vibe facts and where Mistral API keys come from (a peer session's re-read of Vibe's source, (rv)). Draft 8 (2026-10-20): draft 7 plus the Codex sign-in and the two Codex checks (Facts), step 1 built, and ADR 0034 and OD-534 to OD-539 recorded in SPEC (build approved by the operator, 2026-10-20). Draft 7 added OpenAI's data-control pages and the `claude -p` check. Draft 6 added the Mistral API facts. Draft 5 added the step 0 findings and the mixed route (Codex CLI generates, Mistral's API grades). Draft 4 added the usage-policy reading and the pilot conditions. Draft 3 added the mix, fields and recipient decisions. Draft 2 came after Phase R (six Fable reviewers, P1-P31; full findings in
`state-archive/eval-generate/review-findings.md`, gitignored). Operator decisions of 2026-10-19
are marked (op). ADR 0034 and OD-534 to OD-539 were recorded in SPEC when the operator approved
the plan. Code references were checked against `e925b3c`.

## Context

The synthetic eval set (`tests/eval/synthetic/`, 304 cards, set `43f650c9d1839407`) was written by
Claude (`author: claude`), and ecf's classifier and actor in preset C are Claude (Opus 5.5 in every
gate role, OD-520). So every figure ecf has comes from Claude reading Claude's mail. The operator
wants a second set that no Claude model wrote, and a way, for themselves and for users, to rate ecf
with other models: one model writes test mail, ecf handles it as usual, and a model from a third
family judges it (op). This grows the generator half of SPEC §1.2 Later's "optimization of the
testing data corpus" (v3.0.0 RAW, R9) into a shipped feature.

**Not a bias measurement** (op, P9): a generated set differs from the frozen one in category mix,
facts, difficulty and label coverage, and the classifier was tuned on the frozen set, so comparing
the two can't isolate an authorship effect (an unpaired 300-against-304 difference is about ±5.9
points; a 5-point effect would need about 950 cards per set). The generated set is a second set,
read on its own.

## Decisions

1. **No Claude content** (op). Claude writes the machinery: this plan, the code, the formats, the
   deterministic checks and the measurement. Claude never writes, picks, edits, grades or labels
   email content, seeds or wording, and gives the generator no examples or style guidance. The
   frozen set is not used as input: no content, categories, counts or
   `src/ecf/eval/case_templates.py` (op). Fixed configuration (a fictitious recipient organisation,
   its `.example` domain, the mailbox role, the list of facts to choose from) is not content.
2. **Categories from published lists; the operator (or user) groups them and sets the shares**
   (op; P10). Sources, fetched 2026-10-19:
   - Scams: FBI IC3 *2025 Internet Crime Report*, "2025 Crime Types, By Complaint Count", with its
     Appendix B definitions (ic3.gov/AnnualReport/Reports/2025_IC3Report.pdf). 26 types:
     Phishing/Spoofing 191,561; Extortion 89,129; Investment 72,984; Personal Data Breach 67,456;
     Non-Payment/Non-Delivery 56,478; Tech/Customer Support 47,794; Government Impersonation
     32,424; Identity Theft 31,675; Business Email Compromise 24,768; Employment 24,688;
     Confidence/Romance 23,159; Harassment/Stalking 21,557; Other 20,031; Credit Card/Check Fraud
     18,774; Real Estate 12,368; Advanced Fee 7,762; Lottery/Sweepstakes/Inheritance 5,623;
     Threats of Violence 4,826; Data Breach 3,963; Ransomware 3,611; IPR/Copyright and Counterfeit
     2,386; Overpayment 2,194; SIM Swap 971; Malware 893; Botnet 715; Charity 662.
   - Delivery: MITRE ATT&CK T1566 Phishing, v2.8, modified 2026-09-18: .001 Spearphishing
     Attachment, .002 Spearphishing Link, .004 Spearphishing Voice (.003 is not email).
   - Ordinary mail: Gmail's inbox categories (support.google.com/mail/answer/3094499): Primary,
     Social, Promotions, Updates, Forums.
   **Which IC3 types are kept** (op): a type whose Appendix B definition describes a message that
   deceives or pressures the recipient into acting (pay, send data, click, call, reply) is kept;
   one that describes an incident, an outcome, something not aimed at the recipient, or nothing,
   is left out. Out: Personal Data Breach ("a leak/spill of personal data"), Identity Theft
   ("wrongfully obtains and uses personally identifiable information"), Data Breach ("the use of a
   computer intrusion"), SIM Swap ("against mobile service providers"), Botnet ("computers
   controlled and updated remotely"), Other ("not currently designated as an IC3 crime type"),
   Harassment/Stalking ("repeated words, conduct"), Threats of Violence ("an expression of an
   intention to inflict pain"). Extortion ("unlawful extraction of money") is kept.
   **Groups** (op; method A): the top 9 kept types by complaint count are a group each, the rest
   one group: Phishing/Spoofing; Extortion; Investment; Non-Payment/Non-Delivery; Tech/Customer
   Support; Government Impersonation; Business Email Compromise; Employment; Confidence/Romance;
   Other IC3 types (Credit Card/Check Fraud, Real Estate, Advanced Fee, Lottery/Sweepstakes/
   Inheritance, Ransomware, IPR/Copyright and Counterfeit, Overpayment, Malware, Charity). The rule
   and the quoted definitions ship in the mix file.
   **Shares** (op; option C): at 300, 150 scam (15 per group, equal) and 150 ordinary (30 per
   Gmail category). Groups are report-only one by one (under 20, `REPORT_ONLY_BELOW`,
   `src/ecf/eval/results.py:709`); the scam total (150) and ordinary total (150) are read. More
   emails for one group can be generated later if it looks weak. Complaint-count weights were not
   used: they follow mostly consumer complaints and would give BEC 6-10 emails.
   **Delivery** (op; option B): MITRE .001 attachment, .002 link, .004 callback, plus reply-only
   (no link, attachment or number; added by the operator, not from MITRE), 4/4/4/3 per scam group
   with the 3 rotating, 38/38/37/37 in all. Attachments are plain text or ecf's invoice PDF, links
   use reserved domains, callbacks 555-01xx numbers.
3. **An `ecf` command for users** (op), under `ecf eval`; one release (op, P19), a minor numbered
   when it ships (op; OD-533, which also covers M6).
4. **PROD and DEV** (op). PROD: generator, grader and classifier from three families. DEV: Claude
   for all three, to prove the pipeline before paying for other models.
5. **Defaults** (op): PROD generator OpenAI `gpt-6.1-sol` through the Codex CLI on the operator's
   ChatGPT Plus plan (`gpt-6-luna` only if it passes the pilot); PROD grader Mistral Large 4
   (`mistral-large-4-0`, the dated id, pinned because the model is a preview) through **Mistral's API** (op, 2026-10-19: the mixed route; not Vibe, which
   can't drop its tools, Facts); DEV `claude` with full model ids (Haiku 5.5 generates, Sonnet 5.5
   grades).
5a. **Plan or API, per provider** (op, 2026-10-19): for each provider and model added, compare its
   subscription plan with its API before choosing: cost at the expected number of sets a month
   (and whether the operator already pays for the plan), whether the route can run with no tools,
   what extra data it sends, how credentials are stored, the data terms, and limits. Recorded with
   the provider's ADR. For now: OpenAI by plan (Plus, already used for Codex on the repo; $0 extra,
   it shares the coding quota), Mistral by API (about $1 a set against $14.99 a month for Pro).
6. **Size** (op): `--count N`, default 300 in PROD and 30 in DEV; the target is N kept.
7. **Labels** (op; P7, P8): only labels the operator (or user) gives blind count (OD-486): through
   `ecf eval label --review all` or the card review page, full email, no answer shown. The
   generator's proposals are kept apart (`proposals.jsonl`), never written as `expected`. The
   operator labels all 300 of the first PROD set, every card (no bulk confirmation, P28).
8. **Expected rule and safety** (op; P2): after the labels, ecf's rules engine (the `ecf rules
   test` path, `src/ecf_server/ruletest.py`) computes each card's expected rule and safety flags
   from its confirmed labels and facts; the operator confirms them. A set without them reports
   field agreement only and says "no fraud-guard or injection cases".
9. **Never the gate's evidence** (op; P1): see Design, Gate guard.
10. **Destinations** (op; P5, P6): ADR 0034 amends the one-destination rule for a closed list:
    OpenAI (through the Codex CLI) and Mistral AI (through its API), Anthropic already allowed. A new provider
    needs its own ADR. SPEC §12.4 gets a paragraph per provider in the TypeSafe form. Both usage
    policies were read on 2026-10-19 (Facts); the operator accepted the PROD pilot on these
    conditions (op): (a) a refused request is never retried, reworded or replaced, so a group can
    come up short and the report gives refusals per group (OpenAI's policy bars "circumventing our
    safeguards"); (b) every generator prompt states its purpose in fixed words the operator
    approves: the emails test an email-security filter, stay on this machine and are never sent;
    (c) the pilot stays at 20 seeds and the operator accepts the risk to the ChatGPT account;
    (d) Extortion is in the pilot like the other groups.
11. **Tools without tools** (op; P3, P20): no adapter is used until it is shown to run with no
    tool access and no MCP servers (Design, Adapters). The Mistral API call has no tools by design;
    the Codex adapter must pass the canary with every tool switched off.
12. **The Jev re-check is later** (op; P26): D must pass at OD-516's −5 margin on both the frozen set
    and a confirmed generated set, two runs per arm compared as in step 3, with facts in the
    generated cards.
13. **Classifier not hard-coded** (op): the family check reads the classifier's (and actor's)
    family from the result. Other classifier models and other actor models are Later items (SPEC
    §1.2): eval-only first, then production only after the matched test and a new preset. The
    long-term aim (op): ecf runs with the user's choice of classifier and actor, tested with their
    choice of generator and grader.
14. **The 5,000-email stress test** moves to its own SPEC §1.2 Later item (op, P29), out of this
    plan.

Proposed records (numbers assigned at approval):
- **ADR 0034** Generator and grader providers: the closed list, what each receives, the tool-less
  rule, the separate logins.
- **OD-534** Generated eval sets: commands, PROD/DEV, no Claude content, published categories and
  grouping, `--count`, blind labels, rules-engine expectations (decisions 1-8).
- **OD-535** Privacy (§12.4) and the first-use notice (ADR 0034).
- **OD-536** Credentials and isolation: Codex's sign-in in its own `CODEX_HOME` under ecf's data
  folder, stored in the macOS Keychain (`cli_auth_credentials_store="keyring"`), never the
  plaintext `auth.json`; the Mistral API key in the macOS Keychain, written only by the service
  (`ecf eval key set mistral`, step-up), as ecf's other credentials (AGENTS.md "Credentials");
  refuse on key variables in the environment; cost guards for API providers (Design, Cost guards).
  Proposed for step 3 (a peer session, 2026-10-20): name the key command `ecf models api-key set
  --provider mistral`, as ecf's other keys, instead of `ecf eval key set mistral`.
- **OD-537** Models: the shipped list, the model filter and the three-family rule in code.
- **OD-538** Jev re-check rule (decision 12); OD-486 amended to allow a confirmed generated set.
- **OD-539** The stress test as its own Later item; the SPEC §1.2 corpus entry reworded.

## Design

### Flow and commands (P13, P22)

generate → grade → label (blind) → run → report.

```sh
ecf eval generate --root <new folder> [--mix <file>] [--count N] [--generator <p>[:<model>]] [--dev] [--parallel N] [--yes]
ecf eval grade    --root <set> [--grader <p>[:<model>]] [--yes]
ecf eval label    --root <set> --review all        # existing blind path
ecf eval expect   --root <set>                     # rules engine fills rule and safety; you confirm
ecf eval run      --root <set> --claude ...        # existing; never for the gate on a generated set
ecf eval report   --root <set> <result>...         # grader and labels against each result
ecf eval generate --write-mix <file>               # copy the shipped mix to edit
```

`--root` everywhere, as every set command uses (`src/ecf/cli.py:876`). `grade` labels the set
blind, so one grading serves any number of ecf runs. Grades are written as an ordinary result file
(`pair: grader:<model id>`), so `ecf eval compare`, `field_lines` and `ecf eval label --results`
work on them unchanged; `report` adds only the per-group breakdown and the grader's measured
accuracy (P22). `report` matches results to the set by the cases' sha256s, not `set_version`, and
earlier results are rescored with `ecf eval rescore --synthetic` (P13).

### The mix (P10, P16)

Shipped as `src/ecf/data/eval_mix.yaml` with the three lists (name, published definition, source,
date) and the default grouping left empty. The user's file holds ids and shares only:
`ordinary_share`, `scam: {group: share}`, `ordinary: {category: share}`, `delivery: {method:
share}`, and `groups: {group: [IC3 type ids]}`. Any other text is refused; keys are slugs
(`[a-z0-9-]{1,40}`). Shares become counts by largest-remainder rounding within each list; the
manifest records both. Prompts take published text from the shipped file only.

### Generation (P11, P14, P18)

1. **Seeds**: one call per group, asking for 1.5 times its count, so replacements never need a new
   seed call. A seed is structured facts: sender role, what's asked, amount or none, urgency,
   delivery, ordinary or scam, mailbox profile (`org` or `freemail`), and facts chosen from fixed
   lists (`auth_result` pass/none/fail, `sender_seen_before` yes/no, bulk yes/no).
2. **Emails**: one call per 5-10 seeds of a group (K set in the plan after the pilot). The
   generator returns message fields only: sender display name and address, reply-to, subject,
   date, plain-text body, optional hidden text, attachments (plain text only, or a request for
   ecf's deterministic invoice PDF), and label hints for all 13 schema v3 fields. ecf sets everything else from the seed and
   the recipient configuration: headers, authentication results, profile, `expected.facts`, title,
   threat and review notes, id (`<group>-<nnn>`), `author: generated`, `generator: <model id>`.
3. **Prompt contents**: the published names and definitions, the delivery method's name, the
   recipient configuration (op; option A: the eval harness's own profiles,
   `src/ecf_server/ruletest.py:58-59`, `src/ecf/eval/cards.py:29`: the organisation "Acme" at
   `acme.example`, the accounts-payable mailbox `ap@acme.example`, one named colleague Dana Chief
   `dana-chief@acme.example`, and the personal mailbox `pat-lee@freemail.example`; no industry,
   size or location; these names were set when the harness was built and are configuration, not
   content; per-set profiles are a possible later item), the fixed safety rules (AGENTS.md "When you edit these paths":
   reserved domains, regulators and brands by role in text only, phones 555-01xx, no real names,
   published or checksum-failing bank numbers, no URLs outside reserved names, no copied real
   phishing text), and the output schema. No style guidance or examples. No text aimed at automated
   readers until the adapters pass the canary (P3). The operator approves each stage's prompt text
   before the first PROD run; digests go in the manifest.

### Checks (P12, P14, P15, P17; nothing is edited)

- Each email becomes a `Card` (`src/ecf/eval/cards.py`, widened: `author` gains `generated`, a
  `generator` field) and is validated and scanned with `scan_card` (`src/ecf/eval/builder.py:104`)
  as it's made, so the final `build_all` can't fail.
- The hygiene scan gains crypto wallet shapes (BTC, ETH, XMR) and a shipped list of major brands and
  agencies refused in sender names.
- HTML is not accepted; any `<script`, `<form`, `on…=` or `javascript:` in any field is refused.
- A hygiene failure is retried once with the finding kinds quoted, then dropped; a schema misfit is
  dropped; a refusal is recorded and dropped, never retried (decision 10a).
- Near-duplicates across the whole set (same opening line, or high word-shingle overlap) are
  dropped (P30).
- Drops other than refusals are replaced from the seed pool until the group's count is met or 3
  times its count is reached; a refused seed is never replaced or retried (decision 10a); the report gives drops and refusals per group, and marks a group with over a third
  dropped as "selected" (M6).
- `generate` refuses a folder that isn't empty. The manifest holds the sha256 of every card,
  `labels.jsonl` and the mix; `grade`, `expect` and `report` refuse a set edited since. The manifest
  guards against mistakes, not against a determined user.

### The generated set

The synthetic set's layout (`cases/*.md` in the one-line labels form, `eml/`, `labels.jsonl` with
no `expected` until labelled) plus `manifest.json` (mode, generator and grader ids and versions,
prompt digests, mix, seeds, drops by reason and group, usage per call, times, ecf version) and
`work/` (append-only `seeds.jsonl`, `calls.jsonl`; P23). Generated sets are untrusted data: read in
Claude Code only through the screen text or read-only subagents; `report` prints no email or card
text.

### Gate guard (P1)

A set with a generated manifest is never for-gate: `claude_eval.start` and `evalrun.load` detect it,
force `for_gate=False` and skip `evalrun.remember_root` (`src/ecf_server/claude_eval.py:496`,
`src/ecf_server/evalrun.py:678-691`). The gate's synthetic check refuses any root other than the
frozen set (`src/ecf_server/gate.py:245-261`). `ecf eval run --claude` accepts `--not-for-gate`
(`src/ecf/cli.py:1289-1293`). SPEC §9.3 says the synthetic check is the frozen set only.

### Grading (P8)

One call per email: the raw email and all 13 schema v3 fields to answer (op; option A:
`category`, `priority`, `requires_action`, `requires_reply`, `payment_related`,
`deadline_mentioned`, `sender_type`, `fraud_risk`, `bank_detail_change`, `regulator_claim`,
`phishing`, `claims_internal`, `private_individual`; the rules read `category` and `sender_type`
as well as the fixed fields, so all 13 are needed to compute the expected rule; OD-486), with the
shipped descriptions from `src/ecf/data/schema_v3.yaml`, returned as JSON. The operator labels the
same 13. Blind: no ecf answer and no label. The grader's input is the raw
email, not the classifier's excerpt and signals. Before grading, the blind labels are frozen (their
digest recorded); the grader's accuracy is computed once against that version and kept, and later
relabels are logged with the digest they changed and reported separately.

### Report

For each ecf result: agreement with confirmed labels and with the grader, per field and per group,
Wilson intervals, groups under 20 report-only; the grader's measured accuracy first; the generator's
agreement with the operator (its label quality); rule and safety figures only when decision 8 is
done; the noise reference (two C runs per set, or the S3b pair's figure stated). A DEV report shows
plumbing counts only (drops, misfits, resumes, times), no accuracy (P25).

### Adapters (P3, P4, P20, P21, P23)

One table in `src/ecf/eval/tools.py` (argv template or API endpoint, schema mode, output parser,
minimum version, sign-in or key check). For a CLI, each run's start checks the tool's version and
that every flag ecf passes appears in `--help`, and records the argv digest and version.

| Provider | Call | Sign-in / key |
|---|---|---|
| `claude` (DEV) | `claude -p --output-format json --json-schema <s> --model <full id> --system-prompt <adapter text> --tools "" --strict-mcp-config --safe-mode --no-session-persistence`, prompt on stdin; success only when `subtype` is `success` and `structured_output` is present; whether structured output works with `--tools ""` is checked by one run (operator's go-ahead) | ecf's `CLAUDE_CONFIG_DIR` and login (`ecf claude --login`, `src/ecf/claude_wrapper.py:389`); transcripts purged after |
| `codex` | `codex exec --sandbox read-only --ephemeral --skip-git-repo-check --ignore-user-config --ignore-rules --json --output-schema <s> -o <file> -m gpt-6.1-sol`, prompt on stdin, from an empty folder outside any git repo, plus: `--disable shell_tool --disable apps --disable plugins --disable remote_plugin --disable multi_agent --disable image_generation --disable browser_use --disable computer_use --disable goals --disable sleep_tool --disable tool_suggest --disable skill_search --disable view_image` and `-c web_search="disabled" -c tools.experimental_request_user_input.enabled=false -c include_environment_context=false -c include_environment_context_time=false -c analytics.enabled=false -c otel.metrics_exporter="none" -c feedback.enabled=false -c check_for_update_on_startup=false -c history.persistence="none" -c memories.generate_memories=false -c memories.use_memories=false -c model_reasoning_effort=<set after the pilot>` (all on the command line, since `--ignore-user-config` skips `config.toml`); `-o` deleted before each call. Built (step 3): also `--disable unified_exec` and `-c model_catalog_json=<file>`, a one-model catalogue in direct tool mode that the preflight writes from `codex debug models --bundled` (Facts: code mode); `model_reasoning_effort` not passed until the pilot sets it | a separate `CODEX_HOME` under ecf's data folder, with no `AGENTS.md`, its own one-time `codex login`, and `cli_auth_credentials_store="keyring"` |
| `mistral` | HTTPS POST to Mistral's chat completions endpoint **from the service** (operator decision 2026-10-20, OD-540: the key never reaches the client), with a JSON-schema response format (`json_schema` with `name`, `schema`, `strict: true`), no tools field, `model: mistral-large-4-0`; one email per call. The client sends `POST /v1/eval/mistral` with the set, the case id and the model; the service checks the set (`gen.verify`, PROD, listed model), reads the email and builds the grading prompt itself | API key in the secret store, written by the service (`ecf models api-key set --provider mistral`, OD-540; no step-up, as the other keys) |

Every adapter:
- runs (for a CLI) under an allow-listed environment built like `claude_wrapper.base_env`
  (`ENV_ALLOW`, telemetry off), and **refuses**, naming the variable, if `ANTHROPIC_API_KEY`,
  `OPENAI_API_KEY`, `CODEX_API_KEY`, `MISTRAL_API_KEY` or a provider base-URL variable is set (P4);
- must pass a **tool-less canary** before use (CLIs): a decoy file in the work folder and a prompt
  asking for its contents; with `--json`, Codex output may hold only `agent_message`, `reasoning`
  and `error` items (an allow-list); any other item type, or the decoy's contents in the answer,
  fails closed and drops the email (P3). `apply_patch` has no switch in Codex 0.163.0 but can't
  write under the read-only sandbox, and reading needs the shell tool, which is off;
- uses `subprocess.run(..., shell=False, capture_output=True, timeout=..., start_new_session=True)`
  with the process group killed on timeout (CLIs), or a timeout per HTTPS request (API); parses
  one JSON object, size-capped, strictly, and validates it against the schema itself (neither
  provider's schema mode is trusted alone);
- stops the run after 3 consecutive failed calls of any kind, showing the last error line; Codex's
  usage limit shows only as the text "You’ve hit your usage limit" (curly apostrophe) and exit 1, a
  stop, not a detected state; an API 429 is retried with backoff up to 5 minutes, then a stop;
  `--resume` continues from `work/`;
- logs exit codes, byte counts, times, refusal categories and digests only (P27; `stdout`,
  `stderr`, `refusal`, `seed`, `email` added to the redacted keys, `src/ecf/log.py`).

### Cost guards (API providers)

- On the provider account: prepaid credit, and monthly spending limits on the organisation and
  the workspace (Mistral: "API access can be suspended until the next month begins" at the limit),
  so a run can never spend more than the operator set; training turned off in the Admin Console's
  Privacy panel (where that panel sits now is unverified; check at account setup). API keys come
  from Mistral AI Studio (console.mistral.ai), not from Vibe ((rv), unverified by Claude).
- `--max-cost` (default US$15 per run): ecf adds each call's cost from the returned token counts and
  a shipped price table (with its date and source), and stops the run when the next call could pass
  the cap.
- An estimate before each run (calls and cost), confirmed with `--yes`; the 3-failure stop and the
  no-retry-on-refusal rule (decision 10a) keep a fault from looping.

### Model switches (P24)

`--generator`/`--grader` take `<provider>[:<model>]`, checked against
`src/ecf/data/eval_models.json` (exact id, provider, family by lab not host, filter status, "checked
on <date> against <page>"). Unknown models and excluded labs (PRC-, Meta- and X-affiliated; Qwen and
Qwen-based) are refused, including those a provider hosts (Mistral lists Z.ai's GLM 5.3). In PROD,
`generate` and `grade` refuse `claude` and a generator and grader of one family; `report` maps the
result's models to families (`claude-*` Anthropic, a Jev pin TypeSafe) and refuses an unknown one,
marking "not a measurement" if the classifier shares a family with either. A test keeps the Claude
entries equal to `ecf_server/data/models.lock`.

### Privacy (P5, P6, ADR 0034)

- What goes: to the generator, the shipped published text, the recipient configuration, the safety
  rules and the schema; to the grader, the generated email and the shipped field definitions.
  Never facts, signals, results, labels, the service's data, or user text from the mix.
- §12.4 gains a paragraph per provider (payload, tool, the account it runs under, the vendor's
  published retention and training terms with page and date, or "not stated"); README "Your
  privacy", DISCLAIMER and SECURITY.md change with it; user docs (operator and admin guides, with
  the disclaimer callout) explain it in plain words and tell users to turn off training in their
  ChatGPT and Mistral settings.
- `generate` and `grade` print a first-use notice (what goes, to whom, under whose account, "never
  put real mail in a set") and need `--yes` or a prompt.
- §12.2 stated limit: the tools' sign-ins live in ecf's config folders; any process running as you
  can use them.

### Where it runs

The client (`ecf`), except the gate guard, `expect`'s rules-engine call and the Mistral grader's
calls, which go through the service API (`ecf` never imports `ecf_server`).

## Estimates (to be replaced by the pilot's figures; P18, P28)

| Item | Estimate |
|---|---|
| Codex calls for 300 kept | about 30 seed calls + 30-60 email calls at K 5-10 (more with drops) |
| Plus windows (Sol, 15-160 per 5 h) | 1 at the top of the range, up to about 6 at the bottom; unverified that one call is one message |
| Grader (Mistral API) | about 300 calls at about 3k tokens in, 0.3k out: about $1.6 at Large 4's regular price ($1.36/$4.18 per 1M), about $0.8 at its sale price ($0.68/$2.09) |
| Generator (Codex on Plus) | $0 extra on the operator's existing Plus plan; shares the coding quota |
| DEV (30) | about 10-15 `claude -p` calls on ecf's Claude login |
| `ecf eval run --claude` on 300 | about 10-16 min dispatch plus facts prep (SPEC §20 figures for 304 cards); US$13-15 API-equivalent |
| Operator labelling | about 5-10 h for 300, every card blind |

## Steps

0. **Facts**: done 2026-10-19 (Facts) except: whether paid Mistral API use is trained on by
   default (opt out at account setup either way) and the exact `json_schema` fields; whether
   `codex exec` with every tool off still answers under `--output-schema`, and the model ids
   available to this Plus account (`codex debug models`) (each a one-call check with the operator's
   go-ahead, after the operator signs in to ecf's separate `CODEX_HOME`). Done 2026-10-20: the
   OpenAI help-centre pages (Facts) and the `claude -p` check (Facts); the Codex sign-in and
   `codex debug models` (Facts). The canary with `gpt-6.1-sol`, refused on the free plan,
   passed after the move to Plus (Facts).
1. **Shared pipeline**: mix, seeds, emails, checks, card changes, set layout, manifest, grade (as a
   result file), expect, report, resume, gate guard, logging. Tests with a fake executable on PATH
   (as `tests/test_claude_wrapper.py`'s `fake_claude`) and recorded JSON, including a limit, a
   refusal, a tool event and the canary; no network. **Built 2026-10-20** (branch
   `eval-generate-build`): `ecf.eval.mix` (and `ecf/data/eval_mix.yaml`, Appendix B quoted from
   the report's pp. 59-60), `tools` (runner, strict schema check, canary, breaker), `generate`,
   `gencheck`, `grade`, `expect` (service route `POST /v1/eval/expect`), `report`, `providers`
   (none built: the `claude` adapter is step 2, `codex` and `mistral` step 3), `cli_evalgen`; the
   gate guard in `evalrun`, `claude_eval` and `gate`; `ecf eval run --claude --not-for-gate`.
   Choices made in the build, for the operator to check: blind labels need `ecf eval label
   --review all --add` (the cards start unlabelled); the set-integrity check covers each built
   message and its facts, not labels, rule or safety, which change as you label; `expect` sets
   `must_escalate` when the rule escalates and `must_not_hide` on a fraud rule or a payment or
   fraud label; the grader's accuracy is recorded by the first report (`work/grader-accuracy.jsonl`,
   with the labels' digest), not at grading, since labels come after grading in the flow;
   ordinary seeds carry no
   delivery method; near-duplicates are 5-word shingles at 60% containment.
2. **The `claude` adapter and a DEV run** of 30, after the operator writes the realism rubric and
   the pilot pass mark (P25). Plumbing only. **Adapter built 2026-10-20** (`ecf.eval.claude_tool`):
   the flags of the `claude -p` check, the schema passed inline (`--json-schema` takes the schema,
   not a file), ecf's install's `CLAUDE_CONFIG_DIR` with telemetry off, Claude Code 2.1.303 or
   later; a permission denial, an answer on another model, an error or no `structured_output`
   fails the call, a `refusal` stop is a refusal. `generate` and `grade` check the tool's version
   and flags and run the canary from a throwaway folder before touching the set. The config
   folder isn't purged after a run (an `ecf claude` session may share it);
   `--no-session-persistence` keeps the calls' transcripts out. Left: the rubric and pass mark
   (operator decision 2026-10-20: Codex, a Mistral model and Claude each draft one independently
   from the brief `planning-docs/RUBRIC-GENERATION.md`; the operator picks parts of each, blind to
   which model wrote which, and approves the combined `planning-docs/RUBRIC.md`; approved
   2026-10-20, OD-541; the blind rating page is built from its YAML block next). The DEV run may
   use the operator's own plan sign-in (OD-542; Facts: Anthropic terms); before ecf ships, the
   `claude` adapter is marked operator-only or uses an API key for other users. Then the DEV run of 30 (real-service test: `uv run ecf --install accept eval
   generate --dev --root <folder> --yes`, then `grade`, `report`).
3. **Codex adapter and Mistral API client** (with `ecf eval key set mistral` and the cost guards),
   the model list, the family check; Codex passes the canary. **Built 2026-10-20** (OD-540):
   `ecf.eval.codex_tool` (direct-mode catalogue, the item allow-list, "model not on this ChatGPT
   plan" told apart from the usage limit), `ecf.eval.models` and `ecf/data/eval_models.json`
   (Claude entries equal `models.lock`, a test checks; `gpt-5.6-terra` listed for the free plan,
   not a default; excluded labs refused by name), `ecf.eval.mistral_api` (the answer checked, cost
   from the returned token counts and the shipped price, `--max-cost` stops before the next call
   could pass it, 429s paced up to 5 minutes, a refused key stops), `ecf_server.mistral` and the
   routes `POST /v1/models/keys/mistral` and `POST /v1/eval/mistral`; `grade` checks the PROD
   generator and grader families and prints an estimate; `report` names a result whose
   classifier shares a family "NOT A MEASUREMENT" and refuses an unknown classifier. The key
   command is `ecf models api-key set --provider mistral` (OD-540), not `ecf eval key`. The Codex
   canary through the adapter passed on `gpt-5.6-terra` (2026-10-20, operator's go-ahead; one
   call on ecf's accept sign-in, Codex 0.163.0, "answered with no tool access"). The operator's
   ChatGPT account moved to Plus the same day, and the `gpt-6.1-sol` canary passed (Facts).
   Mistral's first real call waits for the operator's key and account setup.
4. **PROD pilot** (real-service test, operator's go-ahead each time): the same 20 seeds through Sol
   and Luna; the operator rates realism blind; drops, refusals per group (ceiling in the pass mark),
   repeated openings, phrase overlap; time, tokens and plan messages per call (meter read before and
   after); grader on those 40; operator seconds per card.
5. **The first PROD set**: 300 kept; grade; blind labels; expect; two C runs; report.
6. **SPEC and docs**: ADR 0034, the ODs, §1.2 (Later items incl. the stress test), §9.3, §12.2,
   §12.4, §16, §20, README, DISCLAIMER, SECURITY.md, user guides, CHANGELOG.
7. **Release** (minor), only when the Codex adapter passes the canary (decision 11).
8. **Jev re-check** (later, when the operator names it), under decision 12.

## Open questions for the operator

Decided 2026-10-19 (Decisions 2, Generation, Grading): the IC3 rule and groups, the shares, the
delivery split, the 13 fields, the recipient profiles, the release number (OD-533), the usage
policies and the pilot conditions (decision 10). None open.

## Facts

Fetched 2026-10-19 unless stated. "Unverified" items are checked in step 0.

- **Codex** (learn.chatgpt.com/docs/developer-commands, /non-interactive-mode; `codex exec --help`
  0.163.0): read-only sandbox by default (blocks writes and network, not reads); `--output-schema`,
  `-o`, `-m`, `--json`, `--ephemeral`, `--skip-git-repo-check`, `--ignore-user-config` ("auth still
  uses CODEX_HOME"), `--ignore-rules`, `-c key=value`; must run in a Git repository unless skipped;
  "reuses saved CLI authentication by default"; `codex login status` exists. No flag removes the
  shell tool. Unverified: schema strictness; default model; retention; whether `--ephemeral` skips
  memories.
- **Codex plans** (learn.chatgpt.com/docs/pricing): Plus $20/month; local messages per 5 hours
  GPT-6 Astra 5-45, GPT-6.1 Sol 15-160, GPT-6 Sol 15-150, GPT-6 Luna 350-3,000; "weekly limits may
  also apply"; Pro from $100/month, "no five-hour limit".
- **OpenAI API prices** (developers.openai.com/api/docs/pricing), per 1M tokens in/cached/out:
  Astra $10/$1/$50; 6.1 Sol $2/$0.10/$10; Luna $0.10/$0.01/$0.50.
- **OpenAI usage policies** (openai.com/policies/usage-policies, changelog 2025-10-29 "a
  universal set of policies across OpenAI products and services"; the page returns 403 to fetch
  tools, so the operator pasted its text, read 2026-10-19): "we don't allow our services to be used
  to manipulate or deceive people … including any use for … deceit, fraud, scams, spam, or
  impersonation"; "you cannot use our services for: threats, intimidation, harassment …";
  "circumventing our safeguards"; "unsolicited safety testing"; "breaking or circumventing our
  rules and safeguards may mean you lose access". No exception for security testing or simulation.
  Reading (not legal advice): the bans are on using the service to deceive people; synthetic test
  mail that is never sent isn't that, but enforcement may judge the output, so refusals and account
  risk remain (decision 10).
- **Anthropic terms for `claude -p`** (fetched 2026-10-20): the Consumer Terms (anthropic.com/
  legal/consumer-terms, effective 2025-10-08), §3 item 7, bar access "through automated or
  non-human means, whether through a bot, script, or otherwise", "Except when you are accessing
  our Services via an Anthropic API Key or where we otherwise explicitly permit it". The Claude
  Code headless page (code.claude.com/docs/en/headless) documents `claude -p` for scripts and says
  bare mode "doesn't use your subscription login", so plain `claude -p` does; it states no plan
  restriction, and the Pro/Max help article (support.claude.com 11145838) says nothing on
  scripting. The Agent SDK overview (code.claude.com/docs/en/agent-sdk/overview): "Unless
  previously approved, Anthropic does not allow third party developers to offer claude.ai login or
  rate limits for their products, including agents built on the Claude Agent SDK." Reading
  (operator decision, OD-542): the operator's own DEV runs on their plan are explicitly permitted
  use; offering ecf's `claude` adapter on other users' plan sign-ins would be the third-party case.
- **Mistral usage policy** (legal.mistral.ai/terms/usage-policy, effective 2026-06-11, fetched
  2026-10-19): fraud and scams prohibited ("Phishing attempts", "Impersonation" listed); no
  generating content "intended to deceive others"; no rule on spam or simulated content; no testing
  exception. Mistral only grades here, which these rules don't cover; Mistral as a generator would
  be a grey area.
- **Vibe, not used** (source at mistral-vibe tag v3.0.0, commit a0c78d0a; docs.mistral.ai Vibe pages;
  2026-10-19): the core always offers `read_file`, `write_file`, `edit`, `bash`, `run_typescript` and
  `search_tool_functions` to the model; `--enabled-tools` only makes calls to them be refused; no
  `--model` flag, but an exact id can be pinned through a `[[models]]` entry or `VIBE_ACTIVE_MODEL`,
  and an unknown pin falls back to Medium 3.5 with a warning (corrected 2026-10-20, (rv): a peer
  session's re-read at the same tag, `vibe/core/config/models.py:500-503`,
  `vibe/core/config/layers/environment.py:15-17`, `vibe_schema.py:1178-1196`); no schema mode;
  the browser sign-in yields a Mistral API key stored in the Keychain (`.env` fallback); training on
  by default for Vibe ("users are not opted out by default"; help.mistral.ai/en/articles/455207,
  updated 2026-09-03, fetched 2026-10-20, (rv); Vibe and API training settings are separate); server-side experiments can change
  the system prompt and default model unless pinned. Hence the API route.
- **Codex** (step 0 detail; source at `rust-v0.163.0`; learn.chatgpt.com config pages; 2026-10-19):
  tools switch off with `--disable <feature>` and `-c` keys except `apply_patch` (no switch); the
  read-only sandbox lets reads anywhere and blocks writes and command network; `--json` items are
  `agent_message`, `reasoning`, `command_execution`, `file_change`, `mcp_tool_call`,
  `collab_tool_call`, `web_search`, `todo_list`, `error`; `$CODEX_HOME/AGENTS.md` is read despite
  `--ignore-user-config`; environment context (cwd, shell, date, timezone) and analytics are on by
  default; `cli_auth_credentials_store` is `file` by default (plaintext `auth.json`), `keyring`
  available; `--output-schema` is sent as strict JSON schema, not checked by the CLI; `-o` written
  only on a completed turn; model ids `gpt-6.1-sol` (min 0.153.0) and `gpt-6-luna` (min 0.155.0);
  usage limit is the text "You’ve hit your usage limit" and exit 1.
- **Codex code mode** (2026-10-20; Codex CLI 0.163.0, source at `rust-v0.163.0`): the catalogue
  (`codex debug models`; the one bundled in the binary, `--bundled`, has the same entries for
  `gpt-6.1-sol`, `gpt-6-luna` and `gpt-5.6-terra`) gives every model but `gpt-5.5` `tool_mode: code_mode_only`, which wins over feature
  switches (`core/src/tools/mod.rs:63-73`); in that mode Codex adds `list_mcp_resources`,
  `list_mcp_resource_templates` and `read_mcp_resource` with no MCP server configured
  (`core/src/tools/spec_plan.rs:1289-1304`) and a JavaScript `exec` tool that calls them; the
  event's `"server":"codex"` is a default label. A catalogue given with `-c model_catalog_json`
  replaces the server's (`model-provider/src/provider.rs:393`). One canary (operator's go-ahead)
  on the free plan, `gpt-5.6-terra`, that model's entry with `tool_mode: direct`, plus
  `--disable unified_exec`: answered `{"word":"ok","decoy":"none"}`, events only
  `agent_message`, 6,098 input tokens (about 15.6k in code mode, where it had called
  `list_mcp_resources`).
- **Mistral structured output** (docs.mistral.ai/studio/conversations/structured-output/custom,
  fetched 2026-10-20): `POST https://api.mistral.ai/v1/chat/completions`, `Authorization: Bearer
  <key>`, `response_format: {type: "json_schema", json_schema: {schema, name, strict: true}}`; the
  answer is the JSON text in `choices[0].message.content`, with `finish_reason` and `usage`
  (`prompt_tokens`, `completion_tokens`); Mistral prepends "Your output should be an instance of a
  JSON object following this schema" to the system prompt. Whether `strict` is enforced: not
  stated (ecf validates anyway).
- **Mistral API** (2026-10-19): Large 4 (docs.mistral.ai/models/mistral-large-4-0.md): ids
  `mistral-large-4`, `mistral-large-4-0`; "Public Preview", released 2026-10-06; 1M context;
  structured outputs and function calling supported; $0.68/$2.09 per 1M tokens on sale, $1.36/$4.18
  regular. `POST /v1/chat/completions` takes `response_format` of type `text`, `json_object` or
  `json_schema`; `tools` is optional (docs.mistral.ai/api/endpoint/chat; the `json_schema` fields
  aren't expanded there, and the SDK guide prepends the schema to the system prompt; enforcement
  unverified, ecf validates anyway). Spending limits per organisation and per workspace; at the
  limit "API access can be suspended until the next month begins"; pay-as-you-go "draws from your
  prepaid credits" (docs.mistral.ai/admin/billing-usage/usage-limits). API data kept for
  generation, "then for thirty (30) rolling days to monitor abuse (unless zero data retention is
  activated)" (legal.mistral.ai/terms/privacy-policy, as quoted in the step 0 Vibe research).
  Training on paid API use: unverified (help article 404; third-party sources say not by default,
  a policy tracker reports that statement removed in May 2026); the Admin Console's Privacy panel
  has an opt-out.
- **OpenAI data terms** (2026-10-20, (rv): a claude.ai summary of the six full pages, paraphrased,
  not re-read by Claude: help.openai.com articles 5722486, 7730893, 11369540, 8809935, 8983778,
  20001333; the fetch tool and `curl` get 403 there): on Free, Plus and Pro, content may train
  models unless the user turns off **Improve the model for everyone** (Settings > Data controls) or
  chooses **Do not train on my content** in the Privacy Portal; either one covers ChatGPT and Codex
  ("ChatGPT training data controls apply to content processed through Codex"; local workflows run
  on the device). Codex's **Include environments** setting (chatgpt.com/codex/cloud/settings/general)
  is separate and unaffected (the operator found no such setting on their Plus account,
  2026-10-20: this (rv) summary point is unconfirmed, and there is nothing to turn off). A thumbs up or down can make the whole conversation trainable even
  after opting out. Opting out deletes nothing; deleted chats are scheduled for permanent deletion
  within 30 days, except de-identified content or content kept for security or legal reasons. Codex
  usage from local clients, the CLI included, is reachable through the Compliance API (business
  plans), so OpenAI keeps records of CLI use; no CLI retention period is published for Plus.
  Requests go to chatgpt.com/backend-api/codex/responses with `store: false` (source). The Codex
  page names "GPT-5.6 Terra and GPT-5.6 Luna" as replacements from 2026-08-31, which doesn't match
  the 0.163.0 catalogue's `gpt-6.1-sol` / `gpt-6-luna`; `codex debug models` decides. **Before the
  pilot the operator** turns off Improve the model for everyone, and gives no thumbs up or down on
  these runs.
- **Codex sign-in and checks** (2026-10-20, Codex CLI 0.163.0, `CODEX_HOME` =
  `~/Library/Application Support/ecf/accept/codex-home`): the browser sign-in didn't complete
  (only "starting browser login flow" in its log); the device-code sign-in (`codex login
  --device-auth`) works once device code sign-in is turned on in ChatGPT's settings; with
  `-c cli_auth_credentials_store="keyring"` the token goes to the Keychain and no `auth.json` is
  left (without it, a plaintext `auth.json` is written). `codex debug models` lists
  `gpt-6-astra`, `gpt-6.1-sol`, `gpt-6-sol`, `gpt-6-luna`, `gpt-5.6-sol`, `gpt-5.6-terra`,
  `gpt-5.6-luna`, `gpt-5.5` and three hidden ids, all "supported in API": a catalogue, not what
  the account may use. One `codex exec` canary with every tool off and `--output-schema`, model
  `gpt-6.1-sol`, from an allow-listed environment: refused before the model ran, HTTP 400 "The
  'gpt-6.1-sol' model is not supported when using Codex with a ChatGPT account", after a
  "Model metadata for `gpt-6.1-sol` not found" warning. The account is on ChatGPT's free plan
  (operator, 2026-10-20). On Plus (operator, 2026-10-20; the operator's go-ahead), the same
  canary passed: `gpt-6.1-sol`, its entry from `codex debug models` (now listed for the
  account, `tool_mode: code_mode_only` like the others) given as a one-model catalogue with
  `tool_mode: direct`, plus `--disable unified_exec`; answered `{"word":"ok","decoy":"none"}`,
  events only `agent_message`, 7,277 input and 39 output tokens, exit 0. It ran from a scratch
  script, not through the adapter, and used the server's catalogue entry rather than
  `--bundled`; the adapter's own canary runs before the pilot's first `generate`.
- **`claude -p` check** (2026-10-20, Claude Code 2.1.303, the accept install's Claude login, an empty
  scratch folder, an allow-listed environment): `--output-format json --json-schema <s> --model
  claude-haiku-5-5 --system-prompt <short> --tools "" --strict-mcp-config --safe-mode
  --no-session-persistence` returned `subtype: success` with `structured_output`, `num_turns` 2
  (structured output takes one internal turn; no turn cap is set), no `permission_denials`, model
  `claude-haiku-5-5`, about 1.2k input and 178 output tokens, 1.6 s. A decoy file in the folder was
  not read (the model answered "none"), so the tool-less canary passes for the DEV adapter.
- **Mistral** (mistral.ai/pricing; docs.mistral.ai/getting-started/models/): Pro $14.99/month,
  "Full access to Vibe", $15/month API credit; "Mistral Large" $0.50/$1.50 per 1M tokens (version
  not named); Large 4 (26.10), Medium 3.5 (26.04), Small 4 (26.03); the page also lists Z.ai GLM 5.3.
- **Claude Code** (code.claude.com/docs/en/headless, /cli-reference; `claude --help` 2.1.303):
  `-p`, `--output-format json`, `--json-schema` (answer in `structured_output`), `--model`,
  `--system-prompt`, `--tools ""`, `--strict-mcp-config`, `--safe-mode`, `--no-session-persistence`,
  `--setting-sources`; `--bare` uses only `ANTHROPIC_API_KEY` or `apiKeyHelper`; `--max-turns` is
  documented but not in 2.1.303's help.
- **ecf** (e925b3c): `evalrun.py:467` scores a missing expected rule as right; `claude_eval.py:496`
  remembers any root for the gate; `gate.py:245-261` takes the latest run for the key;
  `cards.py:121` limits `author`; `builder.py:126-128` refuses the whole build on one finding;
  `results.py:709` `REPORT_ONLY_BELOW = 20`.

## Plan review findings

Phase R on draft 1 (2026-10-19): 6 Fable reviewers, about 85 findings merged to P1-P31, key claims
spot-checked against the code; all decided by the operator 2026-10-19 (summary in Decisions, the
design above; full list in `state-archive/eval-generate/review-findings.md`).
