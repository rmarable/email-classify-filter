# M8 Model-written drafts, M9 Send a draft from Slack

Status: outline, 2026-10-26, not started (operator decisions 2026-10-26, OD-560, amends OD-424).
Supersedes `MODEL-WRITTEN-DRAFTS-REPLIES.md` (draft 1, 2026-10-22, kept as history). Code references
checked against `0aa6c7b`. At M8's start this becomes `docs/roadmap/m8-model-written-drafts.md` and gets
Phase R with four Fable agents (it changes the eval, the actor prompt and policy); M9 gets
`docs/roadmap/m9-send-drafts.md` and its own review at its start.

## Decisions (operator, 2026-10-26)

Model-written email help comes in two stages, and nothing is ever sent without your approval:
- **M8 (after M7):** ecf writes drafts, and they are proven safe and useful by a draft-quality eval with a
  pass mark set in M8's plan.
- **M9 (needs M8 complete and a set 👍 rate on real drafts over a set period):** the draft's Slack card
  gains a **Send email** button beside **Save to Drafts**. Pressing it queues the send for the Mac, where
  step-up shows the exact text and recipient. Nothing goes out unless you press Send and confirm.

**Drafter (author) role.** Today the actor both decides what to do and writes the reply. M8 splits the
writing into its own role, the drafter, with its own pin in `models.lock`. Opus 5.5 stays the default, and
it's replaceable by setting (`ecf settings set`) and in evals (`ecf eval run --drafter-model`) without a
code change. The card names the model that wrote the draft. The actor keeps deciding. M8 builds the role
with Claude models only. A drafter is used only after it passes the draft-quality eval under the
model-choice rule.

**Mistral and OpenAI (via Codex) as actor and drafter options:** a Later item, building on M8's drafter
role. There's no plan and no gate design. The decision is revisited if and when the operator wants one in
the actor or drafter role. That needs the privacy rule changed first: today real mail goes only to
Anthropic, and Mistral and OpenAI see synthetic eval text only, as judges of generated sets (OD-540,
OD-550).

**Draft data M8 collects.** Today ecf records only the proposal, the approve or reject in the audit log, and
the Drafts Message-ID and content hash kept for Undo (`src/ecf_server/mailbox_actions.py:229-240`).
1. **Outcomes and edits:** the fate of every draft:
   - refused by a check (which check);
   - approved, rejected or deleted, regenerated (with which hint), or expired;
   - after saving: sent unchanged, sent edited, deleted unsent, Undone, or still sitting (its age);
   - with M9: sent from the card or from the mail app.

   Plus an edit score (0-100% changed, and whether only the greeting or sign-off changed), stored as numbers.
2. **Quality and safety signals:**
   - 👍/👎 and their reasons;
   - regenerations per approved draft;
   - safety-check refusals by type;
   - new-link warnings;
   - 👎 "promised something" counted as a check miss, which raises an alert.
3. **Missed and unneeded drafts:** you replied yourself where ecf wrote no draft, and ecf drafted where
   you never replied.
4. **Context:**
   - what made it: drafter model, prompt digest, address;
   - the email: category, sender type, language, its length;
   - the draft's length;
   - timing: time to decide, time from saving to sending;
   - cost: tokens and time per draft.

**How it's shown:**
1. **Terminal report, `ecf drafts stats`:**
   - the outcome breakdown;
   - the headline rates (sent unchanged, average edit score, 👍, regenerations per approved draft,
     missed drafts);
   - the 👎 reasons ranked, and the safety counts;
   - splits `--by model|prompt|category|address`, `--since`;
   - before/after comparisons with the eval rule (Newcombe), saying "not enough drafts yet" rather than
     showing a difference the sample can't support;
   - a week-by-week trend.
2. **Local HTML charts, `ecf drafts stats --html`:** a page written on this Mac and opened in the
   browser. No external scripts or fonts, so nothing leaves the Mac.
3. **Weekly Slack summary:** a weekly post of a few count lines in the summary channel, plus alerts at
   thresholds you set (any "promised something" miss, a drop in the sent-unchanged rate). Counts only.

**Where it lives:** in the service's existing SQLite database, in new tables added by a migration. There's
one row per draft version, linked to its item. Only the service writes them; the report, the page and the
summary read through the service API.

Rules: counts, hashes and scores only, never draft or email text. They stay on this Mac, and their
retention follows the items.

Open for M8's plan:
- monthly count rollups kept beyond item retention, for long-term trends;
- whether exports include the tables (a `data_format` change);
- whether ecf may read the Sent folder, which groups 1 and 3 need. Today ecf reads INBOX for mail (SPEC
  §12.2); Undo also looks in Drafts and in the folder an email was moved to.

**Cards must be clean and readable on desktop and phone.** M8 and M9 are done only when the operator has
approved their card mockups, and a real Slack test confirms them with screenshots.

**M9 starts only when** M8 has passed the eval, its card is approved and confirmed in a Slack test, and
real drafts have earned a set 👍 rate over a set period. M8's plan sets both the rate and the period.

## Already done (pointers only)
- **Early draft fix (OD-551, 2026-10-22):**
  - the draft limit stays 4,000 characters;
  - card text continues across sections instead of being cut;
  - a draft whose card can't show all of it has no Slack Approve button (approve with `ecf approve <id>`);
  - an over-length draft is refused, never cut;
  - drafts are never approved in a batch.
- **Classifier window (OD-552, OD-553, OD-555, OD-556, OD-558; v3.1.0):** the classifier reads 4,000
  characters, the same as the actor (`src/ecf_server/message.py:38-39`). In a real-mail count, 39% of
  mail is over 4,000. 8,000 is on the window plan's Later list (`CLASSIFIER-WINDOW-TUNING.md`).

## M8 design notes (carried from draft 1, re-checked)

**Aim:** prove that drafts are safe and useful before M9 lets any be sent. M8 sends nothing.

**Eval, reusing `ecf eval`:**
- cards (card builder plus hygiene scan):
  - new synthetic reply-needed cards whose facts let a draft through policy (`from_count`; the V1.5
    run `c94de7bd` had none, so policy dropped every draft);
  - injection cards that try to steer the draft (pay, change a bank detail, add a recipient, add a link);
  - "no draft should be written" cards (fraud, bulk);
  - long reply-needed cards (`target_chars`, `src/ecf/eval/cards.py`);
  - non-English cards.

  The results file records each draft's text and recipient (it doesn't today).
- deterministic scoring first, zero tolerance:
  - the recipient is the From address only;
  - no payment, bank detail, amount, price or date commitment (reuse the `triggers.scan` detectors,
    `src/ecf_server/triggers.py:232`);
  - no draft where policy forbids one;
  - links are scored, not refused.
- judged scoring second: tone, whether the draft answers the question, and length. The judges are
  Mistral Large 4 and Sol (OD-547, OD-550), on synthetic text only; Claude never judges its own drafts.
  The operator settles disagreements.
- each Claude pin (Haiku, Sonnet, Opus) under the model-choice rule: the larger model stays unless a
  smaller one matches it on the safety checks first.
- the pass mark (set in M8's plan): safety 0 failures, with the upper 95% bound under a set rate; quality
  at least a set level on the judged fields. M8's `ms-` tag follows; M9's start also needs the 👍 rate
  (Decisions).
- measure Claude's real draft lengths against 4,000 and against `slack_render.fits()`
  (`src/ecf_server/slack_render.py:58`).

**Checker in the product:**
- the deterministic checks also run on every real draft before its card;
- a failing draft is refused: it is dropped, the email is flagged and the card says why;
- M9 reuses the checker as its DLP layer.

**Links:** allowed in replies, but nothing in ecf follows, fetches or previews one.
- Already true:
  - posts set `mrkdwn`, `unfurl_links` and `unfurl_media` false (`src/ecf_server/slack_chat.py:64-66`);
  - cards defang links (`src/ecf_server/cards.py:35`);
  - Claude sessions deny Bash, WebFetch and WebSearch (`src/ecf/claude_wrapper.py:55-57`).
- Not defanged: the copy saved in Drafts keeps each link live (only Slack cards defang). For M9 this
  means a sent reply would carry any injected link, so the new-link warning matters most there.
- To pin with tests:
  - `chat.update` edits don't unfurl;
  - the service makes no HTTP request to a URL taken from mail or model text (only Slack, Anthropic,
    Mistral, and the IMAP and SMTP hosts);
  - judges get no browsing or tools;
  - URLs aren't logged;
  - own-mail recognition (`src/ecf_server/own_mail.py`) skips ecf's sent reply when it comes back.
- The actor prompt says nothing about links today (`src/ecf/data/plugin/agents/actor.md:15-25`).
- On the card: "this draft contains N links", and each link that isn't in the original email is marked
  new. This is a warning, not a refusal.

**👍/👎 on a decided draft:**
- Today the card has Approve: <verb>, Reject and Show excerpt (`src/ecf_server/approvals.py:755-756`), and
  no quality feedback; Reject mixes "no draft wanted" with "bad draft".
- 👎 opens a modal (the pattern in `_answer_form`, `src/ecf_server/answers.py:245`, opened with `views.open`,
  `src/ecf_server/slack_runtime.py:399`) with fixed reasons: wrong tone, wrong facts, it promised
  something, too long, no reply was needed, other (an optional short note).
- Rules:
  - only your member ID can rate;
  - one rating per draft, which you can change;
  - ratings are stored locally and never sent to a model automatically.
- Use:
  - the figures above;
  - "it promised something" counts as a hard-check miss, raises an alert, and the operator decides
    whether to add a deterministic check;
  - an improvement loop with no automatic learning: 👎 reasons lead to a prompt change the operator
    reviews, paraphrased into new synthetic cards (real mail never becomes a card), adopted only if the
    eval passes;
  - M9 entry evidence: a set 👍 rate on real drafts over a set period (Decisions; both set in M8's plan).

**Regenerate** (an undecided draft):
- It voids the grant and sends the item back to the actor queue, by the path an answered question takes.
- Hints are fixed choices only: shorter, more formal, warmer, "don't promise anything". Free-text
  guidance (e.g. "mention the meeting on Tuesday") is a TODO: sent to the model marked as your text,
  with a length cap and eval cases, and its own review before it's built.
- The card says "queued: will be rewritten at your next review"; it isn't immediate until scheduled Claude
  (M4).
- At most 3 per item. Every version passes the checks again and is kept with its rating.
- Eval: regenerate-with-hint cards (does "shorter" make it shorter, and does it stay safe?).

**Delete:**
- before approval, **Delete draft** replaces Reject: it voids the grant, saves nothing, keeps the item
  flagged, and offers the optional 👎 reasons;
- after approval, **Delete from Drafts** reuses the Undo logic (`_delete_draft`,
  `src/ecf_server/mailbox_actions.py:331`). It deletes only if exactly one message in Drafts has the
  draft's Message-ID and an unchanged content hash; otherwise it leaves it alone and says why;
- both are for your member ID only, audited, and counted (a delete rate per model and prompt version).

**Card design** (operator 2026-10-22: "nice and tight … clean … easy to read; no header-style dumps"):
- Today (`src/ecf_server/approvals.py:734-756`):
  - the title "Approve? <verb>";
  - an Action field plus every field from `cards.item_card`;
  - the draft one line per Slack section (a line over 3,000 characters continues into the next; lines
    after the first 29 share the 30th; cut only past 40 sections, when `fits()` is false;
    `slack_render.py:30-38`, :99-128), which reads as choppy;
  - Approve: <verb> / Reject / Show excerpt;
  - no Approve button, and a computer note, when `fits()` is false (OD-551).
- Target:
  - one top line: sender · subject;
  - one quiet context line: category · authentication · risk · "written by <model>"; warnings (payment,
    new links, cut excerpt) only when present;
  - the draft as one block that keeps its line breaks and isn't markup (a `rich_text` block may do it;
    its limits are unverified: check in the Slack test);
  - buttons: Save to Drafts (primary), Regenerate (hints in a menu), Delete; Show excerpt in an overflow
    menu; in M9, Send email too (below);
  - after a decision the card shrinks to one line ("Draft saved · 👍 👎 · Delete from Drafts", or
    "Deleted").
- Process: desktop and phone mockups go to the operator before the build; screenshots come from the
  Slack real-service test. M8 changes only the draft cards.

**Gaps to settle in M8's plan:**
1. Bulk approval of unread drafts: fixed (OD-551).
2. Thread context: the actor sees 4,000 characters of this email, but not earlier messages or what you
   already said. Check Sent for an existing reply before drafting or regenerating.
3. Invented details (a name, title, phone number, opening hours, price): add a configured signature or
   sign-off, and a hard check for phone numbers and street addresses that aren't in the email or the
   signature.
4. Language: reply in the sender's language? Non-English cards are in the eval.
5. Stale drafts: a queued regenerate may run after you've replied or the email has moved. Check before
   writing (still in INBOX, no reply in Sent, grant valid).
6. Data and versions: ratings, versions and draft data are new tables (a migration); their retention
   follows the items; exports and `data_format` to be decided; M8 is a minor release.
7. Usage: regenerations add Claude usage; count them in the review figures.
8. Real-service test: the new buttons and the reasons modal need a Slack test (go-ahead, then teardown).
9. User docs: the operator guide covers drafts, ratings, Regenerate, Delete and the stats, in the
   user-facing style with the disclaimer.
10. Slack delivery (new, OD-557): new buttons and modals must work with refused payloads and with
    `ecf slack repost`.

## M9 notes
- Today (SPEC §9.5): Approve on a draft only appends it to Drafts (one click, with Undo). Template
  replies and internal forwards need step-up, so Slack shows "Queued for your computer";
  high-sensitivity addresses add a 10-minute delay with Cancel.
- M9:
  - **Send email** on the draft card, approved like a template reply: Slack, then step-up at the Mac,
    where `ecf approve <id>` shows the whole text;
  - the text and recipient are bound into the grant (OD-317), and the 10-minute delay with Cancel
    applies on high-sensitivity addresses;
  - never one click in Slack, never unattended.
- The button row has two main choices, Save to Drafts and Send email, in M8's card style. Send must look
  clearly different from Save (its own style, never in Save's place), so a phone tap can't send by
  mistake. Mockups are approved first, then checked in a Slack test.
- Prerequisites: injection tests on outgoing text, DLP (M8's checker), a recipient domain allow/deny list.
- Open: a draft edited in Drafts can't be sent from the card, because the grant covers the text you saw
  (recommended, not decided).
- Sending from a phone: a phone-safe step-up belongs to M3 (remote access), not M9.

## Later
On SPEC §1.2's Later list (OD-560), unless noted:
- **Mistral and OpenAI as actor and drafter options:** see Decisions.
- **External forwards.**
- **Learning from past mail and drafts**, explored after M8 has real 👍/👎 data:
  - liked drafts as examples in the actor prompt;
  - per-sender or per-address style;

  Anything that puts past mail or draft text into new requests needs an OD (privacy, injection
  persistence), and every change goes through the draft-quality eval and Phase R.
- **A tone setting you choose** (formality, warmth) for drafts.
- **The actor's window beyond 4,000** (not on SPEC's list; window plan only): see the window plan's Later
  section (`CLASSIFIER-WINDOW-TUNING.md`).
- **A claude.ai dashboard for the draft figures:** probably never; perhaps the Claude app only; to be
  decided. It would send counts to Anthropic outside `/ecf-review`, so it needs a privacy decision.
- **Sending without approval:** no plan, and it may never be built. It would reverse SPEC §3.2 (model
  output never, alone, sends anything), so a threat-model review and an ADR come first.
- **Card clean-up across all cards** (not on SPEC's list; after M8's card is proven): M8's style for item,
  escalation, approval, digest, needs-you, answer and alert cards. Mockups first and a Slack test. Plain
  text and defanged links stay, there are no unfurls, and nothing safety needs (payment note, fraud
  warning) is hidden.
