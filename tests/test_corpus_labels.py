"""Corpus labels and the decrypted-corpus session (SPEC §16.7; R28, R46, R68, R86, R90, R104,
R141, R146, R154)."""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from ecf.errors import ConflictError, InvalidInputError, NotFoundError
from ecf.eval import corpus_labels as cl
from ecf.schema import extend_schema, load_schema
from ecf_server.clock import FakeClock
from tests.test_claude_review import EXT

KEY = ("a" * 64, "b" * 16)
VALUES: dict[str, Any] = {"category": "invoice", "priority": "medium", "requires_action": True,
                          "requires_reply": False, "payment_related": True,
                          "deadline_mentioned": True, "sender_type": "vendor",
                          "fraud_risk": "none"}  # fmt: skip


def test_a_label_is_every_field_or_a_mark() -> None:
    lab = cl.make(KEY, "c1", VALUES, None, date(2026, 10, 7))
    assert lab.confirmed and lab.row()["author"] == "operator"
    assert not cl.make(KEY, "c1", None, "u", date(2026, 10, 7)).confirmed
    with pytest.raises(InvalidInputError, match="exactly the schema"):
        cl.make(KEY, "c1", {"category": "invoice"}, None, date(2026, 10, 7))
    with pytest.raises(InvalidInputError, match="isn't one of"):
        cl.make(KEY, "c1", VALUES | {"priority": "soon"}, None, date(2026, 10, 7))
    with pytest.raises(InvalidInputError, match="values or a mark"):
        cl.make(KEY, "c1", VALUES, "s", date(2026, 10, 7))


def test_a_v1_label_file_reads_staff_as_team(tmp_path: Path) -> None:
    path = cl.path_for(tmp_path / "c.ecfcorpus")
    row = {"key": {"content_hash": KEY[0], "identity_digest": KEY[1]}, "corpus_id": "c1",
           "labels": VALUES | {"sender_type": "staff"}, "mark": None, "confirmed": True,
           "author": "operator", "date": "2026-10-07"}  # fmt: skip
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    labels = cl.load(path)[KEY].labels
    assert labels is not None and labels["sender_type"] == "team"
    with pytest.raises(InvalidInputError, match="isn't one of"):  # only on load, not new labels
        cl.make(KEY, "c1", VALUES | {"sender_type": "staff"}, None, date(2026, 10, 8))


def test_extension_fields_are_optional_and_unknown_fields_refused(tmp_path: Path) -> None:
    schema = extend_schema(load_schema(), EXT)
    assert cl.check_values(VALUES, schema) == VALUES  # a label made before the extension
    full = VALUES | {"category": "legal_notice", "contract_stage": "draft",
                     "lawyer_involved": False}  # fmt: skip
    assert cl.check_values(full, schema) == full
    assert cl.check_values(VALUES | {"lawyer_involved": True}, schema)["lawyer_involved"] is True
    with pytest.raises(InvalidInputError, match="exactly the schema"):
        cl.check_values(VALUES | {"mood": "calm"}, schema)  # unknown field
    with pytest.raises(InvalidInputError, match="exactly the schema"):
        cl.check_values({k: v for k, v in full.items() if k != "priority"}, schema)  # base field
    with pytest.raises(InvalidInputError, match="isn't one of"):
        cl.check_values(VALUES | {"contract_stage": "signed"}, schema)
    with pytest.raises(InvalidInputError, match="exactly the schema"):
        cl.check_values(full, load_schema())  # extension fields without the extension
    # a file written before the extension loads under it; one using it needs it
    path = cl.path_for(tmp_path / "c.ecfcorpus")
    labels: dict[cl.Key, cl.Label] = {}
    cl.put(labels, cl.make(KEY, "c1", VALUES, None, date(2026, 10, 7)))
    cl.save(path, labels)
    assert cl.load(path, schema)[KEY].confirmed
    cl.put(labels, cl.make(KEY, "c1", full, None, date(2026, 10, 8), schema))
    cl.save(path, labels)
    assert cl.load(path, schema)[KEY].labels == full
    with pytest.raises(InvalidInputError, match="exactly the schema"):
        cl.load(path)


def test_the_file_is_sorted_0600_and_its_hash_moves_only_with_labels(tmp_path: Path) -> None:
    path = cl.path_for(tmp_path / "c.ecfcorpus")
    labels: dict[cl.Key, cl.Label] = {}
    other = ("0" * 64, "1" * 16)
    cl.put(labels, cl.make(KEY, "c1", VALUES, None, date(2026, 10, 7)))
    cl.put(labels, cl.make(other, "c1", None, "s", date(2026, 10, 7)))
    h1 = cl.save(path, labels)
    assert path.stat().st_mode & 0o777 == 0o600 and h1 == cl.file_hash(path)
    rows = [json.loads(x) for x in path.read_text().splitlines()]
    assert [r["key"]["content_hash"][0] for r in rows] == ["0", "a"]  # sorted by key
    again = cl.load(path)
    assert not cl.put(again, cl.make(KEY, "c1", VALUES, None, date(2026, 10, 9)))  # same label
    assert cl.save(path, again) == h1  # no date bump, same hash (R146)
    assert cl.put(again, cl.make(KEY, "c1", VALUES | {"priority": "high"}, None, date(2026, 10, 9)))
    assert cl.save(path, again) != h1
    assert "Invoice" not in path.read_text()  # labels only, nothing from the text
    path.write_text("not json\n")
    with pytest.raises(InvalidInputError, match="line 1"):
        cl.load(path)


def test_the_session_holds_one_corpus_and_expires_when_idle(
    tmp_path: Path, conn: sqlite3.Connection, clock: FakeClock, db_path: Path
) -> None:
    from ecf_server import corpus_session as cs  # noqa: PLC0415
    from tests.test_corpus import SECRET, fetch, req, server_with  # noqa: PLC0415

    out = tmp_path / "out"
    out.mkdir()
    path, *_ = fetch(conn, clock, db_path.parent, req(out / "c.ecfcorpus", total=2), server_with(2))
    assert path is not None
    now = [100.0]
    try:
        with pytest.raises(InvalidInputError, match="wrong passphrase"):
            cs.open_session(str(path), "not it", lambda: now[0])
        got = cs.open_session(str(path), SECRET, lambda: now[0])
        assert got["count"] == 2
        with pytest.raises(ConflictError, match="another corpus is open"):
            cs.open_session(str(path), SECRET, lambda: now[0])
        s = cs.get(got["session_id"], lambda: now[0])
        keys = cs.keys(s)
        assert [k["index"] for k in keys] == [1, 2]
        shown = cs.item(s, 1)
        assert shown["display"]["subject"].startswith("Invoice") and "excerpt" in shown
        assert "auth_result" in shown["facts"] and "probabilities" not in shown
        cs.set_busy(got["session_id"], True)
        with pytest.raises(ConflictError, match="eval run is using"):
            cs.close(got["session_id"])
        assert cs.close(got["session_id"], stop=True) == {"closed": False, "stopping": True}
        now[0] += cs.IDLE_S + 1
        assert not cs.expire_idle(now[0])  # busy: kept
        cs.set_busy(got["session_id"], False)
        assert cs.expire_idle(now[0])
        with pytest.raises(NotFoundError):
            cs.get(got["session_id"], lambda: now[0])
    finally:
        st = cs.status(lambda: 0.0)
        if st is not None:
            cs.release(str(st["session_id"]))
