"""The backup key and `export_dir` (V1.5 step 8a; OD-327, OD-339 to OD-342): key text, derivation,
fingerprints, rotation with a typed fingerprint and step-up, the directory checks, the routes and
the CLI."""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable
from pathlib import Path
from types import TracebackType
from typing import Any, Self

import pytest
from nacl.signing import SigningKey
from typer.testing import CliRunner

import ecf.cli_export
from ecf.cli import app
from ecf.client import parse_reply
from ecf.errors import ConflictError, InvalidInputError, NotFoundError, StepupRequiredError
from ecf.log import configure_logging
from ecf.paths import Paths
from ecf_server import _age, backup_key, config, export_keys, settings, stepup
from ecf_server.api import ServiceState
from ecf_server.clock import FakeClock
from ecf_server.notify import FakeNotifier
from ecf_server.secretstore import SecretStoreNeedsYouError
from ecf_server.secretstore.memory import MemorySecretStore
from ecf_server.stepper import FakeStepper
from tests.test_addresses import TOKEN, call, make_state

# ---- backup_key.py ------------------------------------------------------------------------------


def test_key_text_round_trips_and_forgives_case_spaces_and_lookalikes() -> None:
    root = bytes(range(32))
    text = backup_key.key_text(root)
    assert text.startswith("ECF1-") and len(text.replace("-", "")) == 60
    assert all(len(g) == 4 for g in text.split("-"))
    assert backup_key.parse_key_text(text) == root
    sloppy = " ".join(text.lower().split("-")).replace("0", "o").replace("1", "l")
    assert backup_key.parse_key_text(sloppy) == root


def test_key_text_typos_are_caught() -> None:
    text = backup_key.key_text(backup_key.new_root())
    pos = 10 if text[10] != "-" else 11
    typo = text[:pos] + ("A" if text[pos] != "A" else "B") + text[pos + 1 :]
    with pytest.raises(InvalidInputError, match="typo"):
        backup_key.parse_key_text(typo)
    with pytest.raises(InvalidInputError, match="length"):
        backup_key.parse_key_text(text[:-5])
    with pytest.raises(InvalidInputError, match="starts with"):
        backup_key.parse_key_text("AGE-SECRET-KEY-1ABC")
    with pytest.raises(InvalidInputError, match="character"):
        backup_key.parse_key_text("ECF1-" + "U" * 56)


def test_derivation_is_fixed_and_the_halves_belong_together() -> None:
    root = bytes(32)
    a, b = backup_key.derive(root), backup_key.derive(root)
    assert a == b  # restore re-derives the same keys from the typed text
    assert a.identity.startswith("AGE-SECRET-KEY-1") and a.public.recipient.startswith("age1")
    assert _age.recipient_of(a.identity) == a.public.recipient
    data = b"bundle"
    assert _age.decrypt(_age.encrypt(data, a.public.recipient), a.identity) == data
    signed = SigningKey(a.signing_seed).sign(data)
    assert backup_key.verify_key(a.public).verify(signed) == data
    assert backup_key.public_from_seed(a.signing_seed, a.public.recipient) == a.public
    other = backup_key.derive(bytes([1]) + bytes(31))
    assert other.public != a.public and other.public.fingerprint != a.public.fingerprint
    with pytest.raises(_age.AgeError):
        _age.decrypt(_age.encrypt(data, a.public.recipient), other.identity)


def test_fingerprint_shape_and_typed_comparison() -> None:
    fp = backup_key.derive(backup_key.new_root()).public.fingerprint
    groups = fp.split("-")
    assert len(groups) == 4 and all(len(g) == 4 for g in groups)
    assert backup_key.same_fingerprint(f" {fp.lower().replace('-', ' ')} ", fp)
    assert not backup_key.same_fingerprint(fp[:-1] + ("Z" if fp[-1] != "Z" else "Y"), fp)


# ---- export_keys.py -----------------------------------------------------------------------------


Call = Callable[[str | None], dict[str, Any]]


def _with_step_up(conn: sqlite3.Connection, clock: FakeClock, fn: Call) -> tuple[Any, str]:
    with pytest.raises(StepupRequiredError) as ei:
        fn(None)
    issued = stepup.issue(conn, clock, FakeStepper(), str(ei.value.extra["purpose"]),
                          ei.value.extra["target"])  # fmt: skip
    stepup.verify(conn, clock, FakeStepper(), issued.nonce_id)
    return fn(issued.nonce_id), issued.prompt


def _make_key(conn: sqlite3.Connection, clock: FakeClock, store: MemorySecretStore,
              data_dir: Path, n: FakeNotifier) -> tuple[dict[str, Any], Any, str]:  # fmt: skip
    new = export_keys.new_key(conn, clock)

    def rotate(nonce: str | None) -> dict[str, Any]:
        return export_keys.rotate(conn, clock, n, store, data_dir, new["pending_id"],
                                  new["fingerprint"], nonce=nonce)  # fmt: skip

    got, prompt = _with_step_up(conn, clock, rotate)
    return new, got, prompt


def test_first_key_then_rotation(conn: sqlite3.Connection, clock: FakeClock,
                                 tmp_path: Path) -> None:  # fmt: skip
    store, n = MemorySecretStore(), FakeNotifier()
    new, got, prompt = _make_key(conn, clock, store, tmp_path, n)
    assert f"create the backup key {new['fingerprint']}" in prompt
    assert new["replaces"] is None
    assert got["key"]["generation"] == 1 and got["key"]["fingerprint"] == new["fingerprint"]
    root = backup_key.parse_key_text(new["key_text"])
    derived = backup_key.derive(root)
    assert store.get("export-signing-seed") == derived.signing_seed.hex()
    stored = export_keys.current(conn)
    assert stored is not None and stored["recipient"] == derived.public.recipient
    # the key text and the identity are kept nowhere in the database
    dump = "\n".join(conn.iterdump())
    assert new["key_text"] not in dump and derived.identity not in dump
    assert root.hex() not in dump
    events = [r[0] for r in conn.execute("SELECT event FROM audit ORDER BY id")]
    assert "export.key_rotated" in events
    assert any("backup key was created" in t for _h, t in n.sent)

    second, got, prompt = _make_key(conn, clock, store, tmp_path, n)
    assert f"replace backup key {new['fingerprint']} with {second['fingerprint']}" in prompt
    assert second["replaces"] == new["fingerprint"]
    assert got["key"]["generation"] == 2 and got["previous"] == 1
    prev = conn.execute("SELECT value FROM settings WHERE key = 'export.keys_previous'").fetchone()
    assert [k["fingerprint"] for k in json.loads(prev[0])] == [new["fingerprint"]]
    assert any("Bundles made before this need the old key" in t for _h, t in n.sent)


def test_rotation_needs_the_new_fingerprint_typed_and_a_live_pending_key(
    conn: sqlite3.Connection, clock: FakeClock, tmp_path: Path
) -> None:
    store, n = MemorySecretStore(), FakeNotifier()
    new = export_keys.new_key(conn, clock)
    with pytest.raises(InvalidInputError, match="fingerprint"):
        export_keys.rotate(conn, clock, n, store, tmp_path, new["pending_id"],
                           "AAAA-BBBB-CCCC-DDDD", nonce=None)  # fmt: skip
    with pytest.raises(NotFoundError):
        export_keys.rotate(conn, clock, n, store, tmp_path, "nope", new["fingerprint"], nonce=None)
    clock.advance(export_keys.PENDING_FOR.total_seconds())
    with pytest.raises(NotFoundError, match="expired"):
        export_keys.rotate(conn, clock, n, store, tmp_path, new["pending_id"], new["fingerprint"],
                           nonce=None)  # fmt: skip
    assert export_keys.current(conn) is None and store.get("export-signing-seed") is None


def test_a_step_up_for_one_pending_key_cant_commit_another(
    conn: sqlite3.Connection, clock: FakeClock, tmp_path: Path
) -> None:
    store, n = MemorySecretStore(), FakeNotifier()
    a, b = export_keys.new_key(conn, clock), export_keys.new_key(conn, clock)
    issued = stepup.issue(conn, clock, FakeStepper(), "export_key", {"pending_id": a["pending_id"]})
    stepup.verify(conn, clock, FakeStepper(), issued.nonce_id)
    with pytest.raises(StepupRequiredError):
        export_keys.rotate(conn, clock, n, store, tmp_path, b["pending_id"], b["fingerprint"],
                           nonce=issued.nonce_id)  # fmt: skip


def test_export_dir_checks(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    assert export_keys.check_dir(str(out), data) == out.resolve()
    with pytest.raises(InvalidInputError, match="full path"):
        export_keys.check_dir("out", data)
    with pytest.raises(InvalidInputError, match="existing directory"):
        export_keys.check_dir(str(tmp_path / "missing"), data)
    with pytest.raises(InvalidInputError, match="data directory"):
        export_keys.check_dir(str(data), data)
    (data / "inner").mkdir()
    with pytest.raises(InvalidInputError, match="data directory"):
        export_keys.check_dir(str(data / "inner"), data)
    with pytest.raises(InvalidInputError):
        export_keys.check_dir(str(out) + "\n", data)
    locked = tmp_path / "locked"
    locked.mkdir(mode=0o500)
    try:
        with pytest.raises(InvalidInputError, match="can't write"):
            export_keys.check_dir(str(locked), data)
    finally:
        locked.chmod(0o700)


def test_same_volume_except_cloud_folders(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    home = tmp_path / "home"
    icloud = home / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "ecf"
    provider = home / "Library" / "CloudStorage" / "Dropbox" / "ecf"
    for d in (icloud, provider):
        d.mkdir(parents=True)
    assert export_keys.same_volume(tmp_path, data, home) is True  # tmp_path: one disk
    assert export_keys.same_volume(icloud, data, home) is False
    assert export_keys.same_volume(provider, data, home) is False


def test_set_dir(conn: sqlite3.Connection, clock: FakeClock, tmp_path: Path) -> None:
    store, n = MemorySecretStore(), FakeNotifier()
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    out = tmp_path / "out"
    out.mkdir()
    with pytest.raises(ConflictError, match="backup key first"):
        export_keys.set_dir(conn, clock, n, data, str(out), "x", nonce=None)
    _new, got, _ = _make_key(conn, clock, store, data, n)
    fp = got["key"]["fingerprint"]
    with pytest.raises(InvalidInputError, match="fingerprint"):
        export_keys.set_dir(conn, clock, n, data, str(out), "AAAA", nonce=None)

    def set_dir(nonce: str | None) -> dict[str, Any]:
        return export_keys.set_dir(conn, clock, n, data, str(out), fp.lower(), nonce=nonce)

    got, prompt = _with_step_up(conn, clock, set_dir)
    assert f"write backups to {out.resolve()} (backup key {fp})" in prompt
    assert got["dir"] == str(out.resolve()) and got["same_volume"] is True
    assert any("Backups now go to" in t and "same disk" in t for _h, t in n.sent)
    row = conn.execute("SELECT data FROM audit WHERE event = 'export.dir_set'").fetchone()
    assert json.loads(row[0]) == {"was": None, "same_volume": True}  # the path isn't audited


def test_settings_and_config_point_to_the_command() -> None:
    with pytest.raises(InvalidInputError, match="ecf export dir set"):
        settings.key("export_dir", None)
    assert "ecf export dir set" in config.LATER["export_dir"]


def test_key_text_is_redacted_in_logs(caplog: pytest.LogCaptureFixture) -> None:
    configure_logging("cli")
    import structlog  # noqa: PLC0415

    with caplog.at_level(logging.INFO):
        structlog.get_logger("t").info("x", key_text="ECF1-SECRET", backup_key="ECF1-SECRET")
    assert "ECF1-SECRET" not in caplog.text


# ---- routes and CLI -----------------------------------------------------------------------------


def _state(db_path: Path) -> ServiceState:
    st = make_state(db_path, MemorySecretStore())
    st.stepper = FakeStepper()
    st.notifier = FakeNotifier()
    return st


def test_routes(conn: sqlite3.Connection, db_path: Path) -> None:
    del conn
    st = _state(db_path)
    r = call(st, "GET", "/v1/export", None, TOKEN)
    got = r.json()
    assert r.status_code == 200 and got["key"] is None and got["dir"] is None
    assert got["previous"] == 0 and got["same_volume"] is None and not got["set_up"]
    new = call(st, "POST", "/v1/export/keys/new", {}, TOKEN).json()
    body = {"pending_id": new["pending_id"], "fingerprint": new["fingerprint"]}
    r = call(st, "POST", "/v1/export/keys", body, TOKEN)
    assert r.status_code == 403 and r.json()["code"] == "stepup_required"
    assert r.json()["target"] == {"pending_id": new["pending_id"]}
    r = call(st, "POST", "/v1/export/dir", {"path": "/nowhere", "fingerprint": "x"}, TOKEN)
    assert r.status_code == 409  # no key yet
    no_store = make_state(db_path, None)
    r = call(no_store, "POST", "/v1/export/keys/new", {}, TOKEN)
    assert r.status_code == 503  # no secret store: no key is shown


class ApiClient:
    """LocalClient over the ASGI app."""

    def __init__(self, st: ServiceState) -> None:
        self.st = st

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: type[BaseException] | BaseException | TracebackType | None) -> None:
        return None

    def request(self, method: str, path: str, json: Any = None, **_: Any) -> Any:
        return parse_reply(call(self.st, method, path, json, TOKEN))

    def get(self, path: str, **_: Any) -> Any:
        return self.request("GET", path)


def test_cli_rotate_and_dir_set(conn: sqlite3.Connection, db_path: Path, tmp_path: Path,
                                monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    del conn
    st = _state(db_path)

    def client(_paths: Paths) -> ApiClient:
        return ApiClient(st)

    monkeypatch.setattr(ecf.cli_export, "LocalClient", client)
    runner = CliRunner()
    fixed = backup_key.new_root()
    monkeypatch.setattr(backup_key, "new_root", lambda: fixed)
    fp = backup_key.derive(fixed).public.fingerprint
    r = runner.invoke(app, ["--install", "t", "export", "keys", "rotate"],
                      input="later\nsaved\nWRONG\nWRONG\nWRONG\n")  # fmt: skip
    assert r.exit_code == 1 and backup_key.key_text(fixed) in r.output
    assert "not the fingerprint shown" in r.output
    r = runner.invoke(app, ["--install", "t", "export", "keys", "rotate"],
                      input=f"saved\nWRONG\n{fp.lower()}\n")  # fmt: skip
    assert r.exit_code == 0, r.output
    assert f"backup key {fp} in use (generation 1)" in r.output
    assert "next: ecf export dir set" in r.output
    out = tmp_path / "backups"
    out.mkdir()
    r = runner.invoke(app, ["--install", "t", "export", "dir", "set", str(out)], input=f"{fp}\n")
    assert r.exit_code == 0, r.output
    assert f"backups go to {out.resolve()}" in r.output and "same disk" in r.output
    r = runner.invoke(app, ["--install", "t", "export", "keys", "show"])
    assert r.exit_code == 0 and fp in r.output and str(out.resolve()) in r.output


def test_a_locked_store_leaves_the_key_pending_for_a_retry(
    conn: sqlite3.Connection, clock: FakeClock, tmp_path: Path
) -> None:
    store, n = MemorySecretStore(), FakeNotifier()
    new = export_keys.new_key(conn, clock)

    def rotate(nonce: str | None) -> dict[str, Any]:
        return export_keys.rotate(conn, clock, n, store, tmp_path, new["pending_id"],
                                  new["fingerprint"], nonce=nonce)  # fmt: skip

    store.locked = True
    with pytest.raises(SecretStoreNeedsYouError):
        _with_step_up(conn, clock, rotate)
    assert export_keys.current(conn) is None
    store.locked = False
    got, _ = _with_step_up(conn, clock, rotate)  # the same saved key, a fresh step-up
    assert got["key"]["fingerprint"] == new["fingerprint"]
