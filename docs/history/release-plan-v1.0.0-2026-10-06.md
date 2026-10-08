# Release plan: `v1.0.0` (2026-10-06)

An execution plan for SPEC §1.5's release criteria, not a design plan: `docs/CURRENT-DESIGN-PLAN.md`
still points at the design plan. This file is a dated snapshot and isn't edited; SPEC records
what was built and decided.

## Where things stand (2026-10-06)

| §1.5 criterion | Status |
|---|---|
| 1 Milestones tagged | Met: `ms-v1.0-foundations` … `ms-v1.6-gmail`. |
| 2 Real-service tests | Met for v1, except Undo on a real mailbox, which needs the Slack run below. |
| 3 Eval safety gates | Preset A passed (run `e04fac92`: 0 unsafe, fraud-guard recall 67/67). C `standard` passed (`f1dd209a`). C `high` is running (`9e745b99`). B `standard` and B `high` are still to run. |
| 4 Documentation | Written (README, SECURITY.md, CONTRIBUTING, `THIRD_PARTY_NOTICES`, guides). |
| 5 No critical findings | The `rfc822` hash item goes into SPEC as a stated deferral; the Gmail Spam folder is a stated limit. |
| 6 CI, licenses, reproducible and hash-verified artifacts | Built: reproducible builds (Linux CI and macOS byte-identical), `release.yml`, `ecf upgrade` hash checks (OD-458). It's proven by a real release candidate. |
| 7 Operator sign-off | Last step. |

Decisions so far: OD-458 (GitHub Releases only), OD-459 (security reports), OD-460 (literal
fraud-guard recall), and D1-D9 of the session's plan; D1, D5, D6 and D9 still need ODs (step 6).

## Steps

1. **Finish C `high`** (`9e745b99`). Record it in SPEC §16.2: accuracy, per-field figures, cost by
   call source.

2. **Deploy the pending fixes.** Commit, push and CI, then reinstall the shadow service:
   - held submissions no longer block the next agent type (`claude_eval.eval_next`);
   - `promptSuggestionEnabled` and `awaySummaryEnabled` set to false in `ecf claude` sessions.

   Check on the next run that `prompt_suggestion` calls drop to zero.

3. **Decide the `classifier_high` pin**, with your decision.
   - Compare the Haiku classifier (C `standard`, `f1dd209a`) with the Sonnet classifier (C `high`,
     `9e745b99`) on the same 184 cases: category, fraud risk, priority, payment, the classifier's
     share of cost, and the paired comparison (`ecf eval compare`, exact McNemar per field with Holm).
   - **Keep Sonnet** if it's clearly better on fraud risk or category (a significant paired
     difference). Otherwise **pin `classifier_high` to Haiku** (`data/models.lock`, SPEC §7.5, a new
     OD). Opus stays on `actor_high` either way.
   - If the pin changes, the C pin key changes, so C `high` (and C `standard`, whose key also binds
     `classifier_high`) re-run on the new pins: two more paid runs.

4. **Preset B.**
   - One preset A run on the shadow install, about 45 minutes, local only. B uses Gemma's
     classifications from a complete A run on the same install and set version.
   - Then B `standard` and B `high`, `--fraud-only`, by `/ecf-eval`.
   - Record each in SPEC §16.2.

5. **The Slack real-service run** (decision D6), with your go-ahead. Shadow install, test Slack
   workspace, Gmail and Purelymail test mailboxes. Cover:
   - Undo of an archive and of a junk move on real Gmail;
   - Undo of a draft;
   - the run's teardown of everything it created.

   Results go in SPEC §18 and §21.1.

6. **Record the remaining decisions** as ODs (next free: OD-461):
   - D1: §1.5 items 4-6 accepted as written;
   - D5: the `rfc822` deferral, into §21.3 and §12.2;
   - D6;
   - D9: a release candidate before `v1.0.0`;
   - the `classifier_high` outcome.

   Update SPEC §1.5 with each criterion's evidence.

7. **Release candidate.**
   - Bump to `1.0.0rc1` (`src/ecf/__init__.py`, `release.json`) and set the CHANGELOG heading
     `v1.0.0-rc1`.
   - With your approval of the tag and the push: tag `v1.0.0-rc1`. `release.yml` publishes a
     prerelease.
   - `ecf --install shadow upgrade --to v1.0.0-rc1` checks the real download path: `gh` under
     launchd, the manifest and sums, and `uv tool install` from the kept wheel.

8. **Release.**
   - Fix anything step 7 finds.
   - Bump to `1.0.0` and tag `v1.0.0`, with your approval.
   - Merge `v1.0.0-release` into `main` after the macOS gate (0 skipped).
   - Dependabot starts once its config reaches `main`.
   - Your sign-off is recorded in SPEC §1.5.

## What needs you

- Steps 1, 3 and 4: `/ecf-eval` in `ecf claude` for each Claude run.
- Step 3: the pin decision.
- Step 5: about an hour, plus the go-ahead.
- Steps 7-8: the tag approvals and the sign-off.
