"""`ecf config apply` and `ecf rules test` (V1.2 step 10b)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from ecf.cli import app
from ecf.errors import InvalidInputError, StepupRequiredError
from ecf.paths import Paths
from ecf_server import addresses, config, rules, ruletest, slack_admin, stepup
from ecf_server.clock import FakeClock, to_ts
from ecf_server.db import write_tx
from ecf_server.notify import FakeNotifier
from ecf_server.stepper import FakeStepper

ME = "U0ME1"
SYNTHETIC = Path(__file__).parent / "eval" / "synthetic"
STARTER = Path(__file__).parents[1] / "src" / "ecf_server" / "data" / "starter_rules.yaml"


def _setup(conn: sqlite3.Connection, clock: FakeClock) -> None:
    now = to_ts(clock.now())
    with write_tx(conn):
        conn.execute("INSERT INTO addresses (address_id, email, sensitivity, preset, created_at)"
                     " VALUES ('ap', 'ap@acme.example', 'high', 'A', ?)", (now,))  # fmt: skip
        conn.execute("INSERT INTO settings (key, value, updated_at, updated_by)"
                     " VALUES ('org_domains', ?, ?, 'test')",
                     ('["acme.example"]', now))  # fmt: skip
        for k, v in (("slack_app_id", "A1"), ("slack_team_id", "T1"), ("slack_member_id", ME),
                     ("slack_summary_channel", "CSUM")):  # fmt: skip
            slack_admin.put_setting(conn, k, v, now, actor="test")


def _nonce(conn: sqlite3.Connection, clock: FakeClock, exc: StepupRequiredError) -> str:
    issued = stepup.issue(conn, clock, FakeStepper(), exc.extra["purpose"], exc.extra["target"])
    stepup.verify(conn, clock, FakeStepper(), issued.nonce_id)
    return issued.nonce_id


DOC = """
version: 1
org_domains: [acme.example, acme-group.example]
forward_allow_list:
  - {id: ap_lead, address: lead@acme-group.example}
move_folders: [Receipts]
action_policy:
  standard: {archive: approve}
templates:
  version: 1
  templates:
    - {id: received, subject: "Re: {subject}", body: "Hello {sender_name}, received."}
"""


V = "version: 1\n"

# ---- validation -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "why"),
    [
        ("org_domains: [acme.example]", "version: 1"),
        ("version: 1", "no sections"),
        ("version: 1\nnonsense: 1", "unknown section"),
        ("version: 1\nexport_schedule: daily", r"V1\.5"),
        ("version: 1\nalerts: {routes: [email]}", "ecf alerts set"),
        ("version: 1\norg_domains: [gmail.com]", "public mailbox"),
        (V + "forward_allow_list: [{id: x, address: boss@else.example}]", "isn't in org_domains"),
        ("version: 1\nforward_allow_list: [{id: x, address: ap@acme.example}]", "monitored"),
        ("version: 1\nforward_allow_list: [{id: X!, address: a@acme.example}]", "id"),
        ("version: 1\nmove_folders: [INBOX]", "INBOX"),
        ("version: 1\naction_policy: {high: {archive: auto}}", "hard ceiling"),
        ("version: 1\naction_policy: {standard: {forward_internal: auto}}", "fixed"),
        ("version: 1\naction_policy: {standard: {archive: sometimes}}", "auto or approve"),
        (V + "templates: {version: 1, templates: [{id: t, subject: s, body: '{x}'}]}", "only"),
        (V + "rules: {version: 1, rules: [{id: r, then: [{move: Vendors}]}]}", "move_folders"),
        (V + "rules: {version: 1, rules: [{id: r, then: [reply_template]}]}", "not allowed"),
    ],
)
def test_bad_documents_are_refused(
    conn: sqlite3.Connection, clock: FakeClock, text: str, why: str
) -> None:
    _setup(conn, clock)
    with pytest.raises(InvalidInputError, match=why):
        config.parse(conn, text)


def test_a_change_elsewhere_must_keep_the_other_sections_valid(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _setup(conn, clock)
    n = FakeNotifier()
    doc = ("version: 1\nmove_folders: [Receipts]\n"
           "rules: {version: 1, rules: [{id: r, then: [{move: Receipts}]}]}")  # fmt: skip
    with pytest.raises(StepupRequiredError) as ei:
        config.apply(conn, clock, n, doc, dry_run=False, nonce=None)
    config.apply(conn, clock, n, doc, dry_run=False, nonce=_nonce(conn, clock, ei.value))
    with pytest.raises(InvalidInputError, match="isn't in move_folders"):
        config.parse(conn, "version: 1\nmove_folders: [Other]")  # the applied rule moves there


# ---- apply ----------------------------------------------------------------------------------


def test_dry_run_step_up_apply_audit_and_notice(conn: sqlite3.Connection, clock: FakeClock) -> None:
    _setup(conn, clock)
    n = FakeNotifier()
    plan = config.apply(conn, clock, n, DOC, dry_run=True, nonce=None)
    assert plan.changed and not plan.applied
    changes = {c["section"]: c["change"] for c in plan.changes}
    assert changes == {
        "org_domains": "+acme-group.example",
        "forward_allow_list": "+ap_lead",
        "move_folders": "+Receipts",
        "action_policy": "archive: auto to approve",
        "templates": "from the shipped templates: ~received",
    }
    with pytest.raises(StepupRequiredError) as ei:
        config.apply(conn, clock, n, DOC, dry_run=False, nonce=None)
    issued = stepup.issue(conn, clock, FakeStepper(), "config_apply", ei.value.extra["target"])
    assert issued.prompt.startswith("ecf: apply config: org_domains: +acme-group.example;")
    stepup.verify(conn, clock, FakeStepper(), issued.nonce_id)
    r = config.apply(conn, clock, n, DOC, dry_run=False, nonce=issued.nonce_id)
    assert r.applied
    assert addresses.get_org_domains(conn) == ["acme-group.example", "acme.example"]
    assert config.current(conn)["action_policy"] == {"standard": {
        "archive": "approve", "junk": "auto", "mark_read": "auto", "move": "auto"}}  # fmt: skip
    [row] = conn.execute("SELECT data FROM audit WHERE event = 'config.applied'").fetchall()
    assert json.loads(row[0])["sha256"] == r.sha256
    assert n.sent[-1][0] == "[ecf-alert] Security Notice" and "acme-group" in n.sent[-1][1]
    assert config.apply(conn, clock, n, DOC, dry_run=False, nonce=None).changed is False


def test_the_nonce_covers_exactly_one_document_and_the_config_it_replaces(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _setup(conn, clock)
    n = FakeNotifier()
    a = "version: 1\nmove_folders: [Receipts]"
    b = "version: 1\nmove_folders: [Receipts, Spam]"
    with pytest.raises(StepupRequiredError) as ei:
        config.apply(conn, clock, n, a, dry_run=False, nonce=None)
    nonce = _nonce(conn, clock, ei.value)
    with pytest.raises(StepupRequiredError):  # a nonce for A doesn't apply B
        config.apply(conn, clock, n, b, dry_run=False, nonce=nonce)
    # the config changed after the nonce was issued: the nonce no longer fits
    with pytest.raises(StepupRequiredError) as ei:
        config.apply(conn, clock, n, a, dry_run=False, nonce=None)
    nonce_a = _nonce(conn, clock, ei.value)
    with pytest.raises(StepupRequiredError) as eb:
        config.apply(conn, clock, n, b, dry_run=False, nonce=None)
    config.apply(conn, clock, n, b, dry_run=False, nonce=_nonce(conn, clock, eb.value))
    with pytest.raises(StepupRequiredError):
        config.apply(conn, clock, n, "version: 1\nmove_folders: [Receipts]", dry_run=False,
                     nonce=nonce_a)  # fmt: skip


def test_applied_rules_replace_the_starter_rules(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _setup(conn, clock)
    assert config.current_rules(conn).rules[0].id == "fraud_guard"
    doc = "version: 1\nrules: {version: 1, rules: [{id: only, then: [flag]}]}"
    with pytest.raises(StepupRequiredError) as ei:
        config.apply(conn, clock, FakeNotifier(), doc, dry_run=False, nonce=None)
    r = config.apply(conn, clock, FakeNotifier(), doc, dry_run=False,
                     nonce=_nonce(conn, clock, ei.value))  # fmt: skip
    assert r.changes[0]["change"].startswith("from the starter rules: +only, -fraud_guard")
    assert [x.id for x in config.current_rules(conn).rules] == ["only"]


# ---- rules test -----------------------------------------------------------------------------


@pytest.fixture
def small_set(tmp_path: Path) -> Path:
    """The committed cases only (the large built ones take ~25 s to analyze)."""
    root = tmp_path / "synthetic"
    (root / "eml").mkdir(parents=True)
    lines: list[str] = []
    for line in (SYNTHETIC / "labels.jsonl").read_text().splitlines():
        row = json.loads(line)
        if row["file"].startswith("eml/"):
            (root / row["file"]).write_bytes((SYNTHETIC / row["file"]).read_bytes())
            lines.append(line)
    (root / "labels.jsonl").write_text("\n".join(lines) + "\n")
    return root


def test_rules_test_shows_what_a_change_would_do(
    conn: sqlite3.Connection, clock: FakeClock, small_set: Path
) -> None:
    starter = config.current_rules(conn)
    same = ruletest.run(clock, starter, STARTER.read_text("utf-8"), small_set)
    assert same["changed"] == 0 and same["cases"]
    by_id = {c["id"]: c for c in same["cases"]}
    # the fraud trigger comes from the offline analysis of the .eml (hidden HTML text)
    assert by_id["fraud-hidden-html"]["current"]["rule"] == "fraud_guard"
    r = ruletest.run(clock, starter, _drop_rule(STARTER.read_text("utf-8"), "fraud_guard"),
                     small_set)  # fmt: skip
    changed = {c["id"] for c in r["cases"] if c["changed"]}
    assert {"fraud-hidden-html", "starter-bec"} <= changed
    assert r["expected_matched"]["proposed"] < r["expected_matched"]["current"]


def _schema() -> Any:
    from ecf.schema import load_schema_v1  # noqa: PLC0415

    return load_schema_v1()


def _drop_rule(text: str, rule_id: str) -> str:
    from ecf.yamlio import load_yaml  # noqa: PLC0415

    doc = load_yaml(text, source="starter")
    doc["rules"] = [r for r in doc["rules"] if r["id"] != rule_id]
    return json.dumps(doc)


def test_rules_test_skips_unbuilt_cases_and_refuses_paths_outside(
    tmp_path: Path, clock: FakeClock
) -> None:
    starter = rules.load_starter_rules(_schema())
    line: dict[str, Any] = {
        "id": "gone",
        "file": ".build/gone.eml",
        "expected": {"labels": {}, "rule": None},
    }
    (tmp_path / "labels.jsonl").write_text(json.dumps(line) + "\n")
    r = ruletest.run(clock, starter, STARTER.read_text("utf-8"), tmp_path)
    assert r["skipped"] == ["gone"] and r["cases"] == []
    line["file"] = "../outside.eml"
    (tmp_path / "labels.jsonl").write_text(json.dumps(line) + "\n")
    with pytest.raises(InvalidInputError, match="outside"):
        ruletest.run(clock, starter, STARTER.read_text("utf-8"), tmp_path)
    with pytest.raises(InvalidInputError, match=r"labels\.jsonl"):
        ruletest.run(clock, starter, STARTER.read_text("utf-8"), tmp_path / "nope")


# ---- through the service and the CLI -------------------------------------------------------


def test_cli_rules_test_and_config_dry_run(running: Paths, tmp_path: Path, small_set: Path) -> None:
    rules_file = tmp_path / "rules.yaml"
    rules_file.write_text(_drop_rule(STARTER.read_text("utf-8"), "fraud_guard"))
    r = CliRunner().invoke(app, ["--install", "t", "rules", "test", str(rules_file),
                                 "--cases", str(small_set)])  # fmt: skip
    assert r.exit_code == 0, r.output
    assert "CHANGED fraud-hidden-html (expected rule: fraud_guard)" in r.output
    assert "case(s) change; expected rule matched" in r.output
    cfg = tmp_path / "config.yaml"
    cfg.write_text("version: 1\nmove_folders: [Receipts]\n")
    r = CliRunner().invoke(app, ["--install", "t", "config", "apply", str(cfg)], input="n\n")
    assert r.exit_code == 1, r.output  # declined at the prompt: no step-up, nothing applied
    assert "move_folders: +Receipts" in r.output
