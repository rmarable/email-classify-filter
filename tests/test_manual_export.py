"""Manual `ecf export --to` (V1.5 step 9a; OD-326, OD-349 to OD-351): the offered and typed
passphrases, the file (passphrase-encrypted, signed when a backup key exists), the path rules,
step-up bound to the path, the routes and the CLI."""

from __future__ import annotations

import json
import re
import sqlite3
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import ecf.cli_export
from ecf.cli import app
from ecf.errors import ConflictError, InvalidInputError, StepupRequiredError
from ecf.paths import Paths
from ecf_server import (
    _age,
    backup_key,
    export_bundle,
    export_keys,
    manual_export,
    passphrase,
    scheduled_export,
    stepup,
)
from ecf_server.clock import FakeClock
from ecf_server.notify import FakeNotifier
from ecf_server.secretstore import SecretStoreNeedsYouError
from ecf_server.secretstore.memory import MemorySecretStore
from ecf_server.stepper import FakeStepper
from tests.test_addresses import TOKEN, call, make_state
from tests.test_export_keys import ApiClient
from tests.test_scheduled_export import _set_up  # pyright: ignore[reportPrivateUsage]

GOOD = "maple orbit candle river stone"


@pytest.fixture
def data_dir(db_path: Path) -> Path:
    return db_path.parent


@pytest.fixture
def out(tmp_path: Path) -> Path:
    p = tmp_path / "exports"
    p.mkdir()
    return p


def _export(conn: sqlite3.Connection, clock: FakeClock, store: MemorySecretStore | None,
            data_dir: Path, path: Path, secret: str = GOOD,
            n: FakeNotifier | None = None) -> dict[str, Any]:  # fmt: skip
    def go(nonce: str | None) -> dict[str, Any]:
        return manual_export.export(conn, clock, n or FakeNotifier(), store, data_dir, "t",
                                    str(path), secret, nonce=nonce)  # fmt: skip

    return _with_step_up(conn, clock, go)


def _with_step_up(conn: sqlite3.Connection, clock: FakeClock,
                  fn: Callable[[str | None], dict[str, Any]]) -> dict[str, Any]:  # fmt: skip
    with pytest.raises(StepupRequiredError) as ei:
        fn(None)
    issued = stepup.issue(conn, clock, FakeStepper(), str(ei.value.extra["purpose"]),
                          ei.value.extra["target"])  # fmt: skip
    stepup.verify(conn, clock, FakeStepper(), issued.nonce_id)
    return fn(issued.nonce_id)


# ---- passphrases --------------------------------------------------------------------------------


def test_the_offered_passphrase_is_six_words_from_the_list() -> None:
    words = passphrase.wordlist()
    assert len(words) == 2048 and words[0] == "abandon" and words[-1] == "zoo"
    offered = passphrase.generate().split()
    assert len(offered) == 6 and all(w in words for w in offered)
    assert passphrase.generate() != passphrase.generate()


@pytest.mark.parametrize(
    ("secret", "ok"),
    [
        ("x" * 20, True),
        ("x" * 19, False),
        ("one two three four five", True),
        ("one two one two one", False),  # 2 different words, 19 characters
        ("a b c d e", True),
        ("short", False),
        ("tab\there and more words", False),
    ],
)
def test_typed_passphrase_strength(secret: str, ok: bool) -> None:
    if ok:
        assert passphrase.check(secret) == secret
    else:
        with pytest.raises(InvalidInputError) as ei:
            passphrase.check(secret)
        assert secret not in str(ei.value)


# ---- the export ---------------------------------------------------------------------------------


def test_without_a_backup_key_the_export_is_unsigned(
    conn: sqlite3.Connection, clock: FakeClock, data_dir: Path, out: Path
) -> None:
    n = FakeNotifier()
    r = _export(conn, clock, None, data_dir, out / "all.ecfb", n=n)
    path = Path(r["path"])
    assert r["signed"] is False and r["seq"] == 1
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    raw = path.read_bytes()
    header = export_bundle.read_header(raw)
    assert header["kind"] == "manual" and header["signed"] is False
    assert "key_fingerprint" not in header
    with pytest.raises(export_bundle.BundleError, match="isn't signed"):
        export_bundle.verify(raw, [])
    ciphertext = raw[raw.index(b"}") + 1 : -export_bundle.SIG_BYTES]
    files = export_bundle.unpack(_age.decrypt_passphrase(ciphertext, GOOD))
    assert set(files) == {"manifest.json", *(f"tables/{t}.jsonl" for t in export_bundle.INCLUDED)}
    assert json.loads(files["manifest.json"])["kind"] == "manual"
    with pytest.raises(_age.AgeError):
        _age.decrypt_passphrase(ciphertext, "wrong passphrase entirely")
    row = conn.execute("SELECT data FROM audit WHERE event = 'export.completed'").fetchone()
    data = json.loads(row[0])
    assert data["kind"] == "manual" and data["signed"] is False and str(out) not in row[0]
    assert any("manual export of all ecf data" in t for _h, t in n.sent)
    assert not list(out.glob(".*.partial"))


def test_with_a_backup_key_the_export_is_signed(
    conn: sqlite3.Connection, clock: FakeClock, data_dir: Path, tmp_path: Path, out: Path
) -> None:
    store = MemorySecretStore()
    backups = tmp_path / "backups"
    backups.mkdir()
    d = _set_up(conn, store, backups)
    r = _export(conn, clock, store, data_dir, out / "all.ecfb")
    assert r["signed"] is True
    header, _ = export_bundle.verify(Path(r["path"]).read_bytes(),
                                     [backup_key.verify_key(d.public)])  # fmt: skip
    assert header["key_fingerprint"] == d.public.fingerprint and header["signed"] is True
    assert scheduled_export.next_seq(conn, clock) == 2  # one sequence for both kinds
    store.locked = True
    with pytest.raises(SecretStoreNeedsYouError):
        _export(conn, clock, store, data_dir, out / "again.ecfb")
    store.locked = False
    store.delete(export_keys.SEED_NAME)
    with pytest.raises(ConflictError, match="can't sign the export: the signing key is missing"):
        _export(conn, clock, store, data_dir, out / "again.ecfb")
    assert not (out / "again.ecfb").exists()


def test_path_rules(data_dir: Path, out: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    assert manual_export.check_path(str(out / "a.ecfb"), data_dir) == out.resolve() / "a.ecfb"
    for bad, why in (
        ("a.ecfb", "full path"),
        (str(out / "a.zip"), "must end in .ecfb"),
        (str(out / "missing" / "a.ecfb"), "existing folder"),
        (str(data_dir / "a.ecfb"), "data directory"),
        (str(out / "a\n.ecfb"), "a file path"),
    ):
        with pytest.raises(InvalidInputError, match=why):
            manual_export.check_path(bad, data_dir)
    (out / "taken.ecfb").write_bytes(b"")
    with pytest.raises(ConflictError, match="already exists"):
        manual_export.check_path(str(out / "taken.ecfb"), data_dir)


def test_weak_passphrases_are_refused_before_step_up(
    conn: sqlite3.Connection, clock: FakeClock, data_dir: Path, out: Path
) -> None:
    with pytest.raises(InvalidInputError, match="too weak"):
        manual_export.export(conn, clock, FakeNotifier(), None, data_dir, "t",
                             str(out / "a.ecfb"), "short", nonce=None)  # fmt: skip


def test_the_step_up_is_bound_to_the_path(
    conn: sqlite3.Connection, clock: FakeClock, data_dir: Path, out: Path
) -> None:
    target = {"path": str(out.resolve() / "a.ecfb")}
    issued = stepup.issue(conn, clock, FakeStepper(), "export_manual", target)
    assert issued.prompt.startswith(f"ecf: export all of ecf's data to {target['path']}")
    stepup.verify(conn, clock, FakeStepper(), issued.nonce_id)
    with pytest.raises(StepupRequiredError):
        manual_export.export(conn, clock, FakeNotifier(), None, data_dir, "t",
                             str(out / "b.ecfb"), GOOD, nonce=issued.nonce_id)  # fmt: skip
    assert not (out / "b.ecfb").exists()


# ---- routes and CLI -----------------------------------------------------------------------------


def test_routes(conn: sqlite3.Connection, db_path: Path, out: Path) -> None:
    del conn
    st = make_state(db_path, None)
    r = call(st, "GET", "/v1/export/passphrase", None, TOKEN)
    assert r.status_code == 200 and len(r.json()["passphrase"].split()) == 6
    body = {"path": str(out / "a.ecfb"), "passphrase": GOOD}
    r = call(st, "POST", "/v1/export", body, TOKEN)
    assert r.status_code == 403 and r.json()["target"] == {"path": str(out.resolve() / "a.ecfb")}


def test_cli(conn: sqlite3.Connection, db_path: Path, out: Path,
             monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    del conn
    st = make_state(db_path, None)
    st.stepper = FakeStepper()
    st.notifier = FakeNotifier()

    def client(_paths: Paths) -> ApiClient:
        return ApiClient(st)

    monkeypatch.setattr(ecf.cli_export, "LocalClient", client)
    runner = CliRunner()
    r = runner.invoke(app, ["--install", "t", "export", "--to", str(out)], input="y\nsaved\n")
    assert r.exit_code == 0, r.output
    made = list(out.glob("ecf-t-manual-*.ecfb"))
    assert len(made) == 1 and f"exported to {made[0]}" in r.output and "unsigned" in r.output

    def typed(_prompt: str, *, confirm: bool = False) -> str:
        assert confirm
        return GOOD

    monkeypatch.setattr(ecf.cli_export, "hidden", typed)
    r = runner.invoke(app, ["--install", "t", "export", "--to", str(out / "mine")], input="n\n")
    assert r.exit_code == 0, r.output
    assert (out / "mine.ecfb").is_file()
    raw = (out / "mine.ecfb").read_bytes()
    ciphertext = raw[raw.index(b"}") + 1 : -export_bundle.SIG_BYTES]
    assert _age.decrypt_passphrase(ciphertext, GOOD)
    r = runner.invoke(app, ["--install", "t", "export"])
    plain = re.sub(r"\x1b\[[0-9;]*m", "", r.output)  # Rich colors help on GitHub Actions
    assert r.exit_code == 0 and "--to" in plain


def test_check_path_takes_another_suffix_for_corpus_files(tmp_path: Path) -> None:
    """The corpus file uses the same checks with its own suffix (SPEC §16.7)."""
    data_dir, out = tmp_path / "data", tmp_path / "out"
    data_dir.mkdir()
    out.mkdir()
    got = manual_export.check_path(str(out / "c.ecfcorpus"), data_dir, suffix=".ecfcorpus")
    assert got == out.resolve() / "c.ecfcorpus"
    with pytest.raises(InvalidInputError, match=r"end in \.ecfcorpus"):
        manual_export.check_path(str(out / "c.ecfb"), data_dir, suffix=".ecfcorpus")
    with pytest.raises(InvalidInputError, match="a corpus can't go inside"):
        manual_export.check_path(str(data_dir / "c.ecfcorpus"), data_dir, suffix=".ecfcorpus",
                                 what="a corpus")  # fmt: skip
