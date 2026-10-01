"""Review posts (V1.3 step 6a; SPEC §9.2, OD-239): what a post lists, its buttons, Correct, Fix,
"All others correct" and its exclusions, sampling, progress, and the escalations_per_hour cap."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import pytest

from ecf.errors import ConflictError
from ecf_server import decide, escalations, gate, review, settings, slack_in, stages
from ecf_server.clock import FakeClock
from ecf_server.db import write_tx
from ecf_server.slack_in import Click
from tests.test_decide import KNOWN_BULK, MARKETING, item_row, make_classified
from tests.test_digests_daily import ME, MORNING, slack_setup

PAYMENT = MARKETING | {"category": "invoice", "payment_related": True}


@pytest.fixture
def morning(clock: FakeClock) -> FakeClock:
    clock.advance((MORNING - clock.now()).total_seconds())
    return clock


def _items(
    conn: sqlite3.Connection, clock: FakeClock, *classifications: dict[str, Any]
) -> list[str]:
    sids: list[str] = []
    for n, cls in enumerate(classifications):
        sid = make_classified(conn, clock, cls, KNOWN_BULK, sid=f"{n:02x}")
        decide.apply(conn, clock, sid)
        sids.append(sid)
    return sids


def _posts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT payload FROM jobs WHERE queue = 'slack_out' ORDER BY rowid")
    return [json.loads(r[0]) for r in rows]


def _click(conn: sqlite3.Connection, clock: FakeClock, action: str, ref: str,
           values: dict[str, str] | None = None) -> None:  # fmt: skip
    kind = "form" if values is not None else "button"
    slack_in.HANDLERS[action](conn, clock, Click(kind, action, ref, "CAP", ME, values or {}))  # type: ignore[arg-type]


def _review_post(conn: sqlite3.Connection) -> dict[str, Any]:
    [post] = [p for p in _posts(conn) if p["card"]["title"].startswith("Review:")]
    return post


def test_a_post_lists_what_the_model_said_with_the_right_buttons(
    conn: sqlite3.Connection, morning: FakeClock
) -> None:
    slack_setup(conn, morning)
    ok1, ok2, pay = _items(conn, morning, MARKETING, MARKETING, PAYMENT)
    assert review.run(conn, morning) == 1
    card = _review_post(conn)["card"]
    assert card["title"] == "Review: ap (3)"
    assert "marketing, low, fraud none; would: label marketing" in card["text"]
    assert "invoice, low, fraud none, payment" in card["text"]
    assert card["text"].count("(needs its own review)") == 1
    labels = [b["label"] for b in card["buttons"]]
    assert labels == [f"Fix {ok1[:8]}", f"Fix {ok2[:8]}", f"Fix {pay[:8]}", f"Correct {pay[:8]}",
                      "All others correct (2)"]  # fmt: skip
    assert review.run(conn, morning) == 0  # hourly


def test_all_others_correct_never_counts_items_needing_their_own_review(
    conn: sqlite3.Connection, morning: FakeClock
) -> None:
    slack_setup(conn, morning)
    ok1, ok2, pay = _items(conn, morning, MARKETING, MARKETING, PAYMENT)
    review.run(conn, morning)
    [all_ok] = [b for b in _review_post(conn)["card"]["buttons"] if b["action"] == review.ALL_OK]
    _click(conn, morning, review.FIX, ok2, {"category": "notification"})
    _click(conn, morning, review.ALL_OK, all_ok["ref"])
    assert json.loads(item_row(conn, ok1)["review"])["bulk"] is True
    assert json.loads(item_row(conn, ok2)["review"])["verdict"] == "fixed"  # not overwritten
    assert json.loads(item_row(conn, pay)["review"])["verdict"] == "asked"  # still waiting
    with pytest.raises(ConflictError, match="its own review"):
        review.record(conn, morning, pay, actor="t", bulk=True)
    _click(conn, morning, review.CORRECT, pay)
    counts = review.reviewed(conn, "ap")
    assert counts == {"reviewed": 3, "category_ok": 2, "individual": 2, "bulk": 1}


def test_fix_records_the_correction_and_whether_the_category_was_right(
    conn: sqlite3.Connection, morning: FakeClock
) -> None:
    slack_setup(conn, morning)
    a, b = _items(conn, morning, MARKETING, MARKETING)
    _click(conn, morning, review.FIX, a, {"category": "notification", "priority": "low"})
    _click(conn, morning, review.FIX, b, {"priority": "high", "payment_related": "false"})
    ra, rb = json.loads(item_row(conn, a)["review"]), json.loads(item_row(conn, b)["review"])
    assert (ra["verdict"], ra["category_ok"]) == ("fixed", False)
    assert json.loads(item_row(conn, a)["human_correction"]) == {"category": "notification"}
    assert (rb["verdict"], rb["category_ok"]) == ("fixed", True)
    with pytest.raises(ConflictError, match="already reviewed"):
        review.record(conn, morning, a, actor="t")


def test_the_fix_form_offers_the_schema_choices(
    conn: sqlite3.Connection, morning: FakeClock
) -> None:
    slack_setup(conn, morning)
    [a] = _items(conn, morning, MARKETING)
    form = slack_in.FORMS[review.FIX](conn, Click("button", review.FIX, a, "CAP", ME))
    fields = {b["block_id"]: b for b in form["blocks"] if b["type"] == "input"}
    assert set(fields) == set(review.FIXABLE)
    assert fields["category"]["element"]["initial_option"]["value"] == "marketing"
    payload = {
        "type": "view_submission",
        "user": {"id": ME},
        "view": {
            "callback_id": review.FIX,
            "private_metadata": a,
            "state": {"values": {"category": {"v": {"selected_option": {"value": "invoice"}}}}},
        },
    }
    click = slack_in.to_click(payload)
    assert click is not None and click.values == {"category": "invoice"}


def test_after_the_gate_count_only_a_sample_is_asked(
    conn: sqlite3.Connection, morning: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    slack_setup(conn, morning)
    monkeypatch.setitem(review.GATE_COUNT, "standard", 0)
    with write_tx(conn):
        conn.execute("INSERT INTO settings (key, value, updated_at, updated_by)"
                     " VALUES ('review_sample_rate', '0', 't', 't')")  # fmt: skip
    [a] = _items(conn, morning, MARKETING)
    assert review.run(conn, morning) == 0
    assert json.loads(item_row(conn, a)["review"]) == {"verdict": "not_sampled"}


def test_a_post_never_exceeds_slacks_button_limit(
    conn: sqlite3.Connection, morning: FakeClock
) -> None:
    slack_setup(conn, morning)
    _items(conn, morning, *[PAYMENT] * 20)
    review.run(conn, morning)
    card = _review_post(conn)["card"]
    assert len(card["buttons"]) <= review.BUTTONS_MAX
    assert "more in the next post" in card["text"]


def test_stage_status_shows_review_progress(conn: sqlite3.Connection, morning: FakeClock) -> None:
    slack_setup(conn, morning)
    [a, b] = _items(conn, morning, MARKETING, MARKETING)
    with write_tx(conn):  # only reviews of emails classified by the model ecf runs now count
        conn.execute("UPDATE items SET pinned_models = ? WHERE stable_id = ?",
                     (json.dumps({"digest": gate.current_digest()}), a))  # fmt: skip
    review.record(conn, morning, a, actor="t")
    review.record(conn, morning, b, actor="t")
    [row] = stages.status(conn, morning.now())
    assert (
        row["review"] == "1/100 reviewed, 100% accurate, 99 to go (1 one by one, 0 with All"
        " others correct)"
    )


def test_the_new_settings(conn: sqlite3.Connection, morning: FakeClock) -> None:
    from ecf.errors import InvalidInputError  # noqa: PLC0415

    slack_setup(conn, morning)
    settings.set_value(conn, morning, "review_sample_rate", "25", address=None, actor="t")
    settings.set_value(conn, morning, "escalations_per_hour", "5", address="ap", actor="t")
    settings.set_value(conn, morning, "label_folder", "ecf-flagged", address="ap", actor="t")
    with pytest.raises(InvalidInputError, match="INBOX"):
        settings.set_value(conn, morning, "label_folder", "inbox", address="ap", actor="t")
    assert settings.get(conn, "escalations_per_hour", "ap") == 5


# ---- escalations_per_hour ---------------------------------------------------------------------


def test_escalations_over_the_cap_roll_up_but_fraud_never_does(
    conn: sqlite3.Connection, morning: FakeClock
) -> None:
    slack_setup(conn, morning)
    with write_tx(conn):
        conn.execute("UPDATE addresses SET overrides = ? WHERE address_id = 'ap'",
                     (json.dumps({"escalations_per_hour": 2}),))  # fmt: skip
    urgent = MARKETING | {"category": "bug_report", "priority": "urgent"}
    fraud = MARKETING | {"category": "invoice", "fraud_risk": "high", "payment_related": True}
    sids = _items(conn, morning, urgent, urgent, urgent, urgent, fraud)
    assert all(escalations.exempt(item_row(conn, s)) is (s == sids[4]) for s in sids)
    escalations.sweep(conn, morning)
    titles = [p["card"]["title"] for p in _posts(conn)]
    rollups = [t for t in titles if "more escalations this hour" in t]
    assert rollups == ["2 more escalations this hour (over escalations_per_hour)"]
    states = {s: conn.execute("SELECT state, thread_key FROM escalations WHERE stable_id = ?",
                              (s,)).fetchone() for s in sids}  # fmt: skip
    assert states[sids[4]]["state"] == "posted"  # fraud: never held back (OD-212)
    rolled = [s for s in sids if (states[s]["thread_key"] or "").startswith("rollup:")]
    assert len(rolled) == 2 and sids[4] not in rolled
