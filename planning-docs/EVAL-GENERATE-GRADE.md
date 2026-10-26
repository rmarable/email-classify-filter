# `ecf eval generate` and `ecf eval grade`: rate ecf with other models

Draft 26 (2026-10-25): draft 25 plus the hygiene fix's Stage B (OD-554; plan in 29 drafts, each checked by Fable, every finding decided by the operator; `state-archive/hygiene-fix/review-findings.md`): an address's local part is no longer read as a domain, the retry gives advice by kind and quotes checksum numbers, a refusal of the retry is a hygiene drop, the sender addresses get the brand check, and the second diagnostic's criteria (Checks). Draft 25 (2026-10-24): draft 24 plus the hygiene fix's Stage A (plan in six drafts, each reviewed by Fable: Phase R with two agents, then five follow-ups; all findings accepted by the operator 2026-10-24; `state-archive/hygiene-fix/review-findings.md`): the card scan reads each header field on its own, and a DEV switch, `--show-findings`, prints each hygiene finding's text in the operator's terminal and writes nothing (Checks). Stage B, the retry fix, is planned from what the switch shows. Draft 24 (2026-10-22): draft 23 after Phase R (two Fable agents, G1-G10, all accepted by the operator 2026-10-22; `state-archive/eval-draft23/review-findings.md`): a judged set also needs Claude-written seeds; the agreed-safety-field limit; Sol's measured figures; the operator's workload and Sol's Plus windows per 300; the first PROD run as one sequence; grading checks families before its first call (a build step). The operator kept the rule that Claude never judges. Draft 23 (2026-10-22): draft 22 plus the operator's decision not to add Gemini or any other platform (OD-550; ADR 0036 rejected): the judges are Mistral Large 4 (OD-548) and Sol, two judges with no third, and what follows from that (Judges). Draft 22 (2026-10-21): draft 21 plus step 4's measurement on the 57 DEV emails (every predicted count reproduced; the expected rule matches the operator's own labels on 50 of 57) and OD-549 recorded in SPEC; step 1 done (6c2af38), steps 2-3 built (branch A 5badfa3 with the full gate only, no Fable diff review before its merge; reviewed after it (AA1-AA8, all accepted), fixes on branch eval-scoring-fixes; branch B 6d6a42e after a Fable diff review and six re-checks of its fixes). Draft 21 (2026-10-21): draft 20 plus **Settle only what decides something** (Judges): `sender_type` and `priority` asked only when they decide the expected rule, the expected rule and safety computed from the judges' values, durable per-field label provenance, undecided cases out of the figures, the same rules as the run, the safety fields kept in ecf's figures; after seven Fable reviews, all findings accepted by the operator 2026-10-21; OD-549 proposed, recorded in SPEC with step 4's measured counts (draft 22). Draft 20 (2026-10-20): draft 19 after Phase R on the judges (four Fable agents, J1-J20, all accepted by the operator; OD-546, OD-547, ADR 0036 proposed). Draft 19 (2026-10-20): draft 18 plus the pilot's outcome (rating skipped, Sol adopted, Luna not) and **judges**: two or three non-Claude models of different families set the labels, the operator settles only their disagreements (operator decisions 2026-10-20; section Judges; Phase R pending). Draft 18 (2026-10-20): draft 17 plus the operator's approval of the fixed prompt words (decision 10b) and the pilot's shared seeds (Phase R, two Fable agents, S1-S8, operator accepted all; OD-545). Draft 17 (2026-10-20): draft 16 plus the DEV grader comparison's results and what the labelling showed (Phase R, two Fable agents, P1-P8, operator accepted; OD-544): graders read the labeller's mail-client view, the report gives set detectability, confirming a generated set never names the assignment, and every labelling and rating page shows the phishing definition and a signs box; the address book is deferred. Draft 16 (2026-10-20): draft 15 plus the DEV run's results and the fixes they led to (Phase R, two Fable agents, P1-P12, all recommendations accepted by the operator; OD-543): no opening-line drop, seed top-ups, hygiene kinds logged, no self-grading, and the labelled DEV grader comparison; the Mistral free plan's credit as the cap (Cost guards). Draft 15 (2026-10-20): draft 14 plus the operator's decision that the DEV run may use `claude -p` on the operator's own Claude plan sign-in (OD-542; Facts: Anthropic terms). Draft 14 (2026-10-20): draft 13 plus the approved realism rubric and pass mark, `planning-docs/RUBRIC.md` (OD-541): four questions (three 1-4 scales, one yes/no), seven pass rules, a near-miss rule (20 fresh seeds for both models), refusals at most 2 overall and 1 per group, at most 3 check drops; the intended group shown as context. Draft 13 (2026-10-20): draft 12 plus how the realism rubric is made: three independent drafts (Codex, a Mistral model, Claude), the operator combining parts of each (operator decision 2026-10-20; `planning-docs/RUBRIC-GENERATION.md` draft 2). Draft 12 (2026-10-20): draft 11 plus the `gpt-6.1-sol` canary on Plus, which passed (Facts). Draft 11 (2026-10-20): draft 10 plus step 3 built (the `codex` adapter, the Mistral grader through the service, the model list and family checks) and the operator's three decisions on it (OD-540), and the Codex code-mode fact (Facts). Draft 10 (2026-10-20): draft 9 plus the `claude` adapter (step 2, built; the DEV run waits for the rubric). Draft 9 (2026-10-20): draft 8 plus two corrections to the Vibe facts and where Mistral API keys come from (a peer session's re-read of Vibe's source, (rv)). Draft 8 (2026-10-20): draft 7 plus the Codex sign-in and the two Codex checks (Facts), step 1 built, and ADR 0034 and OD-534 to OD-539 recorded in SPEC (build approved by the operator, 2026-10-20). Draft 7 added OpenAI's data-control pages and the `claude -p` check. Draft 6 added the Mistral API facts. Draft 5 added the step 0 findings and the mixed route (Codex CLI generates, Mistral's API grades). Draft 4 added the usage-policy reading and the pilot conditions. Draft 3 added the mix, fields and recipient decisions. Draft 2 came after Phase R (six Fable reviewers, P1-P31; full findings in
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
   operator labels all 300 of the first PROD set, every card (no bulk confirmation, P28). (amended,
   draft 21: on judged sets `sender_type` and `priority` are not scored; amended, OD-547 and
   OD-550: the judges label, the operator settles their disagreements.)
8. **Expected rule and safety** (op; P2): after the labels, ecf's rules engine (the `ecf rules test`
   path, `src/ecf_server/ruletest.py`) computes each card's expected rule and safety flags from its
   confirmed labels and facts; the operator confirms them (amended, draft 21: on judged sets expect
   keeps the labels' provenance and computes the rule from the judges' values). A set without them
   reports field agreement only and says "no fraud-guard or injection cases".
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

generate → grade → label (blind) → run → report. Judged sets (amended, draft 21): generate → grade
(judges) → agree → expect → settle → expect (→ settle → expect when expect says so) → run → report.

```sh
ecf eval generate --root <new folder> [--mix <file>] [--count N] [--generator <p>[:<model>]] [--dev] [--parallel N] [--yes]
ecf eval grade    --root <set> [--grader <p>[:<model>]] [--yes]
ecf eval label    --root <set> --review all        # existing blind path
ecf eval expect   --root <set>                     # rules engine fills rule and safety; you confirm (judged sets: expect confirms; draft 21)
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

1. **Seeds**: one call per group, asking for 1.5 times its count; when a bucket's seeds run out
   while it is short and under 3 times its count in tries, the group asks again for 1.5 times
   what it still needs, at most 3 times per group (OD-543: the DEV run showed the first call
   alone can't reach the try limit). A seed is structured facts: sender role, what's asked, amount or none, urgency,
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
  dropped; a refusal is recorded and dropped, never retried (decision 10a). Stage B (draft 26,
  OD-554): the retry adds fixed advice by kind (checksum numbers quoted, at most five; SSN-shaped
  numbers; lookalike local parts), and a refusal of the retry is a hygiene drop (`refused_on_retry`),
  since the retry prompt is ecf's, not the seed's. The scan blanks addresses before its host scan,
  reads NFKC-normalised text and checks the sender and reply-to addresses for brands. Acceptance:
  the 2026-10-25 diagnostic's printed values re-scan clean (local parts in address form), and a
  second diagnostic (`--dev --show-findings --count 10`, same mix) has no `domain` finding on a
  local part or handle, no all-zero number finding, at most 3 hygiene drops, and romance not
  selected; its retry figures are reported, not judged.
- The card scan reads each header field on its own, named by its field path (`<case id>:subject`
  in generate, `<id>.md:subject` from `ecf eval build` and the settle, agree and expect gates),
  dict keys included except the card's top-level field names, which are fixed, and the body and
  HTML whole (draft 25): one JSON dump of the
  header turned a newline into the characters `\n`, so a wallet, an international phone number or
  a secret at a line start in hidden text or attachment text was missed, a file name there read
  with a stray `n`, and a reserved web address ending a header field was flagged. Checked on the
  committed set, the held-out sets and the six generated sets: no card newly fails.
- `ecf eval generate --dev --show-findings` (draft 25): every hygiene finding, on both passes,
  printed in the operator's terminal as it happens (seed, group, delivery, pass, field, kind and
  value, control and format characters escaped; the case id in the field is the slot the email
  would take, the seed id names the try); it writes nothing, so the logs keep the kinds
  only (P27), and it is refused without `--dev` or unless standard input and output are a
  terminal (a guard against a mistake, not a control: the operator runs it in Terminal, Claude
  never does). Printing generated text to the terminal is a stated exception to "kinds only".
  It exists to find what the scan trips on (the PROD rehearsal, prod-30-sonnet-2026-10-24, lost
  all of romance and other-ic3 to `domain` findings, 2026-10-24) before the retry is changed
  (Stage B).
- Near-duplicates across the whole set are dropped (P30): identical text, or word 5-gram
  containment of 60% or more when both emails have at least 20 5-grams. A shared opening line is
  not a drop (OD-543: the DEV run lost 20 of 46 emails to shared greetings); repeated openings are
  reported. A drop counts against the later email's group. Long footers shared by one sender can
  still reach the limit (a stated limit). A hygiene failure's finding kinds (names, never text)
  are logged for both passes, and retries that pass are counted (RUBRIC.md: reported, not
  limited).
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

One call per email: the raw email and all 13 schema v3 fields to answer (op; option A: `category`,
`priority`, `requires_action`, `requires_reply`, `payment_related`, `deadline_mentioned`,
`sender_type`, `fraud_risk`, `bank_detail_change`, `regulator_claim`, `phishing`, `claims_internal`,
`private_individual`; the rules read `category` and `sender_type` as well as the fixed fields, so
all 13 are needed to compute the expected rule; OD-486; on judged sets 11 plus the judges'
combinations, amended, draft 21), with the shipped descriptions from `src/ecf/data/schema_v3.yaml`,
returned as JSON. The operator labels the same 13. Blind: no ecf answer and no label. The grader's
input is the email as a mail client shows it, the same view the operator labels from
(`cli_card_review.screen`; OD-544: the raw email carried the seed's `Authentication-Results`, which
only scams failed, and neither the labeller nor the classifier sees it), not the classifier's
excerpt and signals; each grade records its input. Every labelling and rating page shows the
`phishing` definition in full (not folded) and a "signs to look for" box of general red flags, never
tied to an email (OD-544; text below). Confirming a generated set's labels (`ecf eval label`) shows
a counter, never the case id, title or test line, which name the assignment. The report gives **set
detectability**: of the emails written as scams, how many each reader (your labels, each grader with
its input, each ecf result) answered phishing, and the seeds' authentication results by kind;
reported, never scored. Signs box text (Claude-written machinery, approved by the operator
2026-10-20): A charge, invoice, order or account problem you didn't expect; pressure (a short
deadline, a threat, "cannot be reversed"); it wants a phone call, a reply with details or a payment
instead of you checking your own account; the sender isn't the company it speaks for (a "help",
"billing" or "support" domain), or Reply-To goes somewhere else; vague about who it is or what you
bought, a generic greeting; new bank details, gift cards, crypto or secrecy; an attachment or link
pushed as the next step. The address book (known contacts in the recipient configuration) is
deferred until after the first PROD set: the reviewers found it would make the set a lookalike test
of a few contacts. Before grading, the blind labels are frozen (their digest recorded); the grader's
accuracy is computed once against that version and kept, and later relabels are logged with the
digest they changed and reported separately.

### Judges (draft 20; operator decisions 2026-10-20, OD-547; Phase R J1-J20 accepted)

The operator labels nothing blind, rates nothing and confirms nothing the judges agree on: the
only human step is settling disagreements (`ecf eval settle`).

**Roles and families**
- Claude generates (`claude:claude-sonnet-5-5`, or Haiku) in a PROD mode that records a
  **generator-family waiver** in the manifest (amends decision 4; operator decision): the report
  says Claude wrote the emails Claude is scored on, and "NOT A MEASUREMENT" stays only for a judge
  sharing the classifier's family (J1). Sol stays a listed generator.
- The `claude` generator runs `claude -p` on the operator's own plan sign-in (OD-542) and is
  marked **operator-only in code**; generation for other users goes through the service with
  ecf's stored Anthropic key, built when ecf is offered to others (J2).
- **Judges** are non-Claude, every pair of {generator, seed writer, judges} from different
  families (`models.check_judge` for each judge against the writers, `check_pair` between the
  judges; J11), none grades itself. **The judges are Mistral Large 4
  (`mistral:mistral-large-4-0`, OD-548) and OpenAI's Sol (`codex:gpt-6.1-sol`, on the ChatGPT Plus
  plan): two judges, no third** (operator decision 2026-10-22, OD-550: no Gemini and no other new
  platform; ADR 0036 rejected). Draft 20 had Mistral and Gemini, with Sol only as a tie-breaker
  (J15). What two judges mean:
  - **No majority.** `category` takes a majority only with three judges, so every `category`
    disagreement goes to the operator (9 of the 57 DEV emails), and a `category` both judges agree
    on stands unasked. The 7 expected rules that differed from the operator's own labels (OD-549)
    came from such agreed categories; a third judge outvoted 2-1 wouldn't have changed them.
  - **Only Claude-written sets can be judged** (G1). With OpenAI and Mistral as the judges, the
    generator and the seed writer must both come from neither family: Claude generates under the
    waiver (OD-547) from its own seeds or a Claude `--seeds-only` run. A set an OpenAI model
    generates, or a Claude set reusing seeds an OpenAI model wrote (Terra wrote the pilot's,
    OD-545), leaves one eligible judge: `ecf eval grade` refuses the OpenAI judge before any call,
    and `ecf eval agree` then refuses a set with one judge's grades. Mistral has no generator
    role. Sol stays a listed generator for sets the operator labels. So every judged set is
    written and classified by Claude (G8): the second, non-Claude set ADR 0034 wanted needs the
    operator's own labels. Claude never judges a PROD set (operator, kept 2026-10-22; DEV allows
    it for plumbing trials): its labels would be the truth its own classifier is scored against.
  - **An agreed safety field is never asked** (G3). Both judges agreeing settles a boolean or
    `fraud_risk`; the only backstop is the assignment-contradiction check, on `phishing` and
    `fraud_risk` only. On the 57 DEV emails the expected safety flags were identical either way
    (OD-549), but the judges agreed on a wrong value in 45 of the 591 fields both agreed on, among them
    `fraud_risk` twice (medium where the operator said high), `bank_detail_change` once,
    `deadline_mentioned` 8, `requires_reply` 7, `requires_action` 5, `payment_related` 5,
    `private_individual` 2 and `category` 15; none was asked.
  - **The judges' own figures** (G4; the 57 DEV emails, 12 fields, phishing and `fraud_risk`
    labels assignment-based): Large 4 87.7% (phishing 98%, `fraud_risk` 79%, category 68%); Sol
    86.4% (phishing 96%, `fraud_risk` 88%, category 60%) (OD-548, OD-550).
  - **The operator's workload** (G5, scaled from the 57): about 153 emails and 210 fields per 300
    in the first sitting, about 11 emails and 11 fields back once (221 fields in all); a third
    judge would have saved at most about 26 emails and 47 fields.
  - **Sol's Plus limits** (G6). Sol graded the 57 within the allowance (2026-10-21; 6.9 s a call,
    8.6k tokens in). Per 300: about 35 min of calls and 2.6M input tokens, $0 beyond Plus; at
    15-160 Sol messages per 5 h, 2 to 20 windows (about 10 h to 4 days) if a call is one message.
    The run will stop at the limit at least once (once at the top of the range, about 19 times at
    the bottom) and resumes later with `--resume`. Sol's judge sign-in keeps the pilot's data controls ("Improve the model for
    everyone" off, no thumbs up or down; Facts) (G9).
  - **Both must answer.** An email either judge refuses, or a call that failed (not a usage limit)
    and wasn't retried, goes to the operator whole (J10, G7). While a grade run is stopped,
    `--resume` asks failed calls again; after it completes, the only retry is deleting that judge's
    grade file and grading again with `--resume`.
  - **Grading checks families first** (G2, built on branch eval-grade-families): `ecf eval grade`
    already refused a judge of the generator's family before any call; it now also refuses one of
    the seed writer's family (before the canary call, not at `agree` after Sol's allowance is
    spent), and a Claude judge (before, only `agree` refused it), through one check
    (`models.check_judge`) shared with grading itself and `agree` (the report refuses a grade file
    from either writer); the refusal names the role.

**Runs and agreement**
- Each judge is an ordinary `ecf eval grade` run (`pair: grader:<model>`, the mail-client view,
  13 fields); no new role flag or command (J16). API judges share one service route and provider
  table and one client adapter with the cost cap and pacing (J17).
- `ecf eval agree --root <set>` reads `grades/` and decides per case and field: **booleans and
  `fraud_risk` need every judge to agree**; `category` and `sender_type` take a majority when three
  judges answered; `priority` is report-only (J5, J20; amended, draft 21: `category` majority only,
  `sender_type` and `priority` unasked unless they decide the rule; OD-550: two judges, so no
  majority ever applies). A field counts only when every
  judge gave a valid value; an email any judge refused or left blank goes to the operator whole
  (J10). Agreed values go into the cards (written all at once, one rebuild, one atomic confirm),
  confirmed `{by: "models", judges, on}` (now per-field `labelled_by`, amended, draft 21);
  `work/agreement.jsonl` records each judge file's digest, and the report recomputes agreement from
  those files and refuses a mismatch or two files from one model (J9, J19).
- **Decision 8**: the expected rule and safety computed from agreed labels confirm `by: models` too
  (J18). **Decision 7** (only operator-confirmed labels count) is amended: agreed and settled labels
  count, every figure states the share of each (J20). (amended, draft 21: minus `sender_type` and
  `priority` on judged rows, the share per field; expect keeps provenance.)

**Settling** (amended, draft 21; `ecf eval label` refuses any judged row): a phone page first (the
dispute list as a private claude.ai page with saved answers, then `ecf eval settle --from <answers>`
imports), later the terminal; a seeded shuffle, a counter, the mail-client view, only the disputed
fields, the phishing definition and signs box; agreed fields and the judges' answers shown only
after the operator answers; `ecf eval label` refuses cases with open disputes (J14). The synthetic
text goes to claude.ai hosting, as preset C.

**Safeguards without operator labelling**
- **Judges measured first** (J4): each PROD judge grades the 57 DEV emails the operator labelled
  (step 2), giving its per-field accuracy and Cohen's kappa before it sets any label.
- **Per-field kappa and base rate** between judges; a field below 0.6 is report-only for ecf scoring
  (not the fixed items or `fraud_risk`, amended, draft 21); an agreed label contradicting the
  assignment (a scam seed labelled not phishing, or no fraud risk) is flagged and left out of
  scoring (J7).
- **No circular judge accuracy** (J6): the report gives pairwise judge agreement and each judge's
  accuracy on operator-settled fields only; `report.confirmed` carries the `by` of every label (now
  per-field `labelled_by`, amended, draft 21).
- **Three figures per field** (agreed, settled, combined) and **comparisons computed twice** (all
  labels; settled only), the agreed share printed; the Jev re-check (OD-538) uses only operator
  settled or labelled values (J12, J13).
- **Gate**: a `by: models` confirmation (now `labelled_by`, amended, draft 21) never counts outside
  a generated set (`is_confirmed`, `carry_over`, the service loader); `ecf eval build` strips it
  (J8).
- The report counts disputed emails and fields, the operator's settling time, and emails refused
  by any judge (J3, J20). Measured guide: two Claude graders on the 57 DEV emails disagreed on 11%
  of fields (58% of emails had at least one); judges of different families will likely disagree
  more; the first run sets the figure.

**Performance** (J19): judges run in parallel with per-judge call logs; a `--pace` interval and a
60 s read timeout for API judges; the service caches `verify` per set.

**Gemini**: not pursued (operator decision 2026-10-22, OD-550: no new platform); ADR 0036 is
rejected. The research behind it stays under Facts, marked not pursued.


### Settle only what decides something (draft 21, measured in draft 22; OD-549)

Seven Fable reviews (draft 1 Phase R, two agents, P1-P8; drafts 2-7, two agents each, Q1-Q9,
R1-R12, T1-T10, U1-U12, V1-V10, W1-W11; all accepted by the operator 2026-10-21; full text in
`state-archive/eval-settle/review-findings.md`). Code checked against `8789d6b`; plan line numbers
(`:139-157` and the like) refer to draft 20 at that commit. Step 4 measured the table on
2026-10-21 (main `6d6a42e`); OD-549 records it in SPEC.

#### Context and measurements (57 operator-labelled DEV emails; judges Mistral Large 4 + Sol)

| | emails you settle | fields | rule undecided |
|---|---|---|---|
| today (OD-547) | 35 | 53 (sender_type 17, fraud_risk 12, category 9) | all (defect 1) |
| sender_type, priority never asked | 25 | 36 | 6 |
| this plan | 29 in the first sitting; 2 of them come back once for sender_type | 42 | 0 |

Estimated before the build by two reviewers from the labels and card facts; measured by step 4 on
2026-10-21 (below), every figure as estimated. Labels the judges agree on match the
operator 546/591 (92.4%) over the 11 asked fields (573/631 with the agreed sender_type values).
sender_type: kappa 0.56/0.55 per copy, the judges match the operator 68/114 (60%); priority: kappa
0.80 vs 0.54 on the two copies, 71/114 (62%). Neither is a usable ecf figure. The 6 cases whose rule
depends on sender_type span both copies (haiku: dev-forums-001, dev-social-003; sonnet:
dev-forums-002, dev-social-001, dev-forums-003, dev-social-002): all `category: notification`,
`requires_action` false, no fixed rule fires, judges automated vs vendor/company, the operator said
automated; the rule is `automated_notification` (`starter_rules.yaml:56-62`), one of the two default
rules that hide ordinary mail (the other is `marketing`, which archives; corrected in draft 22). Four have no other dispute (asked in the first sitting, because expect
runs before settle); two also have a first-round dispute (dev-forums-003 category and deadline;
dev-social-002 fraud_risk) and come back once. No case depends on priority (it reaches the rules
only in `bug_report`, `starter_rules.yaml:8-13`). No real set holds judge-confirmed rows (agree ran
only on scratch copies), so nothing needs migrating. No user-facing doc mentions agree, settle or
expect. "Rule undecided 0" is not "rules verified": the judges' agreed sender_type matches the
operator 27/40; none of the 13 differences reaches a rule in these 57 (masked by fraud_guard,
`marketing` or an agreed category), but an agreed wrong value on a notification card would set a
wrong expected rule unseen (accepted: agreed values stand, OD-547). A computed trigger on one of the
6 (not checkable without the engine) could only move it from "sent to settle" to "rule written".

Existing defects in the judged flow (OD-547), verified at 8789d6b:
1. **No expected rule on judge-labelled cards**: `expect.labelled` needs all 13 fields
   (`expect.py:30-35`); agree and settle strip `priority` (`agree.py:153,164`, `settle.py:105`).
2. **Missing expectations read as passes**: `score_parts` sets `fields["rule"]` true when no rule is
   expected (`evalrun.py:479-480`), `safe = rule is not None` then passes safety (`:483`),
   `end_to_end` reads absent rule/safety as passed (`:515-516`), and `results.compare` pairs every
   case on `correct` (`results.py:216-227`).
3. **Provenance flips**: expect rebuilds (`expect.py:70`); `carry_over` keeps the bare new row when
   the expected hash changed (`labels.py:82-83`, `builder.py:153-156`); `labels.confirm` writes no
   `by` (`labels.py:92-102`); settle confirms with `{}` (`settle.py:112`), so a whole case reads
   "operator" (`labels.py:57-62`) and its judge-set fields count as the operator's (`report.py:311,
   347-356`; J6, J13); the assignment-contradiction check and `judged` are case-level
   (`report.py:199-206, 310`). An unchanged card keeps its confirmation through a rebuild (the
   `Expected` defaults, `cards.py:77-100`, dump the same with `rule: null`).
4. **Different rules**: `/v1/eval/expect` evaluates the applied rules (`api.py:826-828`); `ecf eval
   run` the starter rules with the shipped schema (`evalrun.py:256,740,755,1606`; digest
   `composed_digest(schema, None)`, `:829`).

#### Decisions (OD-549)

1. **sender_type and priority** (P1, Q2a, R3, T7, U5): on a generated set, for every row with
   `labelled_by` (judge-labelled), they are never scored, in ecf's figures or in the judges' figures
   (J6, J13), and not asked when the judges disagree, by decision, not per-set kappa. The one key is
   the row's `labelled_by`: the service's loader reads it into `Case.judged` (`evalrun.py:295-310`),
   which `score_parts` and `rescore` use; `author: generated` scopes only decision 4. They are asked
   only when they decide the expected rule (decision 2). Every other field is asked when the judges
   disagree; the safety fields always. Operator-labelled rows (the DEV sets: no `labelled_by`) keep
   every field in the grader comparison (J4; OD-548's basis unchanged).
2. **Expected outcome** (P2, P7, Q7, R2, T5, T8, U1, U7, U10): expect evaluates every combination of
   the values of sender_type and priority, the other fields at their card values. A value on the
   card (the operator's answer, or a DEV card's label) is used alone; otherwise each judge's value,
   from the judges named in the row's `labelled_by.judges` (W9), read from the grade files the
   latest `work/agreement.jsonl` entry names; every file must be present with its recorded sha256,
   else expect refuses. At most 3 values per field (judges are at most 3, OD-547), 9 combinations. A
   judge that refused, left a field out or gave a value outside the schema contributes no value
   (W10); a field with no value at all goes to settle. Each combination is applied to the card's raw
   labels before `stand_in` runs (`ruletest.py:163-167`, with the hand-set fixed labels). The case's
   rule and both safety flags are written only when every combination gives the same rule and the
   same flags; otherwise the rule is `null` and both flags are written `false` (the defaults), in
   the same write, so no stale flag survives (the card format is unchanged: `Safety` flags stay
   booleans, `cards.py:79-83`). The base reply (no variants) is evaluated with the unasked field
   absent; expect reads only `variants` when it sent any. A field *decides* when two combinations
   that differ only in it give a different rule or flag; every deciding field goes to settle in one
   entry (`reason: "decides the rule"`, `fields`, and `judges: {field: {judge: value}}` so settle
   can show them after the answer). A ruletest error in any combination makes the case undecided,
   logged with the error text (`ruletest.py:233-237`), never sent to settle. Only the rule and the
   two safety flags are compared (`flag`, `actor` aren't scored). Expect never uses a reply where
   the field was absent (W5). sender_type reaches the rules directly (`fixed_rules.yaml:40`
   regulator, `:55` person/friend/family; `starter_rules.yaml:60` automated), not through derivation
   (`derive_labels` skips labelled items, `schema.py:182-190`).
3. **Flow** (R6, T10, U8, U11): `agree`, `expect`, `settle` (the phone page: `settle --export`, then
   `settle --from`), `expect`; when expect sends cases back after the first sitting (about 2 of 57),
   `settle` in the terminal and `expect` once more. `agree`, `settle` and `expect` each print the
   next command. Expect skips a case with an open dispute (R9) and decides it after settle. A whole
   case asks the 11 asked fields, plus sender_type and priority when every judge refused, in one
   sitting; the operator's answers to those two on a whole case are not scored either (the row has
   `labelled_by`; accepted, the number is tiny; W11).
4. **Nothing expected, nothing scored** (P3, Q3, R7, T4, U1, U9): on a generated set, a case whose
   expected rule is `null` is *undecided*: `score_parts` writes no `rule` or `safety` entry in its
   `fields` and sets `safety` to None (`CaseResult.safety: bool | None`); `summarize` leaves it out
   of `correct`, `accuracy`, `wilson95`, `unsafe` (tests `safety is False`), `rule_scored`, the
   per-field rule and safety figures and `gate_passed`, and `results.compare`'s pairs (OD-486's
   comparison), and from `routereport`'s unsafe list (`routereport.py:286`, `safety is False`; W1);
   it is counted as `rule_undecided`; it stays in the per-field label figures and in `confirmed`.
   The summary gains `scored` (cases with an expected rule), the denominator of `accuracy` and
   `wilson95` and the `bool(n)` of `gate_passed` (`evalrun.py:557,575`; W2). `CaseResult` gains
   `undecided: bool = False` (the default reads older result files; `_counts`, `results.py:430`,
   reads it); result files already made keep their `correct` until rescored. `correct_def` reads
   "rule+safety+fixed_items; generated sets: cases with an expected rule". The frozen synthetic
   set's figures are unchanged.
5. **Provenance per field, durable** (P4, Q1, R1, R4, R5, T1, T2, T3, U2, U3, U4): each labels.jsonl
   row of a generated set carries `labelled_by: {judges, settled: [fields], labels_sha256}`. agree
   writes it with `settled: []`; settle adds the fields the operator answered. The case's source
   (`labels.source`) is derived: operator when `settled` holds an asked field the judges disputed or
   the case was whole; `confirmed.by` is dropped. `is_confirmed` returns False for a row with
   `labelled_by` unless `models_ok` (the gate moves from `confirmed.by`; used by the run loader,
   `report.confirmed`, `pending`, `carry_over`). `labels_sha256` is the hash of `expected.labels`,
   written only when a case is confirmed (agree, settle). `labelled_by.judges` is agree's existing
   `"provider:model"` list (`agree.py:180`). **Labels and rule move together** (V3): any write that
   changes a card's labels (agree, settle) also writes `rule: null` and both flags `false`, in the
   same card edit before the rebuild (`review.set_labels`, then `expect.write(path, None,
   defaults)`; W8), so a card is never confirmed with a rule computed from other labels; expect sets
   them again. In the normal flow it is a no-op (those cards have no rule yet); it matters after a
   hand edit. `carry_over` keeps `labelled_by` when the set is generated (`models_ok`), regardless
   of the hash; outside a generated set it drops it with the confirmation (J8 unchanged). One
   re-confirm rule, for a normal run and a rerun after a crash alike: expect re-confirms a row with
   `labelled_by` when its labels hash matches `labels_sha256`, it has no open dispute, and expect
   isn't sending it to settle in this run. Expect removes `confirmed` from every row it sends to
   settle, in the same atomic write. An `agree` rerun skips a row with `labelled_by` whose labels
   hash matches (judged, merely unconfirmed); on a mismatch (a hand edit) it re-decides the row,
   keeping the fields in `settled` at their card values and `settled` itself. A disputed row (no
   `labels_sha256` yet) is re-decided, giving the same result on an unchanged card; the duplicate
   `disputes.jsonl` entry is harmless (the latest wins; W6). A dispute entry whose fields are all in
   `settled` isn't open (`open_disputes`). The report checks phishing and `fraud_risk` against the
   assignment for every row where they aren't in `settled`; `judged` means any row has
   `labelled_by`. `ecf eval label` (`labels.confirm`, and `--review`, `--add`, `--import-fixed`,
   `cli.py:1038-1069`, which write labels; W4) refuses a row with `labelled_by` on a generated set,
   naming `ecf eval settle`, and for a hand-edited card says to rerun `ecf eval agree` first (or
   revert the edit). DEV sets' operator cards (no `labelled_by`) are unchanged: they lose their
   confirmation at expect's rebuild, as today, and the operator confirms them again with `ecf eval
   label` (expect re-confirms only rows with `labelled_by`). `report._dev`, the grader comparison on
   a DEV set, uses only rows without `labelled_by` (the operator's labels) and says so. Copied cards
   keep `author: generated`, so the target folder counts as a generated set (`genset.py:19-42`) and
   keeps `labelled_by`; the gate refuses such a set anyway (J8).
6. **Same rules as the run** (P5): expect and its combinations evaluate the starter rules with the
   shipped schema; the digest goes in `work/expect.jsonl`; the report prints it beside each run's
   `rules_digest` and flags a mismatch (no refusal).
7. **Safety fields kept in ecf's figures** (Q8, R8): `_scorable`'s kappa exclusion (OD-547 J7) no
   longer drops the fixed items or `fraud_risk`, for agreed and settled values alike; the
   assignment-contradiction check stays; their kappa is still reported.
8. **Amended** (T6, U12): OD-547's "`category` and `sender_type` a majority of three, `priority`
   report-only" (now `category` majority; sender_type and priority per decision 1), J7's report-only
   rule for the fixed items and `fraud_risk` (decision 7), "agreed and settled labels count, every
   figure stating the share of each" (now: minus sender_type and priority on judged rows, the share
   per field; rule and safety per decisions 2 and 4) and its "agreed labels confirm `by: models`"
   (`SPEC.md:3579`, now `labelled_by`); SPEC's route table row for `/v1/eval/expect`
   (`SPEC.md:2067`: starter rules, `variants`, the reply below, `rules_digest`); plan decision 7 and
   decision 8 ("the operator confirms them": expect keeps provenance); the plan's Flow and commands
   (`:139-157`), Grading (`:239-243`: "all 13 are needed to compute the expected rule", now 11 plus
   decision 2's combinations on judged sets; W3), Judges bullet (`:280-291`, including `{by:
   "models", judges, on}` at :285-287), Settling (`:292-296`: label refuses any generated row with
   `labelled_by`), Safeguards J6/J7/J13 and J8 (`:302-311`: "`report.confirmed` carries the `by`" at
   :306 and "a `by: models` confirmation" at :310-311, now `labelled_by`), Report (`:326-332`), step
   4b's built list (`:527`: "`confirmed.by: models`", now `labelled_by`; its "priority report-only"
   at :530-531 and "the kappa gate (fields under 0.6 report-only)" at :542 marked "as built then;
   amended by OD-549") and step 5 (`:553-554`: agree, expect, settle, expect).

#### Design

- **agree** (`agree.py`): `UNASKED = ("sender_type", "priority")` replaces `REPORT_ONLY` (stripped
  from the card here only; settle no longer strips); `MAJORITY_FIELDS = ("category",)`; unasked
  fields never enter `disputes.jsonl` or the whole-agreement test; confirms with per-case
  `labelled_by`; rerun rule per decision 5; prints "Next: ecf eval expect".
- **expect** (`expect.py`, CLI): decision 2; skips cases with an open dispute; sends `variants` to
  `/v1/eval/expect`; writes rule and flags (all or nothing per case); appends "decides the rule"
  entries to `disputes.jsonl`; rebuilds once; one atomic labels write that re-confirms per decision
  5 and removes `confirmed` from the rows sent to settle; `labelled` accepts a card with the 11
  asked fields only when the judges' values are available (else all 13, as DEV cards). Logs
  `work/expect.jsonl`, one entry per run: `{on, rules_digest, written, to_settle, undecided, errors:
  [{id, error}]}`. Prints the next command (replacing "Confirm each with `ecf eval label`",
  `cli_evalgen.py:305`).
- **settle** (`settle.py`): asks the fields an open dispute names; a "decides the rule" entry shows
  "your answer decides which rule applies" (terminal and phone page: `export` passes `reason`);
  judges' answers only after answering; whole cases per decision 3; updates `settled` and
  `labels_sha256`; prints "Next: ecf eval expect".
- **service** (`api.py` `eval_expect`, `ruletest.outcomes`): request adds optional `variants: {case
  id: {field: [values]}}` (built: per-field value lists, the service takes every combination; X1,
  the 64 KB body cap), fields limited to `sender_type` and `priority` (others refused), values
  checked against the schema, at most 9 combinations per case, unknown ids ignored. `ruletest.Case` gains
  the card's raw labels (`load_cases` applies `stand_in` at `ruletest.py:130-131`), and `outcomes`,
  per variant, merges the variant and runs `stand_in` (with `_hand_set`) before the rules. Reply:
  `{"rules_digest": ..., "cases": [{"id", "rule", "safety", "error"?, "variants": [{"labels":
  {field: value}, "rule", "safety", "error"?}]?}]}`, each `safety` from `evalrun.expected_safety` on
  that variant's labels and actions. Evaluates `load_starter_rules(schema)` with the shipped schema.
  Writes nothing.
- **results / run / report** (`results.py` `CaseResult`, `_counts`, `compare`; `evalrun.py`
  `score_parts`, `end_to_end`, `summarize`; the loader, `evalrun.py:295-310`, adds `Case.judged`
  from `labelled_by` (`Case.author` exists, `evalrun.py:231`); `score_parts` sets `undecided` from
  `case.author == "generated"` and `exp["rule"] is None`; `rescore_synthetic` (`evalrun.py:1419`)
  reloads from the root, so it sees the same `author` and `judged`; W7; `routereport.py:286`;
  `report.py`): decisions 1, 4, 5 (contradiction check, `judged`, `_dev` on operator rows), 7;
  per-field provenance for J6/J13; lines "sender_type and priority: not scored on judged rows; n
  sent to you because they decided the rule", "rule undecided: n (errors: n)", "rules: <digest>
  (run: <digest>)".
- **labels** (`labels.py`): `labelled_by`; `source()` derived; `confirmed.by` dropped;
  `is_confirmed` gate per decision 5; `carry_over` keeps `labelled_by` only when `models_ok`;
  `confirm` refuses rows with it on a generated set; `confirm_many(root, {id: labelled_by}, on)`.
- Docstrings and help: `expect.py:1-8`, `cli_evalgen.py:245-253, 294-305`, `agree.py:1-15`,
  `labels.py:10-13`, `settle.py:1-10`.

#### Steps

1. Plan draft 21 in `planning-docs/EVAL-GENERATE-GRADE.md` (this section, the table marked
   estimates, every place decision 8 names marked amended). CHANGELOG line. Docs only. **Done**
   (6c2af38).
2. Branch A, scoring (gated): decisions 1 (scoring part), 4 and 7 (`results.py`, `evalrun.py`,
   `report.py`). It reads `labelled_by` from the row into `Case.judged` (nothing writes it until
   branch B; its tests fabricate such rows); tests: a generated-set summary and compare with
   undecided cases (`correct`, `scored` as accuracy's n, `correct_def`), `routereport`'s unsafe list
   with an undecided case, an older result file without `undecided` loads, the synthetic set's
   figures unchanged, an undecided case out of `unsafe`, `rule_scored`, per-field rule/safety and
   `gate_passed`, the kappa exemption, sender_type/priority not scored on judged rows and still
   compared on an operator-labelled DEV set. Without branch B, judged cards read `rule_undecided`
   rather than 100%. CHANGELOG line, stating that the kappa exemption reverses the Unreleased OD-547
   line for the safety fields. **Built** (5badfa3; no Fable diff review before the merge, one after
   it: AA1-AA8, all accepted, fixed on branch eval-scoring-fixes).
3. Branch B, labelling (gated): agree, expect, settle, labels, service route and
   `ruletest.outcomes`, SPEC route row; tests: the automated-vs-vendor case (ruletest-backed: rule
   differs, flags same, sent to settle and unconfirmed; after the answer, rule written, confirmed,
   not sent again); the same after an `agree` rerun (settled answers kept, nothing re-asked); a hand
   edit after a simulated crash stays unconfirmed and an agree rerun re-decides it keeping
   `settled`; settling only sender_type keeps the source models, J6 counts only `settled` fields,
   the contradiction check still runs on the case's phishing; a refused judge (whole case asks
   unasked fields in one sitting); a field with no value; a regulator variant (reaches `regulatory`
   through `fixed_rules.yaml:40`); a ruletest error (undecided, not sent); a case going undecided
   after being decided gets both flags `false`; agree re-deciding or settle changing a card's labels
   nulls its rule (never confirmed with the old rule); an entry whose fields are all settled isn't
   reopened; a card whose base reply differs from every variant (expect uses the variants); `_dev`
   ignoring judge rows on a DEV set agree ran on; the 9 bound and the route's field limit; expect
   skipping an open dispute; a DEV set with 13-field cards (not re-confirmed by expect; `ecf eval
   label` works as today); `ecf eval label` refusing a generated row with `labelled_by`, and
   `--review`/`--add`/`--import-fixed` refusing it; an out-of-schema judge value treated as no
   value; variants only from the row's judges; `is_confirmed` on a copied judged row outside a
   generated set (`test_eval_agree.py:115-125` asserting on `labelled_by`); agreement file checks
   (missing or changed judge file); a judged twin of `test_eval_grade_report.py:164-165` (expect
   keeps provenance), the existing one unchanged; `test_eval_grade_report.py:150-156` for the new
   reply. CHANGELOG line. **Built** (6d6a42e) after a Fable diff review and six Fable re-checks
   of the fixes (X1-X13, Y1-Y10, Z1-Z5, ZR1-ZR5, L1-L4); the request format changed to per-field
   value lists (X1, the service's 64 KB body cap).
4. Measure on the 57 DEV copies with a dev service (`ecf-server dev`), no model calls: fresh copies
   with the cards' labels reset to `{}` (`review.set_labels`, then `build_all`, so agree starts from
   empty cards), confirmations cleared, `work/` holding only the call and grade logs, `grades/` only
   the Large 4 and Sol files; agree, expect, settle round 1 from the operator's saved labels (a
   scratchpad script through `settle.apply`), expect, round 2 the same way, expect. Assert outcomes,
   not only counts: after the first expect, rules for 28 and 4 sent to settle; after round 1 and
   expect, 27 more and 2 sent back; after round 2, all 57 with a rule, 0 undecided, the 6 all
   `automated_notification`. Then plan draft 22 and SPEC OD-549 with the measured counts (docs
   commit). **Done** (above): every outcome as asserted; round 1 is 40 fields and round 2 is 2 (42
   in all, as the table says).

#### Measured (step 4, 2026-10-21, main `6d6a42e`)

On the 57 DEV emails (Claude Haiku 29, Sonnet 28; scratch copies of the DEV sets marked prod so
Mistral could grade them), judges `codex:gpt-6.1-sol` and `mistral:mistral-large-4-0`, a
throwaway dev service, no model calls; every settle question answered with the operator's saved
label, so the sittings are a replay of the operator's earlier answers, not fresh ones. The script
was reviewed by Fable twice before it ran and its results once after (recorded in
`state-archive/eval-settle/review-findings.md`); script, log and `result.json` in the session
scratchpad.

- agree: 32 confirmed by the judges, 25 disputed on 36 asked fields, no whole case.
- First expect: rules for 28; 4 sent to settle for sender_type alone (dev-haiku forums-001,
  social-003; dev-sonnet forums-002, social-001); 25 waiting.
- First sitting: 29 emails, 40 fields. Second expect: 55 with a rule; 2 sent back (dev-sonnet
  forums-003, social-002). Second sitting: 2 emails, 2 fields; 42 fields in all (OD-547's flow,
  computed from the same grade files, not run: 35 emails, 53 fields).
- Last expect: all 57 with a rule, all confirmed, 0 undecided, 0 rules errors; the 6 sender_type
  cases `automated_notification`.
- Derived from the grade files and the operator's labels after the run (not output by it):
  agreed asked fields match the operator 546/591 (92.4%); agreed sender_type 27/40; and the
  expected rule equals the one the operator's own 13 labels give on 50 of 57 (87.7%). The 7
  differences all come from a `category` both judges agreed on, so no one was asked: 6
  `marketing` where the operator said `notification` (4) or `travel` (1) or `notification` with
  `sender_type: company` (1), and 1 `account_security` where the operator said `notification`
  (the judges also agreed `requires_action` and `deadline_mentioned` true where the operator said
  false; the `account_security` rule reads category alone). Four of the six `marketing` cases hide mail either way (archive vs
  mark read and archive); 2 archive mail the operator's labels would leave; the
  `account_security` one flags mail the operator's labels would hide. Safety flags
  (`must_escalate`, `must_not_hide`) are identical on all 57. Accepted under OD-547 (agreed values
  stand); category disputes resolved by majority need a third judge.

Caveats: phishing and `fraud_risk` labels on these sets are assignment-based (labelled with each
email's assignment shown, `LABELS-NOTE.txt`), so the 15 settle answers for them (12 fraud_risk,
3 phishing, on 12 emails) aren't blind; 57
Claude-generated DEV emails, not the frozen set, never for scoring ecf; one judge pair, so no
majority on category; sender_type and priority are unscored by decision, their quality measured
only as 27/40 agreed sender_type; "rule undecided 0" is not "rules verified" (the 7 above).

#### Open questions

- None; all findings accepted 2026-10-21.

### Report

For each ecf result: agreement with confirmed labels and with the grader, per field and per group,
Wilson intervals, groups under 20 report-only; the grader's measured accuracy first; the generator's
agreement with the operator (its label quality); rule and safety figures only when decision 8 is
done (judged sets: undecided cases left out, the rules digest printed; amended, draft 21); the noise
reference (two C runs per set, or the S3b pair's figure stated). A DEV report shows plumbing counts
only (drops, misfits, resumes, times), no accuracy (P25).

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
  so a run can never spend more than the operator set. As set up (operator, 2026-10-20): Mistral's
  free plan with its US$10 default credit stands in for prepaid credit, with no paid plan or
  separate limit; the credit is below the `--max-cost` default, so it binds first. Unverified:
  that calls are refused rather than billed once the credit is used up, and the free plan's rate
  limits (the first grading run shows them); training turned off in the Admin Console's
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

## Estimates (draft 24: the judged PROD flow; G6)

| Item | Estimate |
|---|---|
| Generator (Claude under the waiver, `claude -p` on the operator's plan, OD-542) | seeds and emails for 300 kept: unmeasured at 300; the DEV run kept 19 of 30 in about 11 min (before the duplicate-check fix, OD-543) |
| Judge 1, Mistral Large 4 (API) | about 300 calls; about $1.05 at the measured $0.20 per 57 (OD-548); under the pay-as-you-go spending limit |
| Judge 2, Sol (Codex on Plus) | about 300 calls, about 35 min of calls, 2.6M input tokens; $0 beyond Plus; 2 to 20 Plus windows (15-160 messages per 5 h), unverified that one call is one message |
| Operator settling | about 153 emails / 210 fields in the first sitting, about 11 emails / 11 fields back once, 221 fields in all (scaled from the 57) |
| `ecf eval run --claude` on 300 | about 10-16 min dispatch plus facts prep (SPEC §20 figures for 304 cards); US$13-15 API-equivalent |
| DEV (30) | about 10-15 `claude -p` calls on ecf's Claude login |

Before draft 24 the table assumed a Codex PROD generator and blind operator labelling of all 300
(about 5-10 h); OD-547 and OD-550 replaced both.

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
2. **The `claude` adapter and a DEV run** of 30 (**run 2026-10-20**, OD-542: Haiku generated 52
   calls in 11 minutes and kept 19 of 30, 2 refused (Extortion) and 27 dropped, 20 of them for a
   shared first line (fixed, OD-543) and 7 for hygiene; Sonnet graded all 19 with no refusal or
   misfit in 74 s). **Comparison run 2026-10-20** (57 emails labelled; 11 fields blind,
   `phishing` and `fraud_risk` from a pass with the assignment shown, recorded as assignment-based
   truth, DEV only, after the blind pass showed the operator had read `phishing` narrowly: 0 of 27
   scams; graders then read the raw email with authentication results): all 13 fields, Haiku-made
   set Opus 86.7% [82.9, 89.8], Sonnet 84.4% [80.3, 87.7]; Sonnet-made set Opus 84.9% [80.8, 88.2],
   Haiku 83.8% [79.7, 87.2]; intervals overlap, no grader separates; category, sender_type and
   priority are the weak fields for every grader (54-68%). **DEV grader comparison** (operator, 2026-10-20; OD-543): Haiku, Sonnet and
   Opus each grade, a model never grading its own set: a Haiku-made set graded by Sonnet and
   Opus, and a Sonnet-made set graded by Haiku and Opus, both labelled blind by the operator
   first; the DEV report shows each grader against those labels, marked a grader comparison, not
   a measurement of ecf. Haiku and Sonnet never grade the same emails, so their figures are
   indicative; Opus on both sets is the reference. after the operator writes the realism rubric and
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
   after); grader on those 40; operator seconds per card. The rating page shows the email (the
   mail-client view), its intended group (RUBRIC.md), the `phishing` definition and the signs box
   (OD-544; recorded in RUBRIC.md under its change rule). **Ran 2026-10-20**: seeds by Terra (35
   seeds, 15 groups, no refusal); Sol 20/20 kept, 0 refused, 0 dropped, 2 passed the hygiene retry;
   Luna 20/20, 1 refused (Extortion), 2 dropped; 17 of 20 kept seeds in common. The operator skipped
   the rating (operator decision 2026-10-20): **Sol is adopted, Luna is not**, and the rubric was
   not applied. **Shared seeds** (OD-545): a seed run by a model that is neither contestant writes
   the 20 seeds (`ecf eval generate --seeds-only --generator codex:gpt-5.6-terra --count 20 --root
   <seeds>`), then one email run per contestant from them (`--seeds-from <seeds> --generator
   codex:gpt-6.1-sol`, and `codex:gpt-6-luna`, the same `--count`, mix and `--k`): no seed call and
   no top-up, so both draw on the same pool in the same order with the same tries; a group the seed
   run got no seeds for is unmeasured for both and listed, never a contestant's refusal; the
   source's mode, count, mix and seed-log digest are checked before copying; every manifest records
   its seed-log digest, and an email run records its seed run (name, writer, digest) and the seeds
   it kept. The grader is never the seed writer either. The rating page mixes both sets' 40 emails
   under random tokens (the mail-client view, the group as text), pairs not adjacent; same-seed
   pairs may be recognisable, a stated limit. The fixed prompt words (PURPOSE, RECIPIENT,
   SAFETY_RULES in `src/ecf/eval/generate.py`) were approved as written by the operator on
   2026-10-20 (decision 10b); their digests are in every manifest. Stage B's fixed words (the new
   SAFETY_RULES sentence and the retry texts RETRY, RETRY_NUMBERS, RETRY_SSN, RETRY_LOCAL) were
   approved as written on 2026-10-24 (OD-554); their digests are in the manifest too. 4b. **Judges** (OD-547; build
   order from Phase R): (1) this draft and ADR 0036; (2) `ecf eval agree` built and tried offline on
   the DEV grade files; (3) the shared API-judge route with Mistral, then Gemini (one key check and
   one grading call each as the canary, operator's go-ahead; Gemini dropped, OD-550); (4) the settle phone page and
   importer; (5) the judges measured on the 57 DEV emails the operator labelled; then step 5 with
   judges. **(2) built 2026-10-20**: `ecf eval agree` (`ecf.eval.agree`), label provenance
   (`confirmed.by: models`, as built then; amended, draft 21; counted only on a generated set;
   `labels.by_source`; atomic `labels.write`; `confirm_many`), the report's label-source and
   pairwise judge agreement and kappa lines, `ecf eval label` refusing disputed emails. Offline
   trial on cleared copies of the two DEV sets, their two Claude graders as judges, priority
   report-only (as built then; amended, draft 21): 30 of 57 emails fully agreed, 27 had at least one
   disputed field (sender_type 12, category 6, requires_reply 6, requires_action 4, fraud_risk 3,
   deadline_mentioned 2, bank_detail_change 2); where the judges agreed they matched the operator's
   labels on 581 of 649 fields (89.5%): safety fields 95-100% (phishing 56/57, bank_detail_change
   54/55, fraud_risk 52/54, regulator_claim and claims_internal 57/57), sender_type 28/45 and
   category 33/51: the judges agree with each other and differ from the operator there, the case the
   kappa rule (J7) is for. Both judges were Claude; the PROD judges are other families. **Built next
   (2026-10-20)**: Claude as a PROD generator with `--claude-generator` (the waiver recorded in the
   manifest; the report says WAIVER, not NOT A MEASUREMENT, when only the generator shares the
   classifier's family; a PROD Claude grader still refused); judges' family checks against each
   other and every writer; the report's grader figures only against operator-settled labels when
   judges set some, the kappa gate (fields under 0.6 report-only; as built then, amended, draft 21),
   judge labels contradicting the assignment left out for phishing and fraud_risk, ecf's figures
   three ways; `ecf eval settle` (terminal, `--export` for a phone page with no case ids or judge
   names, `--from` importing only fully answered emails; one rebuild, one atomic confirm); per-judge
   call logs, `--pace`, a 60 s Mistral call timeout, the service checking a set once per run;
   provider errors recorded by type and code only. Still open: comparisons computed twice (J12, `ecf
   eval compare`), the shared API-judge route and Gemini (3, needs the key), measuring the judges
   (5). **As of draft 23:** (3) needs no shared route with one API judge (Gemini dropped, OD-550);
   (5) done: Mistral models measured (OD-548) and Sol on the same 57 (2026-10-21). **Mistral on the 57 DEV emails (2026-10-20, operator's go-ahead)**: refused with HTTP 403 on
   the first grading call of each set (the key's model-list check passes); cause unverified (the
   free plan and the preview model, an account step, or data sharing); the operator checks the
   Mistral console. Resolved 2026-10-21: the free plan doesn't activate API keys; pay-as-you-go
   with a spending limit (OD-548).
5. **The first PROD set**: 300 kept; grade; blind labels; expect; two C runs; report. **As
   amended (OD-547):** Claude generates 300 (waiver); Mistral and Gemini judge; `agree`; the
   operator settles disputes; expect; two C runs; report. **As amended (OD-550):** Mistral Large 4
   and Sol judge (no Gemini). **The first PROD run, as one sequence (draft 24, G10)**, each model
   step with the operator's go-ahead: `ecf eval generate --root <set> --generator claude:<model>
   --claude-generator` (300 kept, Claude's own seeds; the operator-only route, OD-542, OD-547;
   which Claude model generates is the operator's choice before the run, not yet made);
   `ecf eval grade --root <set> --grader mistral:mistral-large-4-0`; `ecf eval grade --root <set>
   --grader codex:gpt-6.1-sol` (expect it to stop at the Plus limit and finish with `--resume` over several
   sessions); `ecf eval agree`; `ecf eval expect`; `ecf eval settle` (about 150 emails, mostly
   `category` and `fraud_risk`; the phone page or the terminal); `ecf eval expect` again (and settle
   once more when it says so); two `ecf eval run --claude` runs; `ecf eval report`. Before it the
   operator knows: the settling load above; Sol spanning several Plus windows; the Mistral
   spending limit covering about $1-2; that the DEV phishing and `fraud_risk` figures were
   assignment-based and may not carry over to a PROD set; that any refusal sends the whole email
   to the operator. **As amended (draft 21):** `agree`,
   `expect`, settle, `expect` (settle and expect again when expect says so); two C runs; report.
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
- **Gemini subscriptions** (not pursued, OD-550; 2026-10-20, research agent, Google's pages, (rv)): Google AI Plus
  ($4.99 or $9.99 by two Google pages), AI Pro ($19.99) and AI Ultra include no API access or
  credit; "Starting June 18, 2026" Gemini CLI and Code Assist stopped serving individual, Pro and
  Ultra sign-ins (developers.google.com Gemini Code Assist privacy notice, updated 2026-10-09;
  github.com/google-gemini/gemini-cli README); the Gemini API's paid services don't use prompts to
  improve products, unpaid ones do with human review (ai.google.dev/gemini-api/terms); prepay
  minimum US$5, credits expire after 12 months, keys stop at US$0 (ai.google.dev/gemini-api/docs/
  billing).
- **Judge vendors** (2026-10-20, a read-only research agent fetched the vendors' own pages,
  (rv)): **Google Gemini** API: `gemini-3.8-flash` (stable; flagship `gemini-4-argon` unpriced on
  the pricing page), strict JSON via `response_format` with a schema (length limits ignored;
  "always validate"), US$0.75/$3.75 per 1M tokens to 2026-12-31 then $1.50/$7.50 (~US$1 per 400
  calls); free tier trains on data with human review, paid tier (prepaid, min US$5) doesn't train
  and keeps logs "for a limited period" (unstated); experimental project spend caps; usage policy
  bars content facilitating phishing or fraud, labelling not addressed (ai.google.dev pages,
  policies.google.com use policy). **Cohere** Command A: schema output, no published price for
  current models, call caps possibly 1,000/month, training default unstated, no hard cap.
  **Amazon Nova** (Bedrock): strongest schema output, needs AWS (deferred to M1), prices
  unverified. **AI21 Jamba**: no schema mode. Recommendation: Gemini, paid tier only (not pursued:
  OD-550, no new platform).
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
  these runs; the same holds for Sol's judge runs (draft 24, G9).
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

Phase R on draft 19, the judges (2026-10-20): four Fable reviewers (safety, measurement, cost and
design, performance), merged to J1-J20, key claims spot-checked (the DEV grader pair's 11% field
disagreement measured); all accepted by the operator 2026-10-20 (design above; full list in
`state-archive/eval-judges/review-findings.md`).

Phase R on draft 1 (2026-10-19): 6 Fable reviewers, about 85 findings merged to P1-P31, key claims
spot-checked against the code; all decided by the operator 2026-10-19 (summary in Decisions, the
design above; full list in `state-archive/eval-generate/review-findings.md`).
