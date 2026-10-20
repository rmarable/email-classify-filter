# `ecf eval generate` and `ecf eval grade`: rate ecf with other models

Draft 4 (2026-10-19): draft 3 plus the usage-policy reading and the pilot conditions. Draft 3 added the mix, fields and recipient decisions. Draft 2 came after Phase R (six Fable reviewers, P1-P31; full findings in
`state-archive/eval-generate/review-findings.md`, gitignored). Operator decisions of 2026-10-19
are marked (op). ODs are proposed (OD-534 onward) and ADR 0034, recorded when the operator approves
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
5. **Defaults** (op): PROD generator OpenAI GPT-6.1 Sol through Codex (Luna only if it passes the
   pilot); PROD grader Mistral Large 4 through Vibe; DEV `claude` with full model ids (Haiku 5.5
   generates, Sonnet 5.5 grades).
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
    OpenAI (through Codex) and Mistral AI (through Vibe), Anthropic already allowed. A new provider
    needs its own ADR. SPEC §12.4 gets a paragraph per provider in the TypeSafe form. Both usage
    policies were read on 2026-10-19 (Facts); the operator accepted the PROD pilot on these
    conditions (op): (a) a refused request is never retried, reworded or replaced, so a group can
    come up short and the report gives refusals per group (OpenAI's policy bars "circumventing our
    safeguards"); (b) every generator prompt states its purpose in fixed words the operator
    approves: the emails test an email-security filter, stay on this machine and are never sent;
    (c) the pilot stays at 20 seeds and the operator accepts the risk to the ChatGPT account;
    (d) Extortion is in the pilot like the other groups.
11. **Tools without tools** (op; P3, P20): no adapter is used until its tool is shown to run with no
    tool access and no MCP servers (Design, Adapters). The release waits for the Mistral adapter to
    pass that test.
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
- **OD-536** Credentials and isolation: provider tools' own sign-ins in ecf's own config folders;
  refuse on key variables; no key held or set by ecf.
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

One table in `src/ecf/eval/tools.py`: argv template, schema mode, output source and parser, minimum
version, sign-in check. At each run's start ecf checks the tool's version and that every flag it
passes appears in `--help`, and records the argv digest and version.

| Provider | Call (prompt on stdin, from an empty work folder under ecf's data folder) | Sign-in and config |
|---|---|---|
| `claude` (DEV) | `claude -p --output-format json --json-schema <s> --model <full id> --system-prompt <adapter text> --tools "" --strict-mcp-config --safe-mode --no-session-persistence` (turn limit verified by one run; `--max-turns` is not in 2.1.303's help) | ecf's `CLAUDE_CONFIG_DIR` and login (`ecf claude --login`, `src/ecf/claude_wrapper.py:389`); transcripts purged after |
| `codex` | `codex exec --sandbox read-only --ephemeral --skip-git-repo-check --ignore-user-config --ignore-rules --json --output-schema <s> -o <file> -m <id>` | a separate `CODEX_HOME` under ecf's data folder with its own `codex login` |
| `mistral` | `vibe --prompt ... --output json --max-turns 1` with tools emptied (method to verify) | Vibe's own sign-in; ecf reads `active_model` from Vibe's config and refuses on mismatch, printing the Vibe command; never writes it |

Every adapter:
- runs under an allow-listed environment built like `claude_wrapper.base_env` (`ENV_ALLOW`,
  telemetry off), and **refuses**, naming the variable, if `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
  `CODEX_API_KEY`, `MISTRAL_API_KEY` or a provider base-URL variable is set (P4);
- must pass a **tool-less canary** before use: a decoy file in the work folder and a prompt asking
  for its contents; any tool or command event, or the decoy's contents in the answer, fails closed.
  Codex runs with `--json`, and any command event drops the email (P3);
- uses `subprocess.run(..., shell=False, capture_output=True, timeout=..., start_new_session=True)`
  with the process group killed on timeout; parses one JSON object, size-capped, strictly;
- stops the run after 3 consecutive failed calls of any kind, showing the last error line; a usage
  limit is a stop, not a detected state; `--resume` continues from `work/`;
- logs exit codes, byte counts, times, refusal categories and digests only (P27; `stdout`,
  `stderr`, `refusal`, `seed`, `email` added to the redacted keys, `src/ecf/log.py`).

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

The client (`ecf`), except the gate guard and `expect`'s rules-engine call, which go through the
service API (`ecf` never imports `ecf_server`).

## Estimates (to be replaced by the pilot's figures; P18, P28)

| Item | Estimate |
|---|---|
| Codex calls for 300 kept | about 30 seed calls + 30-60 email calls at K 5-10 (more with drops) |
| Plus windows (Sol, 15-160 per 5 h) | 1 at the top of the range, up to about 6 at the bottom; unverified that one call is one message |
| Grader | about 300 calls; about $0.50-1.20 at Mistral's listed Large price |
| DEV (30) | about 10-15 `claude -p` calls on ecf's Claude login |
| `ecf eval run --claude` on 300 | about 10-16 min dispatch plus facts prep (SPEC §20 figures for 304 cards); US$13-15 API-equivalent |
| Operator labelling | about 5-10 h for 300, every card blind |

## Steps

0. **Facts**: the vendor terms (usage policies read by the operator; retention and training; where
   each sign-in is stored); each tool's tool-off method, MCP behaviour and what it sends besides
   the prompt; Vibe's per-run model and JSON shape; Claude Code's turn limit with `--json-schema`.
1. **Shared pipeline**: mix, seeds, emails, checks, card changes, set layout, manifest, grade (as a
   result file), expect, report, resume, gate guard, logging. Tests with a fake executable on PATH
   (as `tests/test_claude_wrapper.py`'s `fake_claude`) and recorded JSON, including a limit, a
   refusal, a tool event and the canary; no network.
2. **The `claude` adapter and a DEV run** of 30, after the operator writes the realism rubric and
   the pilot pass mark (P25). Plumbing only.
3. **Codex and Mistral adapters**, the model list, the family check; each passes the canary.
4. **PROD pilot** (real-service test, operator's go-ahead each time): the same 20 seeds through Sol
   and Luna; the operator rates realism blind; drops, refusals per group (ceiling in the pass mark),
   repeated openings, phrase overlap; time, tokens and plan messages per call (meter read before and
   after); grader on those 40; operator seconds per card.
5. **The first PROD set**: 300 kept; grade; blind labels; expect; two C runs; report.
6. **SPEC and docs**: ADR 0034, the ODs, §1.2 (Later items incl. the stress test), §9.3, §12.2,
   §12.4, §16, §20, README, DISCLAIMER, SECURITY.md, user guides, CHANGELOG.
7. **Release** (minor), only when the Mistral adapter passes the canary (decision 11).
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
- **Mistral usage policy** (legal.mistral.ai/terms/usage-policy, effective 2026-06-11, fetched
  2026-10-19): fraud and scams prohibited ("Phishing attempts", "Impersonation" listed); no
  generating content "intended to deceive others"; no rule on spam or simulated content; no testing
  exception. Mistral only grades here, which these rules don't cover; Mistral as a generator would
  be a grey area.
- **Vibe** (docs.mistral.ai/vibe/code/cli/work-with-cli, /api-keys-profiles): `--prompt`,
  `--max-turns`, `--enabled-tools`, `--output text|json|streaming`, `--agent`; programmatic mode
  defaults to the `auto-approve` agent; browser sign-in by default for a Mistral provider;
  otherwise `MISTRAL_API_KEY` or `~/.vibe/.env`; the model is `active_model` in
  `~/.vibe/config.toml`. Not installed on this Mac. Unverified: per-run model, emptying tools, JSON
  shape, sign-in storage, Pro quota.
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
