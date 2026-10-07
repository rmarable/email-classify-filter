"""`ecf corpus merge` (SPEC §16.7; R52, R107, R109, R115, R173)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from ecf.errors import InvalidInputError, StepupRequiredError
from ecf_server import corpus, corpus_merge, stepup
from ecf_server.clock import FakeClock
from ecf_server.corpus_merge import Source
from ecf_server.notify import FakeNotifier
from ecf_server.stepper import FakeStepper
from tests.corpus_fakes import FakeMessage
from tests.test_corpus import SECRET, inline, mail, reader, req, server_with


def make(conn: sqlite3.Connection, clock: FakeClock, data_dir: Path, out: Path,
         uids: range, email: str = "ap@acme.example") -> Path:  # fmt: skip
    s = server_with(0)
    s.folders["INBOX"] = {u: FakeMessage(mail(u)) for u in uids}
    r = req(out, total=len(uids), email=email, order="oldest")
    got = corpus.run(conn, clock, FakeNotifier(), data_dir, r, reader(s), SECRET,
                     corpus.Progress(), isolator=inline)  # fmt: skip
    assert got is not None
    return got


def label(c: corpus.Corpus, index: int, category: str) -> dict[str, Any]:
    row = c.rows[index - 1]
    return {"key": row["key"], "category": category, "corpus_id": c.first["corpus_id"],
            "date": "2026-10-07"}  # fmt: skip


def write_labels(path: Path, rows: list[dict[str, Any]]) -> None:
    path.with_name(path.name + ".labels.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows)
    )


def run_merge(conn: sqlite3.Connection, clock: FakeClock, data_dir: Path, paths: list[Path],
              out: Path, **kw: Any) -> dict[str, Any]:  # fmt: skip
    sources = [Source(p, SECRET) for p in paths]
    mixed = bool(kw.get("allow_mixed", False))

    def go(nonce: str | None) -> dict[str, Any]:
        return corpus_merge.merge(conn, clock, FakeNotifier(), data_dir, sources, str(out),
                                  allow_mixed=mixed, own_passphrase=None, nonce=nonce)  # fmt: skip

    try:
        return go(None)
    except StepupRequiredError as exc:
        issued = stepup.issue(conn, clock, FakeStepper(), exc.extra["purpose"],
                              exc.extra["target"])  # fmt: skip
        stepup.verify(conn, clock, FakeStepper(), issued.nonce_id)
        return go(issued.nonce_id)


@pytest.fixture
def out_dir(tmp_path: Path) -> Path:
    d = tmp_path / "out"
    d.mkdir()
    return d


def test_merge_drops_duplicates_reindexes_and_merges_labels(
    conn: sqlite3.Connection, clock: FakeClock, db_path: Path, out_dir: Path
) -> None:
    a = make(conn, clock, db_path.parent, out_dir / "a.ecfcorpus", range(1, 4))
    b = make(conn, clock, db_path.parent, out_dir / "b.ecfcorpus", range(3, 6))
    ca, cb = corpus.open_corpus(a, SECRET), corpus.open_corpus(b, SECRET)
    write_labels(a, [label(ca, 3, "invoice")])
    write_labels(b, [label(cb, 1, "invoice") | {"date": "2026-10-08"}, label(cb, 2, "other")])
    got = run_merge(conn, clock, db_path.parent, [a, b], out_dir / "m.ecfcorpus")
    assert got["count"] == 5 and got["labels"] == 2 and len(got["passphrase"].split()) == 6
    m = corpus.open_corpus(Path(got["out"]), got["passphrase"])
    assert [r["index"] for r in m.rows] == [1, 2, 3, 4, 5]
    assert [r["source_corpus_id"] for r in m.rows] == [ca.first["corpus_id"]] * 3 + [
        cb.first["corpus_id"]
    ] * 2
    assert m.message(4) == mail(4) and m.header["sources"] == [ca.first["corpus_id"],
                                                               cb.first["corpus_id"]]  # fmt: skip
    lines = Path(got["out"] + ".labels.jsonl").read_text().splitlines()
    assert len(lines) == 2 and all(
        json.loads(x)["corpus_id"] == m.first["corpus_id"] for x in lines
    )
    assert (Path(got["out"] + ".labels.jsonl").stat().st_mode & 0o777) == 0o600


def test_conflicting_labels_and_mixed_mailboxes_are_refused(
    conn: sqlite3.Connection, clock: FakeClock, db_path: Path, out_dir: Path
) -> None:
    a = make(conn, clock, db_path.parent, out_dir / "a.ecfcorpus", range(1, 3))
    b = make(conn, clock, db_path.parent, out_dir / "b.ecfcorpus", range(2, 4))
    write_labels(a, [label(corpus.open_corpus(a, SECRET), 2, "invoice")])
    write_labels(b, [label(corpus.open_corpus(b, SECRET), 1, "spam")])
    with pytest.raises(InvalidInputError, match="different labels"):
        run_merge(conn, clock, db_path.parent, [a, b], out_dir / "m.ecfcorpus")
    c = make(conn, clock, db_path.parent, out_dir / "c.ecfcorpus", range(7, 9), "pat@x.example")
    with pytest.raises(InvalidInputError, match="different mailboxes"):
        run_merge(conn, clock, db_path.parent, [a, c], out_dir / "n.ecfcorpus")
    got = run_merge(conn, clock, db_path.parent, [a, c], out_dir / "n.ecfcorpus",
                    allow_mixed=True)  # fmt: skip
    m = corpus.open_corpus(Path(got["out"]), got["passphrase"])
    assert m.header["source_domain"] == "mixed" and "profile" in m.rows[0]


def test_the_step_up_binds_the_files_and_nothing_is_read_before_it(
    conn: sqlite3.Connection, clock: FakeClock, db_path: Path, out_dir: Path
) -> None:
    a = make(conn, clock, db_path.parent, out_dir / "a.ecfcorpus", range(1, 3))
    b = make(conn, clock, db_path.parent, out_dir / "b.ecfcorpus", range(3, 5))
    sources = [Source(a, "wrong passphrase for a"), Source(b, SECRET)]
    with pytest.raises(StepupRequiredError) as exc:  # the wrong passphrase isn't tried yet
        corpus_merge.merge(conn, clock, FakeNotifier(), db_path.parent, sources,
                           str(out_dir / "m.ecfcorpus"), allow_mixed=False,
                           own_passphrase=None, nonce=None)  # fmt: skip
    t = exc.value.extra["target"]
    assert {s["sha256"] for s in t["sources"]} == {corpus_merge.file_sha256(p) for p in (a, b)}
    assert "passphrase" not in json.dumps(t) and t["out"].endswith("m.ecfcorpus")
    bound = corpus_merge._describe(conn, t)  # pyright: ignore[reportPrivateUsage]
    assert "unverified" in bound.prompt
    with pytest.raises(InvalidInputError, match="at least two"):
        corpus_merge.merge(conn, clock, FakeNotifier(), db_path.parent, sources[:1],
                           str(out_dir / "m.ecfcorpus"), allow_mixed=False,
                           own_passphrase=None, nonce=None)  # fmt: skip
