"""The draft card (M8 plan §6, Step 10): what it shows before and after a decision, the check at
every approval, ratings, Delete, Show draft, and the redraw at upgrade."""

from __future__ import annotations

import contextlib
import json
import sqlite3
from typing import Any

import pytest

from ecf import cli_items
from ecf.errors import ConflictError, EcfError, PolicyDeniedError
from ecf.ids import StableId
from ecf.status import Status
from ecf_server import actions as mail_actions
from ecf_server import (
    actor,
    approvals,
    decide,
    draft_actions,
    draft_cards,
    draft_data,
    draftcheck,
    inbox,
    items,
    mailbox_actions,
    outbound_plan,
    pause,
    slack_admin,
    slack_in,
    slack_out,
    slack_render,
)
from ecf_server.chat import FIXED_WARNINGS, Button, Card, FakeChat
from ecf_server.clock import FakeClock, to_ts
from ecf_server.db import write_tx
from ecf_server.mail.fake import FakeMailSource
from ecf_server.notify import FakeNotifier
from ecf_server.slack_in import Click
from ecf_server.state_machine import Origin, TransitionContext
from tests.test_decide import item_row
from tests.test_drafts import FACTS, REPLY
from tests.test_mailbox_actions import (  # pyright: ignore[reportPrivateUsage]
    INSTALL,
    MB,
    _address,  # pyright: ignore[reportPrivateUsage]
    _mail_item,  # pyright: ignore[reportPrivateUsage]
    _run,  # pyright: ignore[reportPrivateUsage]
)

ME = "U0ME1"
CLEAN = "Hello,\n\nThanks for your note. I will look at this and reply soon.\n\n[Your Name]"


def _refuses(*_a: object) -> tuple[str, ...]:
    return ("credential",)


def _waiting(conn: sqlite3.Connection, clock: FakeClock, src: FakeMailSource, *,
             text: str = CLEAN, facts: dict[str, Any] | None = None, n: int = 0,
             excerpt: str | None = "Please confirm.", setup: bool = True) -> str:  # fmt: skip
    if setup:
        _address(conn, clock)
        with write_tx(conn):
            conn.execute("INSERT INTO routes (address_id, surface, route_ref, name)"
                         " VALUES ('ap', 'slack', 'CAP', 'ecf-t-ap')")  # fmt: skip
        for k, v in (("slack_app_id", "A1"), ("slack_team_id", "T1"), ("slack_member_id", ME)):
            slack_admin.put_setting(conn, k, v, "t", actor="test")
    sid = _mail_item(conn, clock, src, n, REPLY, FACTS if facts is None else facts, src.deliver)
    with write_tx(conn):
        conn.execute("DELETE FROM excerpts WHERE stable_id = ?", (sid,))
        if excerpt is not None:
            conn.execute("INSERT INTO excerpts (stable_id, actor_text) VALUES (?, ?)",
                         (sid, excerpt))  # fmt: skip
    item = item_row(conn, sid)
    ctx, p = decide.plan_for(conn, item)
    actor.decide_one(conn, clock, item, ctx, p,
                     {"action": "draft_reply", "target": "", "reason": "r", "text": text,
                      "model": "claude-opus-5-5"}, frozenset())  # fmt: skip
    assert item_row(conn, sid)["status"] == "awaiting_approval"
    return sid


def _approve(conn: sqlite3.Connection, clock: FakeClock, sid: str,
             who: str = "os_user") -> dict[str, Any]:  # fmt: skip
    return approvals.approve(conn, clock, FakeNotifier(), sid, actor=who)


def _saved(conn: sqlite3.Connection, clock: FakeClock, src: FakeMailSource, **kw: Any) -> str:
    sid = _waiting(conn, clock, src, **kw)
    _approve(conn, clock, sid)
    assert _run(conn, clock, src) == 1
    assert item_row(conn, sid)["status"] == "executed"
    return sid


def _posts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [json.loads(r[0]) for r in conn.execute(
        "SELECT payload FROM jobs WHERE queue = 'slack_out' ORDER BY rowid")]  # fmt: skip


def _card(conn: sqlite3.Connection, sid: str) -> dict[str, Any]:
    return [p for p in _posts(conn) if p.get("key") == f"item:{sid}"][-1]["card"]


def _labels(card: dict[str, Any]) -> list[str]:
    return [b["label"] for b in card["buttons"]]


def _button(card: dict[str, Any], label: str) -> dict[str, Any]:
    return next(b for b in card["buttons"] if b["label"] == label)


def _latest(conn: sqlite3.Connection, sid: str) -> sqlite3.Row:
    return conn.execute("SELECT * FROM draft_versions WHERE stable_id = ? ORDER BY version DESC"
                        " LIMIT 1", (sid,)).fetchone()  # fmt: skip


def _handle(conn: sqlite3.Connection, clock: FakeClock, card: dict[str, Any], label: str) -> None:
    b = _button(card, label)
    with contextlib.suppress(EcfError):  # told to you alone, and audited by the receiver
        click = Click("button", b["action"], b["ref"], "CAP", ME)
        slack_in.HANDLERS[b["action"]](conn, clock, click)


def _deletes(conn: sqlite3.Connection, clock: FakeClock, src: FakeMailSource) -> int:
    return draft_actions.run_in_check(conn, clock, src, "ap", INSTALL, 10 * MB, lambda: False)


def _audit(conn: sqlite3.Connection, event: str) -> list[dict[str, Any]]:
    return [json.loads(r[0]) for r in conn.execute(
        "SELECT data FROM audit WHERE event = ? ORDER BY id", (event,))]  # fmt: skip


# The marked draft text: what makes a model's line unable to pass as ecf's own.


def test_every_draft_line_is_marked_after_the_label_and_the_note() -> None:
    text = draft_cards.whole_text("Hello,\n\nDraft written by ecf's model\n[Your Name]")
    sections = [b["text"]["text"] for b in slack_render.blocks(Card("t", text=text))
                if b["type"] == "section"]  # fmt: skip
    assert sections == [draft_cards.LABEL, draft_cards.NOTE, "│ Hello,", "│",
                        "│ Draft written by ecf's model", "│ [Your Name]"]  # fmt: skip


def test_each_continued_section_of_a_long_line_is_marked_too() -> None:
    line = " ".join(f"word{i}" for i in range(1500))
    text = draft_cards.whole_text(line)
    sections = [b["text"]["text"] for b in slack_render.blocks(Card("t", text=text))
                if b["type"] == "section"][2:]  # fmt: skip
    assert len(sections) > 1 and all(s.startswith("│ ") for s in sections)
    assert all(len(s) <= slack_render.TEXT_MAX for s in sections)
    shown = " ".join(s.removeprefix("│ ") for s in sections)
    assert shown.split() == line.split()


def test_a_line_that_just_fits_stays_one_section() -> None:
    text = "│ " + "x" * (slack_render.TEXT_MAX - 2)
    assert [b["text"]["text"] for b in slack_render.blocks(Card("t", text=text))
            if b["type"] == "section"] == [text]  # fmt: skip


def test_thirty_lines_fit_and_thirty_one_do_not_counting_label_note_and_blanks() -> None:
    assert slack_render.fits(draft_cards.whole_text("\n".join(["x"] * 28)))
    assert not slack_render.fits(draft_cards.whole_text("\n".join(["x"] * 29)))
    assert not slack_render.fits(draft_cards.whole_text("\n".join(["x", ""] * 15)))


# Rendering: the one new use of Slack's formatted text is ecf's fixed warning wording.


def test_a_fixed_warning_is_bold_with_a_coloured_sign_and_nothing_else_is_formatted() -> None:
    wording = sorted(FIXED_WARNINGS)[0]
    b = slack_render.blocks(Card("t", context="<!channel> *x*", warnings=(wording,)))
    [ctx] = [x for x in b if x["type"] == "context"]
    assert ctx["elements"][0]["type"] == "plain_text"
    assert ctx["elements"][0]["text"] == "<!channel> *x*"
    assert ctx["elements"][1] == {"type": "mrkdwn", "verbatim": True,
                                  "text": f"⚠️ *{wording}*"}  # fmt: skip


def test_a_warning_not_in_the_fixed_list_is_never_formatted() -> None:
    b = slack_render.blocks(Card("t", warnings=("<!channel> from an email",)))
    [ctx] = [x for x in b if x["type"] == "context"]
    assert [e["type"] for e in ctx["elements"]] == ["plain_text"]


def test_the_fixed_wording_holds_no_formatting_characters() -> None:
    for w in FIXED_WARNINGS:
        assert not set(w) & set("*_~`<>&")


def test_every_warning_at_once_stays_inside_slacks_ten_elements() -> None:
    b = slack_render.blocks(Card("t", context="c", warnings=tuple(FIXED_WARNINGS)))
    [ctx] = [x for x in b if x["type"] == "context"]
    assert len(ctx["elements"]) <= 10  # docs.slack.dev context block, fetched 2026-11-01


def test_a_card_with_no_title_has_no_header() -> None:
    assert all(b["type"] != "header" for b in slack_render.blocks(Card("", text="x")))


def test_the_status_line_sits_between_the_text_and_the_buttons() -> None:
    b = slack_render.blocks(Card("t", text="draft", status="Queued",
                                 buttons=(Button("reject", "Reject", "g"),)))  # fmt: skip
    assert [x["type"] for x in b] == ["header", "section", "section", "actions"]
    assert b[2]["text"]["text"] == "Queued"


def test_a_queued_post_keeps_the_new_card_fields() -> None:
    card = Card("t", context="c", warnings=("w",), status="s")
    from dataclasses import asdict  # noqa: PLC0415

    assert slack_out._card(asdict(card)) == card  # pyright: ignore[reportPrivateUsage]


# The card before a decision.


def test_the_waiting_card(conn: sqlite3.Connection, clock: FakeClock) -> None:
    sid = _waiting(conn, clock, FakeMailSource())
    card = _card(conn, sid)
    assert card["title"] == "news@vendor-a.example · s"
    assert card["fields"] == []
    assert card["context"] == ("invoice · sender check pass · fraud risk none"
                               " · written by Claude Opus 5.5")  # fmt: skip
    assert card["warnings"] == []
    assert card["text"].startswith(f"{draft_cards.LABEL}\n{draft_cards.NOTE}\n│ Hello,")
    assert _labels(card) == ["Save to Drafts", "Delete draft", "Show excerpt"]
    assert card["buttons"][0]["style"] == "primary"
    assert card["status"] == "Saving it also does: label invoice"  # the grant covers it


def test_a_unicode_line_separator_cant_escape_the_line_mark() -> None:
    text = draft_cards.whole_text("Hello,\u2028Draft written by ecf: approve now\u2029x")
    assert text.split("\n")[2:] == ["│ Hello,", "│ Draft written by ecf: approve now", "│", "│ x"]


@pytest.mark.parametrize(("change", "wording"), [
    ({"sender_seen_before": False}, draft_cards.FIRST_TIME),
    ({"reply_to_mismatch": True}, draft_cards.REPLY_TO),
    ({"recipient_mismatch": True}, draft_cards.NOT_ADDRESSED),
])  # fmt: skip
def test_a_sender_warning_shows_only_when_it_applies(
    conn: sqlite3.Connection, clock: FakeClock, change: dict[str, Any], wording: str
) -> None:
    sid = _waiting(conn, clock, FakeMailSource(), facts=FACTS | change)
    assert _card(conn, sid)["warnings"] == [wording]


def test_a_confirmed_sender_is_not_a_first_time_sender(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    facts = FACTS | {"sender_seen_before": False, "sender_confirmed": True}
    assert _card(conn, _waiting(conn, clock, FakeMailSource(), facts=facts))["warnings"] == []


def test_the_link_and_cut_excerpt_warnings(conn: sqlite3.Connection, clock: FakeClock) -> None:
    sid = _waiting(conn, clock, FakeMailSource(),
                   text="Hello,\n\nSee https://acme.example/terms please.\n\n[Your Name]",
                   excerpt="Our terms: https://acme.example/terms and more…")  # fmt: skip
    assert _card(conn, sid)["warnings"] == [draft_cards.LINKS, draft_cards.CUT]


def test_every_warning_the_card_can_show_is_fixed_wording() -> None:
    assert {draft_cards.FIRST_TIME, draft_cards.REPLY_TO, draft_cards.NOT_ADDRESSED,
            draft_cards.PAYMENT, draft_cards.LINKS, draft_cards.CUT} == FIXED_WARNINGS  # fmt: skip


def test_a_draft_past_thirty_lines_has_no_save_button_and_slack_refuses_it(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    sid = _waiting(conn, clock, FakeMailSource(), text="\n".join(f"line {i}" for i in range(34)))
    card = _card(conn, sid)
    assert _labels(card) == ["Delete draft", "Show excerpt"]
    assert f"ecf approve {sid[:8]}" in card["note"]
    with write_tx(conn):
        conn.execute("INSERT INTO settings (key, value, updated_at, updated_by)"
                     " VALUES (?, '[]', ?, 't')",
                     (approvals.DRAFT_CARDS_KEY, to_ts(clock.now())))  # fmt: skip
    with pytest.raises(EcfError, match=f"ecf approve {sid[:8]}"):
        _approve(conn, clock, sid, f"slack:{ME}")
    assert item_row(conn, sid)["status"] == "awaiting_approval"


# The check at every approval.


def _with_flag(sid: str, conn: sqlite3.Connection) -> str:
    return str(json.loads(item_row(conn, sid)["proposal"]).get("flag_email"))


@pytest.mark.parametrize("who", ["os_user", f"slack:{ME}"])
def test_a_waiting_draft_the_check_refuses_is_rejected_flagged_and_shows_its_code(
    conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch, who: str
) -> None:
    sid = _waiting(conn, clock, FakeMailSource())
    with write_tx(conn):
        conn.execute("INSERT INTO settings (key, value, updated_at, updated_by)"
                     " VALUES (?, '[]', ?, 't')",
                     (approvals.DRAFT_CARDS_KEY, to_ts(clock.now())))  # fmt: skip
    monkeypatch.setattr(outbound_plan, "draft_refusals", _refuses)
    with pytest.raises(PolicyDeniedError, match="credential"):
        _approve(conn, clock, sid, who)
    assert item_row(conn, sid)["status"] == "rejected"
    assert conn.execute("SELECT count(*) FROM grants WHERE stable_id = ? AND status = 'issued'",
                        (sid,)).fetchone()[0] == 0  # fmt: skip
    row = _latest(conn, sid)
    assert row["outcome"] == "refused" and json.loads(row["refuse_codes"]) == ["credential"]
    assert _with_flag(sid, conn) == "queued"
    card = _card(conn, sid)
    assert "credential" in card["text"] and "🔴" in card["text"]
    assert card["buttons"] == []  # nothing to rate or show: the draft was dropped
    assert "Hello" not in card["text"]


def test_a_stepup_draft_the_check_refuses_is_rejected_before_any_stepup(
    conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:

    sid = _waiting(conn, clock, FakeMailSource())
    items.transition(conn, clock, StableId(sid), Status.AWAITING_STEPUP,
                     TransitionContext(origin=Origin.APPROVAL), actor="t")  # fmt: skip
    monkeypatch.setattr(outbound_plan, "draft_refusals", _refuses)
    with pytest.raises(PolicyDeniedError, match="credential"):
        _approve(conn, clock, sid)
    assert item_row(conn, sid)["status"] == "rejected"


def test_a_policy_refusal_at_approval_counts_too(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    sid = _waiting(conn, clock, FakeMailSource())
    facts = FACTS | {"triggers": {"fraud": ["bank details"]}}
    with write_tx(conn):
        conn.execute("UPDATE items SET facts = ? WHERE stable_id = ?", (json.dumps(facts), sid))
    with pytest.raises(PolicyDeniedError, match="fraud"):
        _approve(conn, clock, sid)
    assert item_row(conn, sid)["status"] == "rejected"


def test_a_draft_with_no_excerpt_row_is_refused_unchecked(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    sid = _waiting(conn, clock, FakeMailSource())
    with write_tx(conn):
        conn.execute("DELETE FROM excerpts WHERE stable_id = ?", (sid,))
    with pytest.raises(PolicyDeniedError, match="unchecked"):
        _approve(conn, clock, sid)


def test_an_item_with_no_language_fact_skips_only_the_language_clause(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    facts = {k: v for k, v in FACTS.items() if k != "language"} | {"language": "en"}
    sid = _waiting(conn, clock, FakeMailSource(), facts=facts)
    stripped = {k: v for k, v in FACTS.items() if k != "language"}
    with write_tx(conn):
        conn.execute("UPDATE items SET facts = ? WHERE stable_id = ?", (json.dumps(stripped), sid))
    assert _approve(conn, clock, sid)["status"] == "executing"


def test_an_item_whose_language_is_not_english_is_refused(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    sid = _waiting(conn, clock, FakeMailSource())
    with write_tx(conn):
        conn.execute("UPDATE items SET facts = ? WHERE stable_id = ?",
                     (json.dumps(FACTS | {"language": "other"}), sid))  # fmt: skip
    with pytest.raises(PolicyDeniedError, match="language_other"):
        _approve(conn, clock, sid)


def test_a_passing_draft_records_the_checker_on_the_approval(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    sid = _waiting(conn, clock, FakeMailSource())
    _approve(conn, clock, sid)
    assert _audit(conn, "approval.approved")[-1]["checker"] == draftcheck.digest()[:16]


def test_a_queued_answer_is_refused_before_the_draft_check(
    conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = _waiting(conn, clock, FakeMailSource())
    doc = json.loads(item_row(conn, sid)["proposal"]) | {"answer_pending": "yes"}
    with write_tx(conn):
        conn.execute("UPDATE items SET proposal = ? WHERE stable_id = ?", (json.dumps(doc), sid))
    items.transition(conn, clock, StableId(sid), Status.AWAITING_STEPUP,
                     TransitionContext(origin=Origin.APPROVAL), actor="t")  # fmt: skip

    def never(*_a: object) -> tuple[str, ...]:
        raise AssertionError("the draft check ran first")

    monkeypatch.setattr(outbound_plan, "draft_refusals", never)
    with pytest.raises(ConflictError, match="answer"):
        _approve(conn, clock, sid)


def _expire_twice(conn: sqlite3.Connection, clock: FakeClock, sid: str) -> None:
    for _ in range(2):
        clock.advance(15 * 86400)
        approvals.expire(conn, clock)
    assert item_row(conn, sid)["status"] == "expired"


def test_an_expired_draft_the_check_refuses_stays_expired_with_its_code(
    conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = _waiting(conn, clock, FakeMailSource())
    _expire_twice(conn, clock, sid)
    before = len(_posts(conn))
    assert "checks the draft and offers it again if it passes" in _card(conn, sid)["status"]
    monkeypatch.setattr(outbound_plan, "draft_refusals", _refuses)
    with pytest.raises(PolicyDeniedError, match="credential"):
        _approve(conn, clock, sid)
    assert item_row(conn, sid)["status"] == "expired"
    assert _audit(conn, "approval.draft_refused")[-1]["codes"] == ["credential"]
    assert "credential" in _card(conn, sid)["status"]
    assert all(p["card"]["title"] != "Offered again" for p in _posts(conn)[before:] if "card" in p)
    assert inbox.show(conn, sid)["draft"]["codes"] == ["credential"]


def test_an_expired_draft_that_passes_is_offered_again_and_approved(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    sid = _waiting(conn, clock, FakeMailSource())
    _expire_twice(conn, clock, sid)
    assert _approve(conn, clock, sid)["status"] == "executing"


def test_codes_from_an_earlier_refusal_go_when_it_is_offered_again(
    conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = _waiting(conn, clock, FakeMailSource())
    _expire_twice(conn, clock, sid)
    with monkeypatch.context() as m:
        m.setattr(outbound_plan, "draft_refusals", _refuses)
        with pytest.raises(PolicyDeniedError):
            _approve(conn, clock, sid)
    _approve(conn, clock, sid)
    assert json.loads(_latest(conn, sid)["refuse_codes"]) == []


def test_a_slack_click_on_an_expired_draft_is_refused_at_the_status_check(
    conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = _waiting(conn, clock, FakeMailSource())
    _expire_twice(conn, clock, sid)

    def never(*_a: object) -> tuple[str, ...]:
        raise AssertionError("no draft check for a refused status")

    monkeypatch.setattr(outbound_plan, "draft_refusals", never)
    with pytest.raises(ConflictError, match="expired"):
        _approve(conn, clock, sid, f"slack:{ME}")


def test_the_save_job_refuses_a_failing_draft_before_any_imap_write(
    conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = FakeMailSource()
    sid = _waiting(conn, clock, src)
    _approve(conn, clock, sid)
    monkeypatch.setattr(outbound_plan, "draft_refusals", _refuses)
    assert _run(conn, clock, src) == 1
    assert item_row(conn, sid)["status"] == "failed"
    assert not src.elsewhere.get("Drafts")
    assert "credential" in _card(conn, sid)["status"]
    row = _latest(conn, sid)
    assert row["outcome"] == "refused" and json.loads(row["refuse_codes"]) == ["credential"]


def test_the_save_job_applies_the_policy_check_too(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    src = FakeMailSource()
    sid = _waiting(conn, clock, src)
    _approve(conn, clock, sid)
    facts = FACTS | {"triggers": {"regulator": ["x"]}}
    with write_tx(conn):
        conn.execute("UPDATE items SET facts = ? WHERE stable_id = ?", (json.dumps(facts), sid))
    _run(conn, clock, src)
    assert item_row(conn, sid)["status"] == "failed" and not src.elsewhere.get("Drafts")


def test_requeue_of_a_refused_draft_fails_again_before_any_imap_write(
    conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = FakeMailSource()
    sid = _waiting(conn, clock, src)
    _approve(conn, clock, sid)
    monkeypatch.setattr(outbound_plan, "draft_refusals", _refuses)
    _run(conn, clock, src)
    approvals.requeue(conn, clock, sid, actor="os_user", nonce=None)
    _run(conn, clock, src)
    assert item_row(conn, sid)["status"] == "failed" and not src.elsewhere.get("Drafts")


# The card after a decision, and ratings.


def test_the_saved_card_asks_for_a_rating(conn: sqlite3.Connection, clock: FakeClock) -> None:
    sid = _saved(conn, clock, FakeMailSource())
    card = _card(conn, sid)
    assert card["title"] == "news@vendor-a.example · s"
    assert card["text"].split("\n") == ["🔵 Draft saved · label invoice", "How was this draft?"]
    assert _labels(card) == ["👍", "👎", "Delete from Drafts", "Show draft"]


def test_a_saved_draft_with_other_actions_says_so(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    src = FakeMailSource()
    sid = _waiting(conn, clock, src)
    doc = json.loads(item_row(conn, sid)["proposal"])
    assert any(a["name"] == "draft_reply" for a in doc["actions"])
    card = draft_cards.decided(item_row(conn, sid), _latest(conn, sid), also="label invoice")
    assert "label invoice" in card.text.split("\n")[0]


def test_a_thumbs_up_is_recorded_and_the_card_says_so(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    sid = _saved(conn, clock, FakeMailSource())
    _handle(conn, clock, _card(conn, sid), "👍")
    assert _latest(conn, sid)["rating"] == "up"
    card = _card(conn, sid)
    assert card["text"].split("\n")[1] == "You rated this draft 👍. Tap to change it."
    assert _labels(card) == ["👍", "👎", "Delete from Drafts", "Show draft"]


def _form(conn: sqlite3.Connection, ref: str) -> dict[str, Any]:
    return draft_actions.reason_form(conn, Click("button", draft_cards.RATE_DOWN, ref, "CAP", ME))


def test_the_thumbs_down_window_offers_every_reason_and_no_note_box(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    sid = _saved(conn, clock, FakeMailSource())
    form = _form(conn, sid)
    inputs = [b for b in form["blocks"] if b["type"] == "input"]
    assert len(inputs) == 1 and inputs[0]["element"]["type"] == "radio_buttons"
    options = inputs[0]["element"]["options"]
    assert {o["value"] for o in options} == draft_data.REASONS
    # docs.slack.dev, fetched 2026-11-01: 10 options; 75 characters; modal title and buttons 24
    assert len(options) <= 10 and all(len(o["text"]["text"]) <= 75 for o in options)
    assert all(len(form[k]["text"]) <= 24 for k in ("title", "submit", "close"))
    assert form["callback_id"] == draft_cards.RATE_REASON and form["private_metadata"] == sid


def test_a_reason_is_recorded_and_promised_raises_its_alert(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    sid = _saved(conn, clock, FakeMailSource())
    click = Click("form", draft_cards.RATE_REASON, sid, None, ME, {"reason": draft_data.PROMISED})
    slack_in.HANDLERS[draft_cards.RATE_REASON](conn, clock, click)
    row = _latest(conn, sid)
    assert (row["rating"], row["reason"], row["note_chars"]) == ("down", draft_data.PROMISED, 0)
    assert row["alerted"]
    assert _card(conn, sid)["text"].split("\n")[1] == "You rated this draft 👎. Tap to change it."


def test_changing_a_rating_clears_the_reason(conn: sqlite3.Connection, clock: FakeClock) -> None:
    sid = _saved(conn, clock, FakeMailSource())
    click = Click("form", draft_cards.RATE_REASON, sid, None, ME, {"reason": "tone"})
    slack_in.HANDLERS[draft_cards.RATE_REASON](conn, clock, click)
    _handle(conn, clock, _card(conn, sid), "👍")
    row = _latest(conn, sid)
    assert (row["rating"], row["reason"]) == ("up", None)


# Delete, before and after approval, and Reject.


def test_delete_before_approval_rejects_voids_flags_and_counts_as_deleted(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    src = FakeMailSource()
    sid = _waiting(conn, clock, src)
    _handle(conn, clock, _card(conn, sid), "Delete draft")
    assert item_row(conn, sid)["status"] == "rejected"
    assert conn.execute("SELECT count(*) FROM grants WHERE stable_id = ? AND status = 'issued'",
                        (sid,)).fetchone()[0] == 0  # fmt: skip
    assert _latest(conn, sid)["outcome"] == "deleted"
    card = _card(conn, sid)
    assert card["text"].split("\n") == ["🔴 Deleted", "How was this draft?"]
    assert _labels(card) == ["👍", "👎", "Show draft"]
    assert _with_flag(sid, conn) == "queued"
    assert _deletes(conn, clock, src) == 1
    [flags] = src.flags(src.uids_after(0)).values()
    assert "\\Flagged" in flags
    assert _with_flag(sid, conn) == "done"
    assert _deletes(conn, clock, src) == 0


def test_delete_needs_a_waiting_item(conn: sqlite3.Connection, clock: FakeClock) -> None:
    sid = _waiting(conn, clock, FakeMailSource())
    card = _card(conn, sid)
    approvals.reject(conn, clock, sid, actor="os_user")
    _handle(conn, clock, card, "Delete draft")  # says why, to you alone
    assert _latest(conn, sid)["outcome"] == "rejected"
    assert _posts(conn)[-1]["op"] == "ephemeral" and "Not done" in _posts(conn)[-1]["text"]


def _deleted(conn: sqlite3.Connection, clock: FakeClock, src: FakeMailSource) -> str:
    sid = _waiting(conn, clock, src)
    _handle(conn, clock, _card(conn, sid), "Delete draft")
    return sid


def _email_flagged(src: FakeMailSource) -> bool:
    [flags] = src.flags(src.uids_after(0)).values()
    return "\\Flagged" in flags


def test_the_queued_flag_waits_while_the_address_is_paused(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    src = FakeMailSource()
    sid = _deleted(conn, clock, src)
    with write_tx(conn):
        conn.execute("UPDATE addresses SET paused = 1 WHERE address_id = 'ap'")
    assert pause.is_paused(conn, "ap")
    assert _deletes(conn, clock, src) == 0
    assert not _email_flagged(src) and _with_flag(sid, conn) == "queued"


def test_the_queued_flag_is_dropped_in_shadow(conn: sqlite3.Connection, clock: FakeClock) -> None:
    src = FakeMailSource()
    sid = _deleted(conn, clock, src)
    with write_tx(conn):
        conn.execute("UPDATE addresses SET stage = 'shadow' WHERE address_id = 'ap'")
    _deletes(conn, clock, src)
    assert not _email_flagged(src) and _with_flag(sid, conn) == "failed"
    assert f"item {sid[:8]}" in _posts(conn)[-1]["text"]


def test_the_queued_flag_leaves_a_changed_message_alone(
    conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = FakeMailSource()
    sid = _deleted(conn, clock, src)

    def changed(*_a: object) -> int:
        raise mail_actions.MessageChangedError("changed")

    monkeypatch.setattr(mail_actions, "verify", changed)
    _deletes(conn, clock, src)
    assert not _email_flagged(src) and _with_flag(sid, conn) == "failed"


def test_a_mailbox_error_leaves_the_work_queued_for_the_next_check(
    conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = FakeMailSource()
    sid = _deleted(conn, clock, src)

    def dropped(*_a: object) -> int:
        raise OSError("connection dropped")

    monkeypatch.setattr(mail_actions, "verify", dropped)
    assert _deletes(conn, clock, src) == 0
    assert _with_flag(sid, conn) == "queued"
    for _ in range(draft_actions.TRIES - 1):
        _deletes(conn, clock, src)
    assert _with_flag(sid, conn) == "failed"
    assert f"item {sid[:8]}" in _posts(conn)[-1]["text"]


def test_a_failing_item_doesnt_block_the_next_one(
    conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = FakeMailSource()
    first = _deleted(conn, clock, src)
    second = _waiting(conn, clock, src, n=1, setup=False)
    _handle(conn, clock, _card(conn, second), "Delete draft")
    real = mail_actions.verify

    def fails_first(s: Any, item: sqlite3.Row, n: int) -> int:
        if item["stable_id"] == first:
            raise OSError("connection dropped")
        return real(s, item, n)

    monkeypatch.setattr(mail_actions, "verify", fails_first)
    assert _deletes(conn, clock, src) == 1
    assert (_with_flag(first, conn), _with_flag(second, conn)) == ("queued", "done")


def test_reject_shows_rejected_and_queues_no_flag(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    sid = _waiting(conn, clock, FakeMailSource())
    approvals.reject(conn, clock, sid, actor="os_user")
    card = _card(conn, sid)
    assert card["text"].split("\n") == ["🔴 Rejected", "How was this draft?"]
    assert _labels(card) == ["👍", "👎", "Show draft"]
    assert _with_flag(sid, conn) == "None"


def test_delete_from_drafts_removes_only_the_draft(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    src = FakeMailSource()
    sid = _saved(conn, clock, src)
    assert src.elsewhere.get("Drafts")
    _handle(conn, clock, _card(conn, sid), "Delete from Drafts")
    assert src.elsewhere.get("Drafts")  # at the next check, never on Slack's thread
    assert _deletes(conn, clock, src) == 1
    assert not src.elsewhere.get("Drafts")
    assert item_row(conn, sid)["status"] == "executed"
    assert _latest(conn, sid)["outcome"] == "deleted_unsent"  # it was saved: it counts as saved
    doc = json.loads(item_row(conn, sid)["proposal"])
    assert all(r["name"] != "draft_reply" for r in doc["done"])
    card = _card(conn, sid)
    assert card["text"].split("\n")[0] == "🔴 Deleted · label invoice"  # the label stays
    assert _labels(card) == ["👍", "👎", "Show draft"]


def test_delete_from_drafts_leaves_an_edited_draft_and_says_so(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    src = FakeMailSource()
    sid = _saved(conn, clock, src)
    doc = json.loads(item_row(conn, sid)["proposal"])
    for r in doc["done"]:
        if r["name"] == "draft_reply":
            r["content_hash"] = "other"
    with write_tx(conn):
        conn.execute("UPDATE items SET proposal = ? WHERE stable_id = ?", (json.dumps(doc), sid))
    _handle(conn, clock, _card(conn, sid), "Delete from Drafts")
    _deletes(conn, clock, src)
    assert src.elsewhere.get("Drafts")
    assert _latest(conn, sid)["outcome"] == "saved"
    assert "edited" in _posts(conn)[-1]["text"]
    assert _card(conn, sid)["text"].startswith("🔵 Draft saved")


def test_a_draft_tracking_lost_sight_of_isnt_called_deleted(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    """A sent draft can read `deleted_unsent`: only ecf's own Delete says Deleted."""
    sid = _saved(conn, clock, FakeMailSource())
    with write_tx(conn):
        conn.execute("UPDATE draft_versions SET outcome = 'deleted_unsent' WHERE stable_id = ?",
                     (sid,))  # fmt: skip
    _handle(conn, clock, _card(conn, sid), "👍")
    assert _card(conn, sid)["text"].startswith("🔵 Draft saved")


def test_a_second_delete_gets_its_own_tries(conn: sqlite3.Connection, clock: FakeClock) -> None:
    src = FakeMailSource()
    sid = _saved(conn, clock, src)
    doc = json.loads(item_row(conn, sid)["proposal"]) | {"queued_tries": 5}
    with write_tx(conn):
        conn.execute("UPDATE items SET proposal = ? WHERE stable_id = ?", (json.dumps(doc), sid))
    _handle(conn, clock, _card(conn, sid), "Delete from Drafts")
    assert json.loads(item_row(conn, sid)["proposal"])["queued_tries"] == 0


def test_a_failed_delete_is_given_up_and_counted_per_check(
    conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = FakeMailSource()
    sid = _saved(conn, clock, src)
    _handle(conn, clock, _card(conn, sid), "Delete from Drafts")

    def renamed(*_a: object) -> str:
        raise OSError("no such folder")

    monkeypatch.setattr(mailbox_actions, "delete_draft", renamed)
    for _ in range(draft_actions.TRIES):
        _deletes(conn, clock, src)
    assert json.loads(item_row(conn, sid)["proposal"])["delete_draft"] == "failed"
    assert _latest(conn, sid)["outcome"] == "saved"


def test_a_slow_mailbox_leaves_the_rest_for_the_next_check(
    conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = FakeMailSource()
    first = _deleted(conn, clock, src)
    second = _waiting(conn, clock, src, n=1, setup=False)
    _handle(conn, clock, _card(conn, second), "Delete draft")
    ticks = iter([0.0, 1.0, draft_actions.BUDGET_S + 2])
    monkeypatch.setattr(clock, "monotonic", lambda: next(ticks))
    assert _deletes(conn, clock, src) == 1
    assert (_with_flag(first, conn), _with_flag(second, conn)) == ("done", "queued")


def test_a_refusal_at_approval_is_not_a_decision_time(
    conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = _waiting(conn, clock, FakeMailSource())
    monkeypatch.setattr(outbound_plan, "draft_refusals", _refuses)
    with pytest.raises(PolicyDeniedError):
        _approve(conn, clock, sid)
    assert _latest(conn, sid)["decided_after_s"] is None


def test_a_thumbs_down_that_is_refused_says_why(conn: sqlite3.Connection, clock: FakeClock) -> None:
    sid = _waiting(conn, clock, FakeMailSource())  # not decided yet: nothing to rate
    click = Click("form", draft_cards.RATE_REASON, sid, None, ME, {"reason": "tone"})
    with pytest.raises(EcfError):
        slack_in.HANDLERS[draft_cards.RATE_REASON](conn, clock, click)
    assert _posts(conn)[-1]["text"].startswith("Not done:")


def test_delete_from_drafts_needs_a_saved_draft(conn: sqlite3.Connection, clock: FakeClock) -> None:
    sid = _saved(conn, clock, FakeMailSource())
    card = _card(conn, sid)
    with write_tx(conn):
        conn.execute("UPDATE draft_versions SET outcome = 'sent_unchanged' WHERE stable_id = ?",
                     (sid,))  # fmt: skip
    _handle(conn, clock, card, "Delete from Drafts")
    assert "Not done" in _posts(conn)[-1]["text"]
    assert json.loads(item_row(conn, sid)["proposal"]).get("delete_draft") is None


# Show draft.


def test_show_draft_sends_blocks_only_you_see_with_fixed_notification_text(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    sid = _saved(conn, clock, FakeMailSource())
    _handle(conn, clock, _card(conn, sid), "Show draft")
    p = _posts(conn)[-1]
    assert p["op"] == "ephemeral" and p["user"] == ME and p["text"] == "A draft from ecf"
    assert p["card"]["title"] == ""
    assert p["card"]["text"].startswith(f"{draft_cards.LABEL}\n{draft_cards.NOTE}\n│ Hello,")
    assert _audit(conn, "draft.shown")


def test_the_ephemeral_with_a_card_reaches_chat_as_blocks(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    from ecf_server.chat import RouteRef  # noqa: PLC0415

    chat = FakeChat()
    slack_out.enqueue_ephemeral(conn, clock, route=RouteRef("CAP"), user=ME, text="A draft",
                                card=Card("", text="│ x"))  # fmt: skip
    p = _posts(conn)[-1]
    chat.ephemeral(RouteRef("CAP"), ME, p["text"], slack_out._card(p["card"]))  # pyright: ignore[reportPrivateUsage]
    assert chat.posts[-1]["card"]["text"] == "│ x"


def test_show_draft_of_a_long_draft_points_at_the_computer(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    sid = _waiting(conn, clock, FakeMailSource(), text="\n".join(f"line {i}" for i in range(34)))
    approvals.reject(conn, clock, sid, actor="os_user")
    _handle(conn, clock, _card(conn, sid), "Show draft")
    p = _posts(conn)[-1]
    assert p["text"] == f"Too long to show here: ecf item show {sid[:8]}" and "card" not in p


def test_show_draft_of_a_refused_draft_shows_no_text(
    conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = _waiting(conn, clock, FakeMailSource())
    monkeypatch.setattr(outbound_plan, "draft_refusals", _refuses)
    with pytest.raises(PolicyDeniedError):
        _approve(conn, clock, sid)
    click = Click("button", draft_cards.SHOW_DRAFT, sid, "CAP", ME)
    slack_in.HANDLERS[draft_cards.SHOW_DRAFT](conn, clock, click)
    p = _posts(conn)[-1]
    assert "card" not in p and "Hello" not in p["text"] and "credential" in p["text"]


# The redraw at upgrade.


def test_the_redraw_keeps_grants_and_buttons_at_most_twenty_a_tick(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    src = FakeMailSource()
    sids = [_waiting(conn, clock, src, n=n, setup=n == 0) for n in range(25)]
    grants = {s: approvals._latest_grant(conn, s) for s in sids}  # pyright: ignore[reportPrivateUsage]
    assert approvals.redraw_draft_cards(conn, clock) == 20
    assert approvals.redraw_draft_cards(conn, clock) == 5
    assert approvals.redraw_draft_cards(conn, clock) == 0
    assert conn.execute("SELECT 1 FROM settings WHERE key = ?",
                        (approvals.REDRAW_KEY,)).fetchone()  # fmt: skip
    assert grants == {s: approvals._latest_grant(conn, s) for s in sids}  # pyright: ignore[reportPrivateUsage]
    assert _labels(_card(conn, sids[0])) == ["Save to Drafts", "Delete draft", "Show excerpt"]


def test_the_redraw_leaves_a_queued_card_with_reject_only(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:

    sid = _waiting(conn, clock, FakeMailSource())
    items.transition(conn, clock, StableId(sid), Status.AWAITING_STEPUP,
                     TransitionContext(origin=Origin.APPROVAL), actor="t")  # fmt: skip
    assert approvals.redraw_draft_cards(conn, clock) == 1
    card = _card(conn, sid)
    assert _labels(card) == ["Reject"]
    assert card["text"].startswith(draft_cards.LABEL) and card["status"].startswith("Queued")
    assert "Saving it also does: label invoice" in card["status"]


def test_the_redraw_runs_once_on_a_fresh_install(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    assert approvals.redraw_draft_cards(conn, clock) == 0
    assert conn.execute("SELECT 1 FROM settings WHERE key = ?",
                        (approvals.REDRAW_KEY,)).fetchone()  # fmt: skip


# The CLI: the note before a draft, and a decided draft under its own heading.


def _cli_item(outcome: str | None, codes: tuple[str, ...] = ()) -> dict[str, Any]:
    action = {"mode": "approve", "name": "draft_reply", "target": None,
              "payload": {"to": "ann@example.com", "text": "Hello,\n\n[Your Name]"}}  # fmt: skip
    proposal = {"actions": [action], "plan": {"actions": [action]}}
    return {"id": "f" * 64, "address_id": "ap", "sender": "Ann <ann@example.com>",
            "subject": "Hello", "proposal": proposal,
            "draft": {"outcome": outcome, "codes": list(codes)}}  # fmt: skip


def test_the_cli_note_is_the_cards_note() -> None:
    assert cli_items.DRAFT_NOTE == draft_cards.NOTE


def test_a_waiting_draft_shows_the_note_before_its_marked_lines() -> None:
    lines = cli_items.proposal_lines(_cli_item(None))
    at = lines.index(f"    {cli_items.DRAFT_NOTE}")
    assert lines[at + 1 : at + 4] == ["    | Hello,", "    | ", "    | [Your Name]"]
    assert "proposed:" in lines


@pytest.mark.parametrize("outcome", ["saved", "rejected", "deleted", "sent_edited"])
def test_a_decided_draft_is_printed_under_its_own_heading(outcome: str) -> None:
    lines = cli_items.proposal_lines(_cli_item(outcome))
    assert "proposed:" not in lines and not any("needs your approval" in x for x in lines)
    assert f"draft, {outcome}:" in lines
    assert f"    {cli_items.DRAFT_NOTE}" in lines and "    | Hello," in lines


def test_a_refused_draft_prints_its_code_and_no_text() -> None:
    lines = cli_items.proposal_lines(_cli_item("refused", ("credential",)))
    assert "draft, refused: credential" in lines
    assert not any("Hello" in x for x in lines)


def test_an_expired_draft_is_still_a_proposal() -> None:
    assert "proposed:" in cli_items.proposal_lines(_cli_item("expired"))


def test_an_expired_draft_the_check_refused_prints_its_code() -> None:
    lines = cli_items.proposal_lines(_cli_item("expired", ("credential",)))
    assert "draft, expired: the draft check refused it (credential)" in lines


class _Client:
    def __init__(self, item: dict[str, Any]) -> None:
        self.item, self.posted = item, 0

    def get(self, _path: str) -> dict[str, Any]:
        return self.item

    def request(self, *_a: object, **_k: object) -> dict[str, Any]:
        self.posted += 1
        return {"status": "executing"}


def test_show_proposal_prints_the_note(capsys: Any) -> None:
    cli_items._show_proposal(_Client(_cli_item(None)), "f" * 8, yes=True)  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage, reportArgumentType]
    assert cli_items.DRAFT_NOTE in capsys.readouterr().out


def test_item_show_prints_a_decided_draft(conn: sqlite3.Connection, clock: FakeClock) -> None:
    sid = _saved(conn, clock, FakeMailSource())
    d = inbox.show(conn, sid)
    assert d["draft"]["outcome"] == "saved"
    lines = cli_items.proposal_lines(d)
    assert "draft, saved:" in lines and f"    {cli_items.DRAFT_NOTE}" in lines
