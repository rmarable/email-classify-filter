# ARC for forwarded and list mail (draft 1)

Status: draft 1, approved by the operator 2026-10-28, **FROZEN 2026-10-28** (operator). Nothing built; no Phase R yet; an OD to be assigned when resumed (OD-576 is the next free number today). Code references checked against main 3464d97. Resume at Step 0: a script the operator runs in their own Terminal (the corpus passphrase is theirs; it is read with a hidden prompt), which prints only big public sealers by name, others as `other-N`, and writes the full names to a 0600 file on this Mac that Claude doesn't read (privacy: sealer names are metadata of real mail). Needs the corpus path and the operator's go-ahead.

## Context

Forwarded and mailing-list mail often breaks the sender's DKIM signature, and ecf then records
`auth_result = none` (SPEC §7.3 Limits, `SPEC.md:857`; ADR 0011). That mail can't build sender
history, gets "unauthenticated known sender" noise, and payment mail on it lands in rule 1a. ARC
(RFC 8617, Experimental, 2019; datatracker read 2026-10-28) lets each server a message passes
through seal what it saw on arrival. The seal is signed, so an outsider can't forge it, unlike the
provider Authentication-Results headers ecf refuses to trust (OD-051). RFC 8617 is clear about
what ARC doesn't do: "ARC is not a trust framework". It proves who sealed a message, not that the
sealer is honest, and a passing chain "is also not an indication of message safety".

Operator decisions 2026-10-28:
- the first version **shows and measures only**: a separate fact, `auth_result` unchanged, no
  rule, policy or history change;
- the trusted-sealer list is **decided from Step 0's counts**;
- Step 0 reads the **corpus `.eml` files**.

**Safety point behind "show only":** a trusted sealer's claim says the sender check passed *when
the sealer received the message*. A later hop could change the body (an IBAN, say) and re-seal,
and the chain still validates. So ARC never proves that the text you see is the sender's.

## Facts checked (2026-10-28)

- dkimpy 1.1.8 (installed) has `dkim.arc_verify(message, logger, dnsfunc, minkey, timeout)`
  (`.venv/.../dkim/__init__.py:1437`). It returns `(cv, per-instance dicts, reason)`. Each dict
  carries `as-domain`, `ams-domain`, `as-valid`, `ams-valid`, `cv` and `aar-value`. It ignores
  `timeout` (it fixes 5). `ARC.verify` returns `None` when a seal reported `cv=fail`.
- ARC signing in dkimpy needs the `authres` package, which isn't installed (`__init__.py:1042-1047`).
  Tests would need it as a dev dependency, through the licence allow-list.
- Google's "ARC email authentication" page (knowledge.workspace.google.com, updated 2026-10-26)
  doesn't say whether Gmail seals or evaluates inbound mail. Whether Gmail seals every inbound
  message (which would also help SPF-only senders and late verification) is **unverified**. Step 0
  answers it.
- Microsoft 365 documents an override for trusted sealers that an admin configures
  (learn.microsoft.com, "Configure trusted ARC sealers"). That's background only.

## Step 0: measure (read-only; real mail; needs the operator's go-ahead when run)

The script stays in the session scratchpad, never the repo. It writes numbers only, no message
text, addresses or subjects.

- Open the corpus through `ecf_server.corpus.Corpus.message()` (`corpus.py:690`). First check how
  it decrypts (Keychain access from a script may need the service). If it can't run outside the
  service, stop and ask.
- For each message, recompute `senderauth.evaluate` (real DNS, today's keys) and
  `dkim.arc_verify` with the same cache-backed `dnsfunc`.
- Counts:
  - messages with ARC headers;
  - `cv` pass, fail and none;
  - sealer domains by instance, and whether `google.com` (or the provider) seals the last
    instance on all inbound mail;
  - among today's `auth_result = none`: how many have a valid chain whose AAR claims
    `dmarc=pass` or `dkim=pass` for the From domain, broken down by sealer;
  - the share that are bulk/list mail, payment-related, or have `trigger 6` (org domain) hits;
  - chain lengths and the DNS time per message, against the 30 s budget.
- **SPF at receipt** (operator 2026-10-28): for SPF-only senders, which are `none` today because
  SPF can't be checked after delivery (SPEC §7.3), count how often a sealer's AAR records an
  `spf=` result for the From domain, and its value. Count only; nothing is built on it.
- **Trigger 6 on list mail** (operator 2026-10-28; OD-451 never measured it): count trigger-6
  (`from_org_domain` and not pass) hits on corpus mail with `List-Id`/`List-Unsubscribe`, and how
  many of those a valid chain would rescue. Count only.
- **Latency** (operator 2026-10-28): the extra time ARC adds per message, measured separately
  for DNS and crypto. Report the median, p95 and maximum, cold and warm cache, on the messages
  ARC would run on (sealed and not passing), and how often the budget runs out.
  - Estimate (unmeasured; based on today's DNS timeouts in `dnscache.py:26-32`, not on any
    measurement): tens of ms typical, at most about 1.5-3 s when a sealer's DNS doesn't answer.
  - The figures go to the operator with the rest of Step 0. Claude sets no latency threshold.
- Limit to state in the report: keys rotated since delivery make old seals fail today, so the
  counts are a lower bound.
- The report goes in `state-archive/arc/step0-report.md`. A result that bears on the design or
  Gmail facts goes in SPEC §18 and §21.2.

**Does ARC make a difference? What Step 0 reports:**
- **Reach:** of today's `auth_result = none` corpus mail, the share with a valid chain whose AAR
  claims `dkim=pass` or `dmarc=pass` for the From domain ("rescuable"), and the count per month.
- **Concentration:** how many sealer domains cover 90% of the rescuable mail, which sets the size
  of the trust list.
- **Effect if later given limited weight** (a what-if on the stored facts, not built):
  - how many `UNAUTHENTICATED_KNOWN` route hits would go away;
  - how many senders would reach `sender_seen_before`;
  - how many trigger-6 (org-domain) hits on list mail would go away.
  Payment-related rescuable mail is counted but gets no credit, because the body can change
  after sealing.
- **Warning value:** chains that are valid but claim a *fail* for the From domain, or whose inner
  signature breaks after a trusted seal. These are mail where ARC would add a warning line rather
  than reassurance.
- **Unknown:** seals failing on missing or rotated keys are counted apart, because old corpus mail
  undercounts.
- **Go / no-go** (operator decision 2026-10-28): no bar is fixed beforehand. The operator decides
  from the Step 0 numbers, and the record says plainly that the bar was chosen after seeing them.
  The report states every count as measured, with no thresholds or targets set by Claude; any
  estimate is marked unmeasured. On "no-go", the result goes in SPEC §21.2 and ARC stays Later,
  with nothing built.

**Then:** the operator picks the trusted-sealer list (shipped defaults, your own list in
`ecf config`, or both), and draft 2 is written.

## Design (draft 1; changes after Step 0 come in draft 2)

- **Where it runs: before classification.** The ARC check runs at fetch, in the same step as
  today's DKIM/DMARC check (`senderauth.evaluate`, called from `analysis.py`). That step runs in
  the isolated child, before the classifier and before any model sees the message.
- **No model sees the result.** The classifier and Claude-review code and `classifier.md` don't
  read `auth_result` (grep, 2026-10-28). The ARC result goes only into the stored facts and the
  card's "Sender check" line. Prompts, the gate key and eval results are unchanged.
- **Its time comes before classification.** Expected to be small next to the classifier's
  (Opus about 5 s per message, measured in the OD-561 runs; the ARC figure is an unmeasured
  estimate until Step 0).

- **`senderauth.evaluate`** (`src/ecf_server/senderauth.py:104`): when the message has an
  `ARC-Seal` and `result != pass`, run `arc_verify`.
  - It uses the same `DnsCache` (`dnscache.py`; budget, negative caching).
  - It prefetches every instance's AS and AMS keys (`dnscache.prefetch`).
  - ecf's own cap on instances is below RFC 8617's 50, to protect the budget. The value is set in
    draft 2 from Step 0's chain lengths (the placeholder of 10 isn't measured). Over the
    cap, or with the budget used up, the result is `none`.
  - A `None` return from dkimpy (an earlier hop said `cv=fail`) is recorded as `fail`.
- **New `AuthOutcome.arc`** (:63): `cv`, `instances`, `sealers` (instance → AS domain), the AAR
  claims for the From domain (dkim/dmarc result only, parsed from `aar-value`), and
  `trusted_sealer` (null until the list exists). `facts()` (:71) writes it under `auth.arc`.
  - `isolate.py:150-158` rebuilds `AuthOutcome` from JSON field by field, so the field is added
    there too.
- **AAR parsing:** a small parser for `dkim=`, `dmarc=`, `header.from=` and `header.d=`, tested on
  fixtures. Adding `authres` as a runtime dependency is the alternative, decided at build (licence
  check).
- **Card:** `cards.py:236-240` `sender_check` (also `inbox.py:168`) adds a second line only when a
  valid chain claims a pass for the From domain, for example: "Forwarded by lists.vendor-a.example;
  when it arrived there, the sender check passed for vendor-a.example. The text may have changed
  after that." Fixed wording, sealer domain defanged as on cards today.
- **Not changed:**
  - `auth_result`, the closed list `rules.FACTS["auth_result"]` (`rules.py:72`), and every rule,
    trigger and policy reader of it (facts.py, triggers.py, own_mail.py, policy.py);
  - sender history (`facts.record_sender`).
  - The new fact isn't added to `rules.FACTS`, so no rule can read it. `FACTS_VERSION` stays 3,
    because the eval's Scratch has no DNS (`ruletest.py:102-104`) and rules don't read the fact.
    Phase R checks this reasoning.
- **No migration** (facts are JSON in `items.facts`).
  - At build, check that an older ecf reading an export bundle ignores the unknown `auth.arc` key.
    If it doesn't, a `data_format` bump is needed (§1.4).
- **No new destination:** ARC adds DNS lookups for sealer selectors, the same kind as today's key
  lookups. No mail content leaves the computer. Prompts and the gate key are unchanged.

## Later (not this plan; each needs its own OD and review)

- Using a trusted ARC result with limited weight:
  - counting it toward sender history;
  - silencing `UNAUTHENTICATED_KNOWN`;
  - never for payment or fraud rules, hiding, drafts or `reply_template`, because the body can
    change after sealing.
- Trusted-sealer configuration in `ecf config`.

## Related, separate plan (operator 2026-10-28)

- **Attachment text extraction (sandboxed)**, so the payment and bank scans see PDF invoices. It
  gets its own plan after this one, not part of ARC. Recorded as Later in the design plan
  (`docs/history/design-plan-2026-09-27.md:1126`); "PDF parsing" was out of scope in v2.1.0
  (`planning-docs/PRE-CLASSIFIER-IMPROVEMENTS.md` §7). Nothing about its size or benefit is
  measured yet.
- Noted, not planned: DoH (OD-050). Domain age (RDAP) is left out, because it would add a
  destination, which the privacy rule forbids without an operator decision.

## Review and records

- **Phase R before any build** (it changes facts; AGENTS hard constraint): 4 Fable agents
  (safety, measurement, cost and design, performance) on draft 2. On a Fable 429, Opus runs them
  (OD-562). Findings are merged, P1…, spot-checked, and decided by the operator.
- **Records:**
  - SPEC §7.3 (the ARC paragraph; Limits updated) and §18 (Gmail result);
  - a new OD row (next free OD-576);
  - an ADR 0011 amendment;
  - CHANGELOG;
  - an operator-guide sentence explaining the forwarded line, in user-facing style;
  - SPEC §1.2 Later row: ARC becomes "built (show only), wider use Later".

## Verification (build)

- **Tests first** (they fail on main): `tests/test_senderauth.py` helpers (`sign`, `FakeDns`,
  :28-121) gain ARC-sealing fixtures (dkimpy `arc_sign` with `authres` as a dev dependency).
  Cases:
  - a valid one-hop and a valid two-hop chain;
  - a tampered body after sealing (the outer seal still valid, the inner AMS fails);
  - `cv=fail` and a `None` return;
  - over the cap;
  - the DNS budget used up;
  - a chain whose AAR claims a pass for a different domain (no card line);
  - `auth_result` unchanged in every case.
- **Isolation:** `test_isolate.py` round-trips the new field through the child.
- **Cards:** a card test for the line, and no line when there's no claim.
- **Performance:** the time per message on a 10-instance chain, against the budget.
- Targeted tests, ruff and pyright while working. The full suite once before the commit. The
  macOS gate (0 skipped) before the merge. CI after the push.
