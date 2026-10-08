# ADR 0022: real-mail test corpus

- **Status:** accepted (2026-10-07; OD-466 to OD-468); not built yet
- **Context source:** SPEC §12.4, §16.7, §9.6, §14.3, §17.3; `planning-docs/GENERATE-EMAIL-CORPUS-PLAN.md`
  (draft 7, after review rounds R to R6, findings R1-R209)

## Context

Evaluation used only the synthetic set (185 cards). The decision-model experiment needs real mail,
labelled by the operator, to compare classifiers. SPEC forbade it in three places: full messages are
never written to disk (§12.4, OD-108/109), test mailboxes receive synthetic mail only (§17.3), and
end-to-end runs use synthetic mail only (§16.6).

## Decision

- **Storage:** a corpus of real messages may exist on disk only as one age-encrypted file, with a
  passphrase the service generates by default. It is written outside the data folder and outside
  any git work tree, never overwriting a file.
- **Who and where:** only from a mailbox the operator owns. ecf can't check that; it is the
  operator's condition, as OD-427 is for watching another person's account. A prod or test install
  may make one, after step-up and with a Security Notice. `ecf-server dev`, whose step-up is a
  fake, may not.
- **Destinations:** none added. The corpus is a new place content is stored, and the operator reads
  its excerpts and headers while labelling. In v1 a corpus never goes to Anthropic: `--corpus` with
  `--claude`, and `/ecf-eval` on a corpus, are refused.
- **Isolation of untrusted mail:** every message is parsed, checked and excerpted in the OD-204
  child at fetch time, then analysed as live fetch does. Evaluation and labelling read only the
  stored manifest and never parse.
- **Separation from the gate:** a corpus run writes only its result file, never an `eval_runs`
  row, with `gate_passed` always false and its own set version. The go-live gate and the synthetic
  check never see it.
- **What it measures:** classifier field accuracy, calibration and real-mail noise, reported and
  never gated. Expected values come from the same policy and stored facts as the run, so rule and
  safety scores on a corpus test only the classifier's fields.
- **Replay:** only from `ecf-server dev` to an IPv4 loopback Dovecot, with its mail home on tmpfs
  and the dev data folder on a RAM disk. Replay is outside the `v1.0.0` gate.
- **Gating:** the corpus gates `v1.0.0` only through the decision-model experiment's recorded
  decision.

## Alternatives considered

- **Corpus in plaintext on an encrypted disk (FileVault only):** rejected. The file would leave the
  machine in any backup or copy, readable.
- **Any mailbox the install watches:** rejected for v1. Another person's agreement under OD-427
  covers live processing, not a stored copy read by the operator.
- **Corpus runs recorded in `eval_runs` with a `kind` column:** rejected as more change than
  needed. Writing only a result file keeps every gate reader unchanged.
- **Preset B/C runs on a corpus:** deferred. A corpus from a preset-A mailbox would send that mail
  to Anthropic, which its owner never agreed to.
- **Replay into a test install connected to Slack:** rejected. Excerpts would land in a database
  and a Slack workspace outside the encrypted file.

## Consequences

- New stated limits (§12.2):
  - the owner condition is unchecked;
  - memory is a few times the corpus size;
  - the design relies on encrypted swap and on core dumps being off;
  - Python strings holding secrets can't be wiped;
  - terminal scrollback may keep excerpts;
  - an ssh forward is a residual for replay;
  - the audit pseudonym for a one-off source can be reversed.
- The decision-model comparison has a real-mail harness. Its absolute safety gates stay on the
  synthetic set.
- Building it touches the mail layer (append dates), the download budget (a one-off source table),
  the eval runner (case source, rescore) and the dev service (refusals, a CA option).
