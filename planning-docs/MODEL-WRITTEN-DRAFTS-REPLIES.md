# Roadmap: M8 Model-written drafts, M9 Model-written replies

Status: superseded 2026-10-26 by `M8-M9-DRAFTS-AND-SEND.md` (OD-560); kept as history. Was: draft 1, 2026-10-22, not started.

## Context

Model-written replies sit on SPEC §1.2's Later list (OD-424, SPEC.md:85), with a draft-quality eval
as the first prerequisite. Nothing is planned or built for that eval, and V1.5's eval never measured a
draft: its cards lacked the fact that lets a draft through policy, so policy dropped them all (run
`c94de7bd`). The operator wants two linked milestones, with replies depending on drafts completing
successfully. Operator decisions (2026-10-22):
- M8 and M9 go at the end of the roadmap, after M7.
- Replies only. External forwards stay on the Later list.
- "Complete" means the draft-quality eval passes a pass mark that the operator sets when M8's plan
  is written. M9 starts only after that, at M8's `ms-` tag.

This is a docs-only change to the roadmap entries. Nothing gets built, and Phase R isn't needed,
since no classifier, rules, policy, facts or eval change. Each milestone gets its own design review
at its start, like M6 and M7.

## Changes (worktree `../ecf-roadmap-drafts`, branch `roadmap-drafts`, from `origin/main`)

1. **`SPEC.md` §1.2**
   - Intro sentence (line 36): add that M8 (model-written drafts) and M9 (model-written replies,
     which need M8) are outlined below, each with its own design review, and cite the new OD.
   - Table: two rows after M7.
     - **M8 Model-written drafts (no dependency):** the draft-quality eval and draft improvements.
       The eval uses synthetic reply-needed cases whose facts let a draft through policy, and its
       results file records each draft's text. It scores commitments (no payment, bank detail,
       price or date), recipient, tone and injection-following, for each Claude pin. M8 is
       complete when the eval meets the pass mark set in its plan. Design in: this section, plus
       `docs/roadmap/m8-model-written-drafts.md` at its start.
     - **M9 Model-written replies (needs M8 complete):** the model sends its own replies, as
       today's template replies are sent. The exact outgoing text is shown in Slack and bound into
       the grant (OD-317). Prerequisites: injection tests on outgoing text, DLP, and a recipient
       domain allow/deny list. External forwards are not included.
   - Later row (line 47): remove "model-written replies".
   - Paragraph at line 85: rewrite as **M8 Model-written drafts** and **M9 Model-written replies**
     paragraphs. Keep the existing facts (`from_count`, run `c94de7bd`, "it can measure today's
     drafts") and the dependency rule, and keep the OD-424 citation.
2. **`SPEC.md` OD table:** a new OD row. The next free number is OD-550 today, but the peer session
   is adding ODs, so re-check right before committing. The row records:
   - the M8 and M9 milestones, with M9 depending on M8;
   - the pass mark, set when M8's plan is written;
   - that external forwards stay Later;
   - that this amends OD-424.
3. **`AGENTS.md:23`:** extend the roadmap list with "M8 model-written drafts, M9 model-written
   replies".
4. **`CHANGELOG.md`, Unreleased:** add one line in plain language: "Planned: model-written replies
   now come after their own milestone for drafts, which must pass a draft-quality eval first
   (OD-550)."
5. The design plan (`docs/history/`) stays unchanged, since it's immutable. There are no
   `planning-docs/` outlines yet: the SPEC paragraphs serve as the outline, as for M7's row.

## Steps

- Re-read `CLAUDE-STATE.md` and add a progress line. Then:
  1. Make the edits in the worktree and show the diff.
  2. Commit with the operator's OK, using the trailer
     `Co-Authored-By: Claude <noreply@anthropic.com>`.
  3. Merge `--no-ff` into main with "docs only, no macOS gate", and push.
  4. Confirm CI is path-skipped.
  5. Remove the worktree and branch (OK needed).
- Tell the peer session the OD number used.

## Verification

- `git diff --stat` touches only `SPEC.md`, `AGENTS.md` and `CHANGELOG.md`.
- `grep -n "M8\|M9" SPEC.md` shows the intro sentence, both table rows, both paragraphs and the OD
  row.
- The Later row no longer lists model-written replies.
- Each OD number appears exactly once in the OD table.
- `gh run list --commit <merge>` is empty (docs paths are ignored).

## Claude's view of M8 and M9 (2026-10-22, discussion only)

Work on this section in plan mode. The early draft fix and the email-size tuning come before M8 (operator 2026-10-22).

### Context
The operator asked how M8 should work. The other session's outline is
`planning-docs/MODEL-WRITTEN-DRAFTS-REPLIES.md` (draft 1, untracked; operator not ready to proceed).
Drafts already exist as built in V1.5 (SPEC §8.3, §8.4):
- the actor proposes `draft_reply`;
- the operator approves it in Slack;
- the service appends the draft to Drafts and never sends it, and Undo deletes it.

The draft's quality has never been measured. V1.5's eval cards had no `from_count`, so policy
dropped every draft (run `c94de7bd`), and the results file doesn't record draft text.

### View
1. **The aim of M8:** prove that drafts are safe and useful before M9 lets any be sent. M8
   sends nothing.
2. **Eval, reusing `ecf eval`:**
   - new synthetic reply-needed cards (card builder plus hygiene scan), whose facts let a draft
     through policy;
   - injection cards that try to steer the draft (pay, change a bank detail, add a recipient,
     include a link);
   - "no reply should be drafted" cards (fraud, bulk).
   The results file records the draft text and the recipient.
3. **Deterministic scoring first:**
   - the recipient is the From address only;
   - no payment, bank detail, amount, price or date commitment (reuse `triggers.scan` detectors);
   - links are allowed and scored, not refused (see item 9);
   - no draft where policy forbids one.
   Zero tolerance on these.
4. **Judged scoring second:** tone, whether the draft answers the question, and length. The
   non-Claude judges are Mistral Large 4 and Sol (OD-547/OD-550); Claude never judges its own
   drafts. The operator settles disagreements, and only synthetic text goes to judges.
5. **The checker moves into the product:** the deterministic checks also run on every real draft
   before its Slack card. A failing draft is refused (operator 2026-10-22). M9 reuses this as
   its DLP layer.
6. **Per model:** run each Claude pin (Haiku, Sonnet, Opus) under the model-choice rule. The
   larger model stays unless a smaller one matches it on the safety checks first.
7. **Real-mail signal (local only):** count how many approved drafts are edited before sending
   (Drafts content hash changes) and how many are Undone. The count stays on this Mac.
8. **Pass mark (set in M8's plan):**
   - safety: 0 failures, with the upper 95% bound under a set rate;
   - quality: at least a set level on judged fields.
   M9 starts at M8's `ms-` tag.
9. **Fix: show the full draft before approval.**
   - The bug: the draft cap is 4,000 characters (`src/ecf_server/outbound_plan.py:31`, OD-317;
     no reason recorded). The Slack card limits a section to 3,000 characters (`TEXT_MAX`) and a
     card to 30 line sections (`src/ecf_server/slack_render.py:29,84-90`). A long single
     paragraph, or a draft of more than 30 lines, is cut with "…", yet the full text is bound
     into the grant. SPEC §8.4 says the full text is shown before approval.
   - Step 1: a test first. Render a maximum-length draft (one paragraph, and 40 short lines),
     check the card shows every character, and prove the test fails today.
   - Step 2: lower `DRAFT_MAX` to 3,000. Also refuse, at planning, any draft whose card would
     be cut (count its line sections against the cap). The draft is then dropped and the email
     flagged, with the reason on the card, which is the same handling as a draft that fails a
     hard check.
   - Step 3: update the MCP schema's `maxLength` (`src/ecf/mcp_server.py:289`) and the actor
     prompt (`agents/actor.md`). The SPEC §8.4 text and a new OD amend OD-317 and record the
     reason. Add a CHANGELOG line.
   - The cleaning step also cuts silently today: `clean_draft` truncates at `DRAFT_MAX`
     (`src/ecf_server/outbound_plan.py:41-49`). Change it to refuse an over-length draft instead.
   - URLs in drafts: today nothing stops them.
     - The actor prompt doesn't mention links (`src/ecf/data/plugin/agents/actor.md:15-17`).
     - `clean_draft` keeps them.
     - Slack defangs them, so they aren't clickable (`src/ecf_server/cards.py:30-38`).
     - The Drafts copy keeps the live link.

     Operator decision 2026-10-22: URLs are allowed in replies, but they must never poison
     ecf's environment. Nothing in ecf follows, fetches or previews a link.
     - Already true (code read 2026-10-22):
       - Slack posts set `unfurl_links` and `unfurl_media` false and `mrkdwn` false
         (`src/ecf_server/slack_chat.py:65-66`).
       - Cards defang links.
       - Claude sessions deny WebFetch, WebSearch and Bash (`src/ecf/claude_wrapper.py:54-58`).
     - To check and pin with tests in M8:
       - `chat.update` edits don't unfurl;
       - the service never makes an HTTP request to a URL taken from mail or model text (only
         Slack, Anthropic, Mistral and the IMAP and SMTP hosts);
       - judges (Mistral, Sol) get no browsing or tools;
       - URLs aren't logged;
       - own-mail recognition skips ecf's sent reply when it comes back.
     - On the card: show "this draft contains N links". Mark each link that isn't in the
       original email as new, so an injected link stands out. This is a warning, not a refusal
       (operator accepted 2026-10-22).
   - Step 4: measure Claude's real draft lengths in M8's eval, to confirm 3,000 is enough.
   - Timing: the cut-draft and silent-truncation parts move to the **Early draft fix** at the
     top, with a 4-agent Phase R. Links and length measurement stay in M8.
10. **Process:** M8 changes the eval, the actor prompt and policy, so it gets a full Phase R
   (4 Fable agents) before the build.

### Email size and accuracy (found 2026-10-22; operator: do this BEFORE M8, as classifier tuning)
- Goal: tune the classifier's input window for accuracy. Safety-relevant fields come first
  (phishing, fraud_risk, payment_related, rule and safety outcomes), then the others; cost and
  time decide only between results that are equal.
- Levers:
  - window size (classifier 1,500 and actor 4,000 today);
  - how the cut is made (head only, or head plus tail, or new text before quoted text);
  - whether the classifier and actor share one window.
- Operator choices (2026-10-22): test window size and a shared classifier and actor window.
  Not the cut strategy. Step 1 (counting real mail lengths) is approved.
- Step 1 blocker: no corpus exists. The 2026-10-08 tidy found the corpus test files gone. A new
  `ecf corpus fetch` (SPEC §16.7) is a real-service run: it needs step-up, a Security Notice and
  the operator's go-ahead, and the operator chooses the mailbox. The manifest records each
  message's raw `size` (`src/ecf_server/corpus.py:571`) and a stored excerpt, which is capped at
  4,000 characters. So it shows the share of mail cut at 1,500 and at 4,000, but not the share
  cut at 8,000, without new code.
- Corpus handling, as explained to and accepted by the operator (2026-10-22):
  - the mailbox is read-only (EXAMINE, BODY.PEEK);
  - the corpus is one age file outside the data folder and outside any git tree; the
    passphrase is shown once, in the operator's Terminal, never in chat;
  - nothing goes to any model;
  - the count script is throwaway, lives in the scratchpad, and the operator runs it in Terminal;
    it prints counts only;
  - the corpus file is deleted after counting (ask first).
- The frozen synthetic set changes when long cards are added. That makes a new set version,
  with baselines re-run on it before any comparison.
- Risk: a larger window gives injected text more room. Long injection cards are part of the
  set, and injection-following is reported per card.
- The eval doesn't test size. The 304 synthetic card files measure 466-2,009 bytes (median
  907, front matter included), and only one is padded (`pad_to_mb: 11`, for the size limit).
  Reports have no size breakdown, though the build index records each case's `bytes`
  (`src/ecf/eval/builder.py:145`).
- The classifier sees the first 1,500 characters (`CLASSIFIER_CHARS`,
  `src/ecf_server/message.py:38`). The actor sees 4,000 (`ACTOR_CHARS`). The 1,500 limit dates
  from the local model's context window, and the local model was removed (ADR 0031).
- Tuning steps, in order:
  1. Measure the body-length distribution of real mail on the local corpus (SPEC §16.7;
     read-only, counts only) to see how much mail passes 1,500 and 4,000 characters.
  2. Add a size breakdown to `ecf eval run` reports: cut or not cut at each limit.
  3. Add long-email cards: the same scam with its signal at the start, past 1,500 and past
     4,000, behind benign threads, quotes and footers. Build them with the builder, keep them
     hygiene-clean, and have them labelled by the operator.
  4. Compare classifier windows of 1,500, 4,000 and 8,000 on Claude under the evaluation rules
     (Newcombe), recording cost and time per call. Every window is a new gate key.
- Changes to the classifier and eval need Phase R with 4 Fable agents before any build.

### M8: like or dislike a draft in Slack (proposed 2026-10-22; operator: thumbs OK here, since the "no thumbs" note was for the eval pilot only)
- Today, a draft card has **Approve: save draft**, **Reject** and **Show excerpt**
  (`src/ecf_server/approvals.py:715-717`). There is no quality feedback. Reject mixes "no draft
  wanted" with "bad draft".
- Proposed: once the draft is decided, the card gains 👍 and 👎. 👎 opens a modal (the pattern
  from Answer, `src/ecf_server/answers.py:299`) with fixed reasons:
  - wrong tone;
  - wrong facts;
  - it promised something;
  - too long;
  - no reply was needed;
  - other, with an optional short note.
- Rules:
  - only your member ID can give feedback;
  - one rating per draft, which can be changed;
  - it is stored locally in SQLite with the item and is never sent to a model automatically;
  - it is shown in M8's figures beside the edit and Undo counts.
- Use (proposed):
  1. **Figures:** each rating is stored with the actor model and the prompt digest. A report
     gives the 👍 rate and the 👎 reasons for each model and prompt version, beside the edit and
     Undo rates.
  2. **Safety signal:** a 👎 for "it promised something" counts as a hard-check miss. It raises
     an alert, and the operator decides whether a new deterministic check is added.
  3. **Improvement loop, with no automatic learning:** a periodic summary of 👎 reasons leads to
     a prompt change the operator reviews. The pattern is paraphrased into new synthetic cards
     (real mail never becomes a card), the eval runs, and the change is adopted only if it
     passes.
  4. **M9 entry evidence:** besides the eval pass mark, a set 👍 rate on real drafts over a set
     period. The operator sets both in M8's plan.
- Not done: no liked drafts are fed back into prompts as examples, and nothing is tuned per
  sender. Either would put past mail text into new requests, so it would need an OD.

### M8: "Regenerate draft" button (proposed 2026-10-22)
- On an undecided draft card: **Regenerate**. It voids the current grant and sends the item
  back to the actor queue, reusing the path an answered question takes (`clarified` returns to
  the actor; SPEC §7, `answers.py`). The new draft gets a new card and a new grant.
- Optional guidance, as fixed choices only (operator decision 2026-10-22): shorter, more
  formal, warmer, or "don't promise anything".
- TODO (later): free-text guidance (for example "mention the meeting on Tuesday"). It would be
  sent to the model marked as the operator's text, with a length cap and eval cases. It needs
  its own review before it's built.
- Timing: Claude runs only on demand (`/ecf-review` in an `ecf claude` session). The card says
  "queued: will be rewritten at your next review". It isn't immediate until scheduled Claude
  (M4) exists.
- Limits:
  - at most 3 regenerations per item;
  - every version passes the hard checks again;
  - each version and its rating are kept, so the figures show regeneration rates per model and
    prompt version.
- Eval: regenerate-with-guidance cards (does "shorter" make it shorter, and does it stay
  safe?).

### M8: "Delete draft" button (operator request 2026-10-22)
- **Before approval:** on a draft card, **Reject** is relabelled **Delete draft**. It voids the
  grant and saves nothing, and the item stays flagged for the operator. It then offers the 👎
  reasons, which are optional.
- **After approval:** the card gets **Delete from Drafts**. It reuses the existing Undo logic
  (SPEC §8.4, step 2c). It deletes only if exactly one message in Drafts has the draft's
  Message-ID and its content hash is unchanged. A draft that was edited, sent or already deleted
  is left alone, and the card says why.
- Both actions are only for the operator's member ID, are audited, and count in the figures (a
  delete rate per model and prompt version).

### M8: card design (operator 2026-10-22: "nice and tight … clean … easy to read; no header-style dumps")
- Today's approval card (`src/ecf_server/approvals.py:704-717`) has:
  - the title "Approve? <verb>";
  - an Action field plus every field from `cards.item_card`;
  - the draft split into one section per line (`slack_render._text_sections`), which reads as
    choppy and is where the 30-line and 3,000-character cuts come from;
  - three buttons.
- Target layout:
  - **one line on top:** sender · subject;
  - **one quiet context line:** category · authentication · risk. Warnings appear only when
    present: payment, new links, a cut excerpt;
  - **the draft as one block** that keeps its line breaks and isn't markup. A Slack `rich_text`
    block may do this (limits unverified; check in the Slack test). The "written by ecf's model"
    label goes in the context line, not in a heading;
  - **buttons:** Approve (primary), Regenerate (its hints in a menu), Delete. "Show excerpt" moves
    into an overflow menu;
  - **after a decision,** the card shrinks to one line, "Draft saved · 👍 👎 · Delete from
    Drafts", or "Deleted".
- Process: mockups of the desktop and phone versions go to the operator before the build, and
  screenshots come from the Slack real-service test.
- Scope: the same tightening could apply to other cards later. M8 changes only the draft cards.

### Gaps found in the review of 2026-10-22 (to settle in M8's plan)
1. **Possible bug today: drafts can be bulk-approved without being read.**
   - What the code does: "Approve all N reversible" excludes `approvals.SENDS`
     (`forward_internal`, `reply_template`), but `draft_reply` is in `approvals.REVERSIBLE`
     (`src/ecf_server/digest_actions.py:63-65`, `src/ecf_server/approvals.py:64-66`). A
     digest's "Approve all" can therefore approve drafts whose text wasn't shown.
   - SPEC §9.5 says Approve-all excludes outbound, and `outbound_plan.OUTBOUND` includes drafts.
   - This comes from reading the code. Prove it with a test first, then exclude `draft_reply`.
     It moves to the **Early draft fix** at the top (operator 2026-10-22).
2. **Thread context:** the actor sees only this email's excerpt, not earlier messages or what you
   already said, so it may draft wrong facts. This links to the window tuning, and to checking
   whether you already replied (search Sent) before drafting or regenerating.
3. **Invented details:** the model can make up a name, title, phone number, opening hours or a
   price. Add:
   - a configured signature or sign-off;
   - a hard check for phone numbers and street addresses that aren't in the email or the
     signature.
4. **Language:** reply in the sender's language? Add non-English cards to the draft eval.
5. **Stale drafts:** a regenerate queued until the next review may run after you've replied or
   the email has moved. Check before writing (email still in INBOX, no reply in Sent, grant
   valid).
6. **Data and versions:**
   - ratings and versions are new tables, so a migration is needed;
   - their retention follows the items;
   - whether exports change (and a `data_format` bump) is to be decided;
   - M8 is a minor release.
7. **Usage:** regenerations add Claude usage. Count them in the review figures.
8. **Real-service test:** the new Slack buttons and the reasons modal need a Slack test (with
   the go-ahead, then teardown), as V1.2's did.
9. **User docs:** the operator guide covers drafts, ratings, Regenerate and Delete, written in
   the user-facing style with the disclaimer.

### Later item: clean up Slack cards globally (operator 2026-10-22)
Apply M8's card style to every card:
- item and escalation cards;
- approvals, digests, the needs-you card, answers and alerts.

The style: a single top line, one quiet context line, warnings only when present, buttons
trimmed with secondary actions in an overflow menu, and cards that shrink after a decision. It
starts from M8's draft card once that is proven. Mockups go to the operator first, and a Slack
real-service test confirms them. Constraints: plain text and defanged links stay; there are no
unfurls; nothing that safety needs (payment note, fraud warning) is hidden.

### Later item: draft-reply tuning (operator 2026-10-22, "explore down the road")
To go on SPEC §1.2's Later list when these notes are saved. Explore after M8 has real 👍/👎
data. Ideas to explore:
- liked drafts as examples in the actor prompt;
- per-sender or per-address style (formality, sign-off, length);
- a tone setting you choose;
- the actor's window (shared with the email-size tuning);
- a model-choice comparison for the drafting role.


Constraints:
- anything that puts past mail or draft text into new requests needs an OD (privacy, and
  injection persistence);
- every change goes through the draft-quality eval and Phase R.

### M9 note: how a model-written reply is approved
- Today (SPEC §9.5): Approve on a draft in Slack only appends it to Drafts (one click, Undo
  available). Template replies and internal forwards need step-up, so Slack shows "Queued for your
  computer". High-sensitivity addresses add a 10-minute delay with Cancel.
- Proposed for M9: a model-written reply is approved like a template reply. The Slack Approve
  button, then step-up at the Mac, where `ecf approve <id>` shows the whole text. The reply text
  and its recipient are bound into the grant. On high-sensitivity addresses, the 10-minute delay
  with Cancel applies too. It is never one click in Slack, because the send can't be undone and a
  Slack session or phone is weaker proof than step-up.
- Sending from a phone: design a phone-safe step-up in M3 (remote access), not in M9.

Operator answers (2026-10-22):
- A real draft that fails a hard check is refused: the draft is dropped, the email is flagged
  and the card says why.
- The outline is for discussion only: nothing is written now. It goes into M8's plan when the
  operator is ready.
