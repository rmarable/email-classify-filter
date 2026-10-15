"""Applying an import (V1.5 step 9c; OD-355, OD-357 to OD-361): an empty target, --replace, what
changes on the way in, settings and config, the single transaction, step-up, the routes and the
CLI."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import ecf.cli_import
from ecf.cli import app
from ecf.errors import ConflictError, InvalidInputError, StepupRequiredError
from ecf.paths import Paths
from ecf_server import (
    bundle_reader,
    config,
    db,
    export_bundle,
    importer,
    install_identity,
    scheduled_export,
    stepup,
)
from ecf_server.clock import FakeClock
from ecf_server.db import write_tx
from ecf_server.notify import FakeNotifier
from ecf_server.secretstore.memory import MemorySecretStore
from ecf_server.stepper import FakeStepper
from tests.test_addresses import TOKEN, call, make_state
from tests.test_export_keys import ApiClient
from tests.test_scheduled_export import ROOT, _set, _set_up  # pyright: ignore[reportPrivateUsage]

KEY_TEXT = __import__("ecf_server.backup_key", fromlist=["key_text"]).key_text(ROOT)
LABEL = json.dumps({"actions": [{"name": "label", "target": "invoice"}]})


@pytest.fixture
def target(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = db.connect(tmp_path / "target" / "ecf.db")
    db.migrate(c)
    yield c
    c.close()


def _source(conn: sqlite3.Connection) -> None:
    with write_tx(conn), db.items_writer("create"):
        conn.execute("INSERT INTO addresses (address_id, email, sensitivity, stage, outbound,"
                     " preset, created_at) VALUES ('ap', 'ap@acme.example', 'standard', 'live', 1,"
                     " 'C', 't')")  # fmt: skip
        rows = [("00aa00bb", "awaiting_approval", LABEL), ("11aa00bb", "delayed", LABEL),
                ("22aa00bb", "executing", LABEL), ("33aa00bb", "executed", LABEL),
                ("44aa00bb", "awaiting_approval", None)]  # fmt: skip
        for i, (sid, status, proposal) in enumerate(rows):
            conn.execute("INSERT INTO items (stable_id, address_id, uid, uidvalidity, status,"
                         " content_hash, proposal, created_at, updated_at) VALUES (?, 'ap', ?, 1,"
                         " ?, 'h', ?, 't', 't')", (sid, i + 1, status, proposal))  # fmt: skip
        conn.execute("INSERT INTO routes (address_id, surface, route_ref) VALUES ('ap', 'slack',"
                     " 'C1')")  # fmt: skip
        conn.execute("INSERT INTO alerts (key, kind, address_id, detail, opened_at) VALUES"
                     " ('login_rejected:ap', 'login_rejected', 'ap', 'x', 't')")  # fmt: skip
        conn.execute("INSERT INTO audit (ts, event, actor, outcome, data) VALUES ('t',"
                     " 'test.event', 'service', 'ok', '{}')")  # fmt: skip
    _set(conn, "org_domains", ["acme.example"])
    _set(conn, "export_schedule", "weekly")
    _set(conn, "alerts.routes", ["slack", "email"])
    _set(conn, "slack_member_id", "U1")
    _set(conn, "stale_item_days", 45)


def _bundle(conn: sqlite3.Connection, clock: FakeClock, tmp_path: Path) -> Path:
    _source(conn)
    store = MemorySecretStore()
    out = tmp_path / "bundles"
    out.mkdir(exist_ok=True)
    _set_up(conn, store, out)
    r = scheduled_export.run(conn, clock, FakeNotifier(), store, conn_dir(conn), "t")
    assert r["ok"], r
    return out / r["file"]


def conn_dir(conn: sqlite3.Connection) -> Path:
    return Path(conn.execute("PRAGMA database_list").fetchone()[2]).parent


def _import(target: sqlite3.Connection, clock: FakeClock, path: Path, *, replace: bool = False,
            typed: str | None = None, n: FakeNotifier | None = None) -> dict[str, Any]:  # fmt: skip
    parsed = bundle_reader.read(target, str(path), KEY_TEXT)

    def go(nonce: str | None) -> dict[str, Any]:
        return importer.apply(target, clock, n or FakeNotifier(), conn_dir(target), "t", parsed,
                              str(path), replace=replace, typed_install=typed,
                              nonce=nonce)  # fmt: skip

    return _with_step_up(target, clock, go)


def _with_step_up(conn: sqlite3.Connection, clock: FakeClock,
                  fn: Callable[[str | None], dict[str, Any]]) -> dict[str, Any]:  # fmt: skip
    with pytest.raises(StepupRequiredError) as ei:
        fn(None)
    issued = stepup.issue(conn, clock, FakeStepper(), str(ei.value.extra["purpose"]),
                          ei.value.extra["target"])  # fmt: skip
    stepup.verify(conn, clock, FakeStepper(), issued.nonce_id)
    return fn(issued.nonce_id)


def _one(c: sqlite3.Connection, sql: str, *args: Any) -> Any:
    return c.execute(sql, args).fetchone()[0]


def test_every_exported_table_is_copied_or_handled_apart() -> None:
    assert set(importer.COPY) | set(importer.NOT_COPIED) == set(export_bundle.INCLUDED)
    assert not set(importer.COPY) & set(importer.NOT_COPIED)


# An import into an empty install, a replace, the bound step-up, rollback on a bad row,
# validation, and what happens to a bundle's own fields and v2 rules.


def test_import_into_an_empty_install(conn: sqlite3.Connection, clock: FakeClock, tmp_path: Path,
                                      target: sqlite3.Connection) -> None:  # fmt: skip
    path = _bundle(conn, clock, tmp_path)
    source = install_identity.install_id(conn)
    n = FakeNotifier()
    r = _import(target, clock, path, n=n)
    assert r["counts"]["addresses"] == 1 and r["counts"]["items"] == 5
    assert r["counts"]["reposted"] == 2
    a = target.execute("SELECT * FROM addresses").fetchone()
    assert (a["paused"], a["outbound"], a["stage"]) == (1, 0, "assist")
    status = dict(target.execute("SELECT stable_id, status FROM items").fetchall())
    assert status == {"00aa00bb": "awaiting_approval", "11aa00bb": "awaiting_approval",
                      "22aa00bb": "failed_unknown", "33aa00bb": "executed",
                      "44aa00bb": "needs_human"}  # fmt: skip
    grants = target.execute("SELECT stable_id FROM grants WHERE status = 'issued'"
                            " ORDER BY stable_id").fetchall()  # fmt: skip
    assert [g[0] for g in grants] == ["00aa00bb", "11aa00bb"]
    assert _one(target, "SELECT count(*) FROM items WHERE origin = ?", source) == 5
    assert _one(target, "SELECT count(*) FROM audit WHERE origin = ? AND event = 'test.event'",
                source) == 1  # fmt: skip
    assert _one(target, "SELECT count(*) FROM routes") == 0
    assert _one(target, "SELECT count(*) FROM alerts") == 0
    assert _one(target, "SELECT count(*) FROM eval_runs WHERE path != ''") == 0
    # settings: the allow-list
    assert install_identity.install_id(target) != source  # the target keeps its own identity
    settings_now = {r[0]: json.loads(r[1]) for r in target.execute("SELECT key, value FROM"
                                                                   " settings")}  # fmt: skip
    assert settings_now["org_domains"] == ["acme.example"]
    assert settings_now["export_schedule"] == "weekly"
    assert settings_now["alerts.routes"] == ["slack"]
    assert settings_now["stale_item_days"] == 45
    assert "slack_member_id" not in settings_now and "export.key" not in settings_now
    assert config.current(target)["org_domains"] == ["acme.example"]
    # audit and notice
    data = json.loads(_one(target, "SELECT data FROM audit WHERE event = 'import.completed'"))
    assert data["source_install"] == source and data["signer"] == "typed_key"
    assert data["replace"] is False
    notice = next(t for h, t in n.sent if "Security Notice" in h)
    assert f"Data from install {source} was imported" in notice
    assert "org_domains" in notice and "export_schedule" in notice
    # the staging copy is gone
    assert not list(conn_dir(target).glob(".import-*"))


def test_a_non_empty_target_needs_replace_and_the_name(
    conn: sqlite3.Connection, clock: FakeClock, tmp_path: Path, target: sqlite3.Connection
) -> None:
    path = _bundle(conn, clock, tmp_path)
    _import(target, clock, path)
    parsed = bundle_reader.read(target, str(path), KEY_TEXT)
    with pytest.raises(ConflictError, match="--replace"):
        importer.apply(target, clock, FakeNotifier(), conn_dir(target), "t", parsed, str(path),
                       replace=False, typed_install=None, nonce=None)  # fmt: skip
    with pytest.raises(InvalidInputError, match="name typed"):
        importer.apply(target, clock, FakeNotifier(), conn_dir(target), "t", parsed, str(path),
                       replace=True, typed_install="other", nonce=None)  # fmt: skip
    with write_tx(target):
        target.execute("INSERT INTO addresses (address_id, email, sensitivity, preset,"
                       " created_at) VALUES ('old', 'old@acme.example', 'standard', 'C',"
                       " 't')")  # fmt: skip
    r = _import(target, clock, path, replace=True, typed="t")
    assert r["replace"] is True
    ids = [x[0] for x in target.execute("SELECT address_id FROM addresses")]
    assert ids == ["ap"]
    assert _one(target, "SELECT count(*) FROM items") == 5
    assert _one(target, "SELECT count(*) FROM grants WHERE status = 'issued'") == 2


def test_the_step_up_is_bound_to_the_bundle(
    conn: sqlite3.Connection, clock: FakeClock, tmp_path: Path, target: sqlite3.Connection
) -> None:
    path = _bundle(conn, clock, tmp_path)
    parsed = bundle_reader.read(target, str(path), KEY_TEXT)
    other = {"path": str(path), "sha256": "0" * 64, "replace": False}
    issued = stepup.issue(target, clock, FakeStepper(), "import", other)
    assert issued.prompt.startswith(f"ecf: import the bundle {path} (SHA-256 000000000000)")
    stepup.verify(target, clock, FakeStepper(), issued.nonce_id)
    with pytest.raises(StepupRequiredError):
        importer.apply(target, clock, FakeNotifier(), conn_dir(target), "t", parsed, str(path),
                       replace=False, typed_install=None, nonce=issued.nonce_id)  # fmt: skip
    assert _one(target, "SELECT count(*) FROM addresses") == 0


def test_a_bad_row_rolls_everything_back(
    conn: sqlite3.Connection, clock: FakeClock, tmp_path: Path, target: sqlite3.Connection
) -> None:
    path = _bundle(conn, clock, tmp_path)
    parsed = bundle_reader.read(target, str(path), KEY_TEXT)
    parsed.tables["addresses"][0]["sensitivity"] = "extreme"  # breaks a CHECK constraint

    def go(nonce: str | None) -> dict[str, Any]:
        return importer.apply(target, clock, FakeNotifier(), conn_dir(target), "t", parsed,
                              str(path), replace=False, typed_install=None,
                              nonce=nonce)  # fmt: skip

    with pytest.raises(bundle_reader.BadBundleError, match="breaks a rule"):
        _with_step_up(target, clock, go)
    assert _one(target, "SELECT count(*) FROM addresses") == 0
    assert _one(target, "SELECT count(*) FROM settings WHERE key = 'org_domains'") == 0


def _setting(key: str, value: str) -> dict[str, Any]:
    return {"key": key, "value": value, "updated_at": "t", "updated_by": "t"}


INVALID: dict[str, tuple[Callable[[bundle_reader.Parsed], object], str]] = {
    "address_id": (lambda p: p.tables["addresses"][0].update(address_id="Bad_ID"),
                   r"address ID|breaks"),
    "column": (lambda p: p.tables["addresses"][0].update(new_column=1), "column this schema"),
    "setting": (lambda p: p.tables["settings"].append(_setting("stale_item_days", "2")),
                "stale_item_days"),
    "config": (lambda p: p.tables["settings"].append(_setting("org_domains", '["gmail.com"]')),
               "config fails its checks"),
    # an extension over a cap is a SchemaLimitError, still a bad bundle (OD-478)
}  # fmt: skip
# a bundle's schema over a cap isn't refused any more: it is dropped (S4, P11; below)


@pytest.mark.parametrize("case", sorted(INVALID))
def test_validation(conn: sqlite3.Connection, clock: FakeClock, tmp_path: Path,
                    target: sqlite3.Connection, case: str) -> None:  # fmt: skip
    change, why = INVALID[case]
    path = _bundle(conn, clock, tmp_path)
    parsed = bundle_reader.read(target, str(path), KEY_TEXT)
    change(parsed)

    def go(nonce: str | None) -> dict[str, Any]:
        return importer.apply(target, clock, FakeNotifier(), conn_dir(target), "t", parsed,
                              str(path), replace=False, typed_install=None,
                              nonce=nonce)  # fmt: skip

    with pytest.raises(bundle_reader.BadBundleError, match=why):
        _with_step_up(target, clock, go)
    assert _one(target, "SELECT count(*) FROM addresses") == 0


EXT = {
    "fields": {
        "contract_stage": {
            "type": "enum",
            "description": "Where a contract stands.",
            "values": {"none": "None.", "draft": "A draft."},
        }
    },
    "category_values": {"legal_notice": "Letter from a lawyer."},
}


@pytest.mark.parametrize("preset", ["C"])
@pytest.mark.parametrize("over_cap", [False, True])
def test_s4_a_bundles_own_fields_are_dropped_and_named(
    conn: sqlite3.Connection,
    clock: FakeClock,
    tmp_path: Path,
    target: sqlite3.Connection,
    preset: str,
    over_cap: bool,
) -> None:
    """P11 (OD-502): no stored-but-unused copy. A bundle's `config.schema` is dropped from the
    staged copy, the preview, the result and the Security Notice name its fields and that they
    need `ecf config apply` here (an A or B address gets the same text from v3.0.0, ADR 0031).
    One over a cap is dropped too, not refused."""
    from ecf.schema import load_schema  # noqa: PLC0415
    from ecf_server import config, import_plan  # noqa: PLC0415

    ext = {"category_values": {f"c{i}": "Added." for i in range(5)}} if over_cap else EXT
    path = _bundle(conn, clock, tmp_path)
    parsed = bundle_reader.read(target, str(path), KEY_TEXT)
    parsed.tables["settings"].append(_setting("config.schema", json.dumps(ext)))
    for a in parsed.tables["addresses"]:
        a["preset"] = preset
    preview = import_plan.preview(target, parsed)["schema_dropped"]
    n = FakeNotifier()

    def go(nonce: str | None) -> dict[str, Any]:
        return importer.apply(target, clock, n, conn_dir(target), "t", parsed, str(path),
                              replace=False, typed_install=None, nonce=nonce)  # fmt: skip

    done = _with_step_up(target, clock, go)
    note = done["schema_dropped"]
    assert note == preview
    assert note["fields"] == sorted(ext.get("fields", {}))
    assert note["category_values"] == sorted(ext["category_values"])
    assert "re-apply your config file" in note["text"] and "preset A" not in note["text"]
    assert config.current(target)["schema"] is None
    assert config.current_schema(target).digest == load_schema().digest
    assert all(s["section"] != "schema" for s in done["config_changes"])
    assert any("own schema fields" in t for _h, t in n.sent)


def test_s4_a_bundle_without_own_fields_says_nothing(
    conn: sqlite3.Connection, clock: FakeClock, tmp_path: Path, target: sqlite3.Connection
) -> None:
    from ecf_server import import_plan  # noqa: PLC0415

    path = _bundle(conn, clock, tmp_path)
    parsed = bundle_reader.read(target, str(path), KEY_TEXT)
    assert import_plan.preview(target, parsed)["schema_dropped"] is None
    assert _import(target, clock, path)["schema_dropped"] is None


def _v2_rules() -> dict[str, Any]:
    from importlib import resources  # noqa: PLC0415

    from ecf.yamlio import load_yaml  # noqa: PLC0415

    text = resources.files("ecf_server.data").joinpath("starter_rules_v2.yaml").read_text("utf-8")
    return dict(load_yaml(text, source="v2"))


def test_a_bundles_v2_rules_lose_their_unchanged_fixed_rules(
    conn: sqlite3.Connection, clock: FakeClock, tmp_path: Path, target: sqlite3.Connection
) -> None:
    """P7 (OD-500): a bundle from v2 whose applied rules copy the starter rules is converted as
    the service converts at start; a changed fixed rule makes the bundle's config fail."""
    from ecf_server import config, rules  # noqa: PLC0415

    doc = _v2_rules()
    path = _bundle(conn, clock, tmp_path)
    parsed = bundle_reader.read(target, str(path), KEY_TEXT)
    parsed.tables["settings"].append(_setting("config.rules", json.dumps(doc)))

    def go(nonce: str | None) -> dict[str, Any]:
        return importer.apply(target, clock, FakeNotifier(), conn_dir(target), "t", parsed,
                              str(path), replace=False, typed_install=None,
                              nonce=nonce)  # fmt: skip

    _with_step_up(target, clock, go)
    # the rest copies the default rules unchanged, so they go too (S4, P24): `rules: default`
    assert config.current(target)["rules"] is None
    data = json.loads(_one(target, "SELECT data FROM audit WHERE event ="
                                   " 'config.rules_converted'"))  # fmt: skip
    assert data["stripped"] == [r["id"] for r in doc["rules"] if rules.is_fixed_id(r["id"])]
    assert data["defaults_stripped"] == [r["id"] for r in doc["rules"]
                                         if not rules.is_fixed_id(r["id"])]  # fmt: skip
    assert data["default"] is True


def test_a_bundle_with_a_changed_fixed_rule_is_kept_as_at_service_start(
    conn: sqlite3.Connection, clock: FakeClock, tmp_path: Path, target: sqlite3.Connection
) -> None:
    """A2 (OD-500): not refused; the rules are kept, the refusal audited, and the starter rules
    apply with the System Error and doctor path naming the changed rule."""
    from ecf_server import config, rules  # noqa: PLC0415

    doc = _v2_rules()
    doc["rules"][0]["then"] = ["flag"]
    path = _bundle(conn, clock, tmp_path)
    parsed = bundle_reader.read(target, str(path), KEY_TEXT)
    parsed.tables["settings"].append(_setting("config.rules", json.dumps(doc)))

    def go(nonce: str | None) -> dict[str, Any]:
        return importer.apply(target, clock, FakeNotifier(), conn_dir(target), "t", parsed,
                              str(path), replace=False, typed_install=None,
                              nonce=nonce)  # fmt: skip

    _with_step_up(target, clock, go)
    assert config.current(target)["rules"] == doc
    assert "rule fraud_guard is a changed copy" in str(config.rules_problem(target))
    assert config.current_rules(target).rules == rules.load_starter_rules(
        config.current_schema(target)).rules  # fmt: skip
    assert _one(target, "SELECT count(*) FROM audit WHERE event = 'config.rules_converted'"
                " AND outcome = 'denied'") == 1  # fmt: skip


def test_a_bundles_other_rules_are_still_checked(
    conn: sqlite3.Connection, clock: FakeClock, tmp_path: Path, target: sqlite3.Connection
) -> None:
    doc = _v2_rules()
    doc["rules"][0]["then"] = ["flag"]
    doc["rules"].insert(5, {"id": "bad", "when": {"field": "nope", "eq": True}})
    path = _bundle(conn, clock, tmp_path)
    parsed = bundle_reader.read(target, str(path), KEY_TEXT)
    parsed.tables["settings"].append(_setting("config.rules", json.dumps(doc)))

    def go(nonce: str | None) -> dict[str, Any]:
        return importer.apply(target, clock, FakeNotifier(), conn_dir(target), "t", parsed,
                              str(path), replace=False, typed_install=None,
                              nonce=nonce)  # fmt: skip

    with pytest.raises(bundle_reader.BadBundleError, match="unknown field 'nope'"):
        _with_step_up(target, clock, go)


# Routes and CLI: the import route and command, and S4 R1 rules on dropped fields.


def test_route_and_cli(conn: sqlite3.Connection, clock: FakeClock, tmp_path: Path,
                       monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    path = _bundle(conn, clock, tmp_path)
    tdb = tmp_path / "t2" / "ecf.db"
    c = db.connect(tdb)
    db.migrate(c)
    c.close()
    st = make_state(tdb, None)
    st.stepper = FakeStepper()
    st.notifier = FakeNotifier()
    r = call(st, "POST", "/v1/import", {"path": str(path), "secret": KEY_TEXT}, TOKEN)
    assert r.status_code == 403 and r.json()["code"] == "stepup_required"

    def client(_paths: Paths) -> ApiClient:
        return ApiClient(st)

    monkeypatch.setattr(ecf.cli_import, "LocalClient", client)

    def typed(_prompt: str) -> str:
        return KEY_TEXT

    monkeypatch.setattr(ecf.cli_import, "hidden", typed)
    runner = CliRunner()
    r = runner.invoke(app, ["--install", "t", "import", str(path)], input="n\n")
    assert r.exit_code == 1 and "arrives paused" in r.output
    r = runner.invoke(app, ["--install", "t", "import", str(path)], input="y\n")
    assert r.exit_code == 0, r.output
    assert "imported 1 address(es) and 5 emails; 2 approval(s) to decide again" in r.output
    r = runner.invoke(app, ["--install", "t", "import", str(path)], input="y\n")
    assert r.exit_code == 1 and "add --replace" in r.output
    r = runner.invoke(app, ["--install", "t", "import", str(path), "--replace"], input="y\nt\n")
    assert r.exit_code == 0, r.output


SOCCER = {
    "fields": {"about_soccer": {"type": "boolean", "description": "The email is about soccer."}},
    "category_values": {"club_news": "News from a sports club we belong to."},
}
SOCCER_RULES = {"version": 1, "rules": [
    {"id": "soccer_club", "when": {"field": "about_soccer", "eq": True},
     "then": [{"label": "soccer"}]},
    {"id": "club", "when": {"field": "category", "in": ["club_news", "invoice"]}, "then": ["flag"]},
    {"id": "keep", "when": {"field": "requires_action", "eq": True}, "then": ["flag"]},
]}  # fmt: skip


@pytest.mark.usefixtures("extensions_on")
def test_s4_r1_a_bundles_rules_on_its_fields_are_dropped_with_them(
    conn: sqlite3.Connection, clock: FakeClock, tmp_path: Path, target: sqlite3.Connection
) -> None:
    """Review R1 (operator decision 2026-10-14): the RAW soccer example on a C install. The
    fields and the two rules that read them (a user field, an added category value) are dropped
    together and named with their count; the other rule imports as before."""
    from ecf_server import config, import_plan  # noqa: PLC0415

    path = _bundle(conn, clock, tmp_path)
    parsed = bundle_reader.read(target, str(path), KEY_TEXT)
    parsed.tables["settings"] += [_setting("config.schema", json.dumps(SOCCER)),
                                  _setting("config.rules", json.dumps(SOCCER_RULES))]  # fmt: skip
    for a in parsed.tables["addresses"]:
        a["preset"] = "C"
    preview = import_plan.preview(target, parsed)["schema_dropped"]
    n = FakeNotifier()

    def go(nonce: str | None) -> dict[str, Any]:
        return importer.apply(target, clock, n, conn_dir(target), "t", parsed, str(path),
                              replace=False, typed_install=None, nonce=nonce)  # fmt: skip

    note = _with_step_up(target, clock, go)["schema_dropped"]
    assert note == preview and note["rules"] == ["soccer_club", "club"]
    assert "about_soccer" in note["text"] and "the 2 rules that read them" in note["text"]
    assert "re-apply your config file" in note["text"]
    stored = config.current(target)
    assert stored["schema"] is None
    assert [r["id"] for r in stored["rules"]["rules"]] == ["keep"]
    assert [r.id for r in config.current_rules(target).rules][5] == "keep"


def test_s4_r1_rules_on_fields_reads_every_place_a_rule_names_one() -> None:
    doc = {"version": 1, "rules": [
        {"id": "a", "when": {"not": {"field": "about_soccer", "eq": True}}, "then": ["flag"]},
        {"id": "b", "when": {"trigger": "fraud"}, "then": [{"label": {"field": "about_soccer"}}]},
        {"id": "c", "when": {"trigger": "fraud"},
         "then": [{"flag": True, "if": {"field": "category", "eq": "club_news"}}]},
        {"id": "d", "when": {"field": "category", "eq": "invoice"}, "then": ["flag"]},
    ]}  # fmt: skip
    assert importer.rules_on_fields(doc, SOCCER) == ["a", "b", "c"]
    assert importer.rules_on_fields(None, SOCCER) == []
