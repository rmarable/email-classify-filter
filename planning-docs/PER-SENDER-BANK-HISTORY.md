# Per-sender bank-account history (draft 1)

Status: draft 1, approved by the operator 2026-10-27, **PARKED 2026-10-27** (operator). Nothing built; no Phase R yet; OD-564 proposed, not assigned (re-check the next free OD when resumed). Code references checked against main 188a1e8. Resume at Step 0 (corpus count; needs the operator's go-ahead and a mailbox) and Step 1 (Phase R).

## Context

SPEC §1.2 "Later" lists per-sender bank-account history, and SPEC lines 71-77 sketch it. This draft follows that sketch.

The gap: fraud trigger 1 (`triggers._money_triggers`, triggers.py:565-599) fires on bank details only when the sender is unconfirmed, the mail uses change wording, or there's a Reply-To mismatch. Take a **confirmed** vendor whose mailbox is compromised, sending a new checksum-valid IBAN with none of those. Today nothing deterministic catches it; only the model's `fraud_risk` might.

Goal: a fraud reason when a confirmed sender sends an IBAN it hasn't used before, while its history already holds one.

Operator decisions (2026-10-27):
- IBANs only.
- A genuine change is accepted with a step-up command.
- History stays out of export bundles and is relearned after a restore.
- Count on the corpus first.

New OD = OD-564 (re-check it is still free at the time). This changes rules and facts, so **Phase R (4 Fable: safety, measurement, cost+design, performance; Opus if Fable is out of credits, OD-562)** reviews this plan before any build.

## Design

**Detection.** `triggers._iban` (triggers.py:280) stops at the first valid IBAN. It changes to also collect every valid normalised code it checks; the bounds stay `IBAN_CANDIDATES` and `IBAN_ATTEMPTS`. `scan` returns these codes in a separate transient value, never in `keywords`, because `keywords` is stored in item facts. The rule from triggers.py:72 still holds: the number never goes into facts, logs or audit.

**Keyed hash.** The hash is HMAC-SHA256 under a per-install key.
- The key is a new secret-store name, `sender-account-key`, added to the name pattern in secretstore/__init__.py:17. Only the service writes it, and it creates the key the first time it needs one.
- Before a code is hashed, it is upper-cased, with spaces and hyphens removed.
- An unkeyed hash won't do: an IBAN is short and structured enough to guess against one. Today's sender hashes (`facts.sender_hash`) are unkeyed.

**Storage.** Migration `0043`, a new table:

`sender_accounts(address_id, sender_hash, account_hmac, first_seen_at, last_seen_at, PRIMARY KEY(address_id, sender_hash, account_hmac))`

- At most `ACCOUNTS_PER_SENDER = 5` per sender; when full, the oldest by `last_seen_at` is dropped.
- It is a new table, not a column on `senders`. It stays out of `importer.COPY` (importer.py:59) and the export table list (export_bundle.py:62), so no data-format bump is needed.
- A short-lived `item_accounts(item_id, account_hmac, expires_at)` holds the hashes of an escalated item, for the accept command below. It is deleted when the item is resolved or the entry expires.

**Fact and reason.** `facts.compute` (facts.py:139) reads the sender's account history.
- New facts:
  - `bank_account_new`: the message has a valid IBAN not in the history, and the history has at least one entry.
  - `bank_account_history`: the history has at least one entry.
- Trigger 1 gets a fourth clause in its `held` list (triggers.py:574): "an account this sender hasn't used before".
- The reason keeps the "bank details with " wording, so it is in `MONEY_REASONS`. That way `with_money` (triggers.py:805) recomputes it in the eval with no new code path. It also counts as a fraud signal for routing, hide blocking, the pre-check and the no-send guardrail, like trigger 1 today.
- It fires only for confirmed senders, since unconfirmed senders already fire trigger 1. It never fires on `self_sent` mail.

**Learning.** The history learns in `analysis.record` (analysis.py:99-129), under the same gate as `record_sender`:
- the message passed DMARC;
- it has no fraud reason, no lookalike domain and no weak route reason;
- it isn't `backfill`;
- and, new, the sender is confirmed.

So a vendor's first IBAN after confirmation is learned, a known IBAN refreshes `last_seen_at`, and a new IBAN fires the reason and is not learned.

**Accepting a genuine change.** `ecf sender accept-account <item>` uses step-up, following the pattern of `senders.confirm` (senders.py:171) with a new purpose `sender_accept_account`. It moves the item's `item_accounts` hashes into `sender_accounts` and audits `sender.account_accepted`, with no value.
- The Slack card for this reason tells you the command exists.
- MCP gets no tool for it, since MCP has no approval or admin tools.
- `ecf sender show` prints the number of known accounts, never the hashes.

**Restore or move.** History is not exported. After a restore, the history is empty and rebuilds as vendors write. The stated limit: until a vendor's first IBAN after the restore is learned, no check runs on it. SPEC §12.2 and the operator guide say so.

**Eval.** The card builder gets a new expected fact, `sender_accounts: [IBAN, …]`, which seeds history for a card. Its IBANs must be published examples (hygiene rule).
- The eval scratch uses a fixed test key.
- `ruletest.with_expected` lays the history over before `with_money`.
- `facts_version` goes up to 3.
- New cards:
  - a confirmed vendor with a new IBAN, no change wording, passing DMARC (scam, rule 1);
  - a confirmed vendor with its known IBAN (benign);
  - a confirmed vendor's first IBAN (benign, learned);
  - a vendor with two IBANs, one known and one new (scam).
- Each card gets a blind Opus check before the operator labels it (blind-check memory).

## Steps

0. **Corpus count (real-service; go-ahead and mailbox from the operator).** Per sender, in the isolate child, the corpus session keeps transient HMACs under a throwaway key. It reports counts only:
   - senders with a valid IBAN;
   - IBANs per sender;
   - senders whose IBAN set changed, and over what span.

   The code is a throwaway in the scratchpad. Results go in SPEC. If changes turn out common among genuine senders, the operator decides, before the build, whether the reason should escalate or only flag (rule 1b).
1. Plan as a roadmap-style doc `planning-docs/PER-SENDER-BANK-HISTORY.md` (with the operator's OK). Then Phase R, then the operator decides each finding, then the next draft.
2. Build on branch `sender-bank-history` in `../ecf-sender-bank`, off `origin/main`, tests first:
   - `_iban` collecting codes, and `scan`'s transient output;
   - the key in the secret store;
   - migration 0043, plus the release.json schema number and digest;
   - `facts.compute` facts and the trigger 1 clause;
   - `analysis.record` learning;
   - `item_accounts` and the accept command (CLI, API route, step-up purpose);
   - the card text, and `ecf sender show`;
   - eval card field, `with_expected`, `facts_version` 3;
   - SPEC §8.5 paragraph, OD-564 row, §12.2 limit, §1.2 Later entry removed;
   - CHANGELOG line, operator guide (user-facing style).
3. Eval cards (blind check, then the operator labels and confirms). Run `ecf rules test` / `eval expect` on the kept sets to confirm no other outcome moves.
4. Diff review (2 agents) only if the build departs from the plan Phase R reviewed. Then the full macOS gate with Colima (0 skipped), commit, merge and push with the operator's OK, and watch CI.

## Files

- `src/ecf_server/triggers.py`: `_iban`, `scan`, the trigger 1 clause
- `src/ecf_server/facts.py`: `compute`, a new `record_accounts`
- `src/ecf_server/analysis.py`: `record`
- `src/ecf_server/senders.py`: `accept_account`, `show`
- `src/ecf_server/secretstore/__init__.py`
- `src/ecf_server/migrations/0043_sender_accounts.sql`
- `src/ecf_server/ruletest.py`, plus the eval builder and card spec
- CLI and API route files for `ecf sender`
- `SPEC.md`, `CHANGELOG.md`, `docs/operator-guide.md`

## Verification

- Unit tests that fail on main:
  - a new IBAN from a confirmed sender with history fires the reason;
  - a known IBAN doesn't;
  - an empty history learns the first IBAN;
  - an unconfirmed sender behaves exactly as today;
  - a fraud-flagged message doesn't learn;
  - the bound of 5;
  - the accept command needs step-up and moves only that item's hashes;
  - the number appears in no facts, audit or log (grep the test DB and log);
  - a backfill doesn't learn.
- Perf test: the IBAN code collection keeps the existing worst-case bounds (`tests/test_perf.py`).
- Eval: the new cards hit their expected rules. `ecf rules test` on the 394-card set shows no outcome change except the new cards.
- Full suite on macOS with 0 skipped before the merge, then CI green.
