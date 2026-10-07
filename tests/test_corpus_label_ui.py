"""`ecf eval label --corpus`: blind labelling (SPEC §16.7; R5, R6, R66, R68, R122, R155, R197)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from ecf import cli_corpus_label as ui
from ecf.errors import InvalidInputError
from ecf.eval import corpus_labels as cl
from ecf.schema import load_schema_v1
from ecf_server import corpus_session as cs
from ecf_server.clock import FakeClock
from tests.test_corpus import SECRET, fetch, req, server_with

ANSWERS = ["1", "2", "y", "n", "y", "y", "1", "1"]  # invoice, medium, ..., vendor, none


class FakeClient:
    """Routes the label screen's requests to the service's session code, in-process."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def request(self, method: str, path: str, json: Any = None, *, auth: bool = True,
                timeout: float | None = None) -> Any:  # fmt: skip
        del auth, timeout
        self.calls.append((method, path))
        parts = path.strip("/").split("/")
        if method == "POST" and path == "/v1/corpus/session":
            return cs.open_session(json["path"], json["passphrase"], lambda: 0.0)
        s = cs.get(parts[3], lambda: 0.0)
        if parts[-1] == "keys":
            return {"keys": cs.keys(s)}
        if method == "DELETE":
            return cs.close(parts[3])
        return cs.item(s, int(parts[-1]))


@pytest.fixture
def made(
    conn: sqlite3.Connection, clock: FakeClock, db_path: Path, tmp_path: Path
) -> Iterator[Path]:
    out = tmp_path / "out"
    out.mkdir()
    path, *_ = fetch(conn, clock, db_path.parent, req(out / "c.ecfcorpus", total=3), server_with(3))
    assert path is not None
    yield path
    st = cs.status(lambda: 0.0)
    if st is not None:
        cs.release(str(st["session_id"]))


def script(*answers: str) -> Any:
    queue = list(answers)

    def read(_prompt: str) -> str:
        return queue.pop(0)

    return read


def test_labelling_is_blind_saved_each_time_and_resumes(made: Path) -> None:
    screen: list[str] = []
    c = FakeClient()
    # message 1: reveal, then label every field; message 2: unsure; message 3: quit
    t = ui.label(c, made, SECRET, read=script("r", "", *ANSWERS, "u", "q"), write=screen.append,
                 today=date(2026, 10, 7), shuffle=lambda _x: None)  # fmt: skip
    assert (t.labelled, t.unsure, t.reveals) == (1, 1, 1)
    out = "".join(screen)
    assert out.startswith(ui.ALT_ON) and out.endswith(ui.ALT_OFF)  # left the alternate screen
    assert "Invoice 3" in out and "probabilit" not in out and "got" not in out  # no model output
    assert ("DELETE", c.calls[-1][1]) == c.calls[-1] and cs.status(lambda: 0.0) is None
    labels = cl.load(cl.path_for(made))
    assert len(labels) == 2 and sum(lab.confirmed for lab in labels.values()) == 1
    confirmed = next(lab for lab in labels.values() if lab.confirmed)
    assert confirmed.labels is not None and confirmed.labels["category"] == "invoice"
    assert confirmed.labels["priority"] == "medium" and confirmed.labels["sender_type"] == "vendor"
    # resume: only the one left is offered
    t2 = ui.label(FakeClient(), made, SECRET, read=script("s"), write=screen.append,
                  today=date(2026, 10, 7), shuffle=lambda _x: None)  # fmt: skip
    assert (t2.skipped, len(cl.load(cl.path_for(made)))) == (1, 3)


def test_quitting_in_the_middle_of_a_message_saves_nothing_for_it(made: Path) -> None:
    t = ui.label(FakeClient(), made, SECRET, read=script("", "1", "q"), write=lambda _s: None,
                 today=date(2026, 10, 7), shuffle=lambda _x: None)  # fmt: skip
    assert t.labelled == 0 and not cl.load(cl.path_for(made))


def test_answers_take_numbers_values_or_yes_no() -> None:
    f = load_schema_v1().fields
    assert ui.parse_answer(f["category"], "1") == "invoice"
    assert ui.parse_answer(f["category"], "bug_report") == "bug_report"
    assert ui.parse_answer(f["category"], "99") is None
    assert ui.parse_answer(f["requires_reply"], "Y") is True
    assert ui.parse_answer(f["requires_reply"], "maybe") is None


def test_the_screen_hides_injection_text_until_revealed() -> None:
    item = {"index": 4, "display": {"from": "a <a@x.example>", "reply_to": [], "to": ["b@y"],
            "subject": "Hi\x1b[31m", "date": "", "attachments": []},
            "excerpt": "Pay me.\n[text removed]", "unredacted": "Pay me.\nNote to the assistant",
            "facts": {"auth_result": "none"}, "keywords": {}, "triggers": {}}  # fmt: skip
    hidden = "\n".join(ui.render(item, more=False, reveal=False))
    shown = "\n".join(ui.render(item, more=True, reveal=True))
    assert "assistant" not in hidden and "assistant" in shown and "\x1b" not in hidden


def test_each_field_is_shown_with_its_meaning_and_numbered_choices() -> None:
    """Operator feedback during test 1: the one-line prompt was too terse."""
    f = load_schema_v1().fields
    cat = "\n".join(ui.field_block(f["category"], 1, 8))
    assert "Field 1 of 8: category" in cat and "What this email is primarily about." in cat
    assert "12  notification" in cat and "account activity" in cat
    assert "Type the number or the name." in cat
    yn = "\n".join(ui.field_block(f["requires_reply"], 4, 8))
    assert "y  yes" in yn and "Type y or n." in yn


def test_again_and_marked_offer_messages_a_second_time(made: Path) -> None:
    """Operator request during test 1: relabel a message, or go back to skipped and unsure ones."""
    first = ui.label(FakeClient(), made, SECRET, read=script("", *ANSWERS, "s", "u"),
                     write=lambda _s: None, today=date(2026, 10, 7),
                     shuffle=lambda _x: None)  # fmt: skip
    assert (first.labelled, first.skipped, first.unsure) == (1, 1, 1)
    screen: list[str] = []
    back = ui.label(FakeClient(), made, SECRET, read=script(*["", *ANSWERS] * 2),
                    write=screen.append, today=date(2026, 10, 8), marked=True,
                    shuffle=lambda _x: None)  # fmt: skip
    assert back.labelled == 2 and "relabelling; was skipped" in "".join(screen)
    labels = cl.load(cl.path_for(made))
    assert all(lab.confirmed for lab in labels.values())
    changed = [*ANSWERS[:6], "2", "4"]  # sender_type customer, fraud_risk high
    ui.label(FakeClient(), made, SECRET, read=script("", *changed), write=lambda _s: None,
             today=date(2026, 10, 9), again=frozenset({3}), shuffle=lambda _x: None)  # fmt: skip
    after = cl.load(cl.path_for(made))
    three = cs_key(made, 3)
    relabelled = after[three].labels
    assert relabelled is not None and relabelled["fraud_risk"] == "high"
    with pytest.raises(InvalidInputError, match="no message 9"):
        ui.label(FakeClient(), made, SECRET, read=script(), write=lambda _s: None,
                 today=date(2026, 10, 9), again=frozenset({9}))  # fmt: skip


def cs_key(corpus: Path, index: int) -> cl.Key:
    opened = cs.open_session(str(corpus), SECRET, lambda: 0.0)
    try:
        row = cs.get(opened["session_id"], lambda: 0.0).rows[index - 1]
        return (str(row["key"]["content_hash"]), str(row["key"]["identity_digest"]))
    finally:
        cs.release(opened["session_id"])
