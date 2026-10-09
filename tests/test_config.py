"""`ecf config apply` and `ecf rules test` (V1.2 step 10b)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import ecf.schema
from ecf.cli import app
from ecf.errors import InvalidInputError, SchemaLimitError, StepupRequiredError
from ecf.paths import Paths
from ecf.schema import load_schema
from ecf_server import (
    addresses,
    claude_pins,
    config,
    ops_doctor,
    rules,
    ruletest,
    slack_admin,
    stepup,
)
from ecf_server.clock import FakeClock, to_ts
from ecf_server.db import write_tx
from ecf_server.notify import FakeNotifier
from ecf_server.stepper import FakeStepper
from tests.test_addresses import call, make_state

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
        ("version: 1\nexport_schedule: hourly", "daily, weekly or off"),
        ("version: 1\nalerts: {routes: [email]}", "ecf alerts set"),
        ("version: 1\norg_domains: [gmail.com]", "gmail.com; list the people .* in org_addresses"),
        (V + "forward_allow_list: [{id: x, address: boss@else.example}]", "must be in org_domains"),
        ("version: 1\nforward_allow_list: [{id: x, address: ap@acme.example}]", "monitored"),
        ("version: 1\nforward_allow_list: [{id: X!, address: a@acme.example}]", "id"),
        ("version: 1\nmove_folders: [INBOX]", "INBOX"),
        ("version: 1\naction_policy: {high: {archive: auto}}", "hard ceiling"),
        ("version: 1\naction_policy: {standard: {forward_internal: auto}}", "fixed"),
        ("version: 1\naction_policy: {standard: {archive: sometimes}}", "auto or approve"),
        (V + "templates: {version: 1, templates: [{id: t, subject: s, body: '{x}'}]}", "only"),
        (V + "rules: {version: 1, rules: [{id: r, then: [{move: Vendors}]}]}", "move_folders"),
        (V + "rules: {version: 1, rules: [{id: r, then: [reply_template]}]}", "not allowed"),
        (V + "org_addresses: [{address: pat@x.example, name: Pat}]", "at least two words"),
        (V + "org_addresses: [{address: pat@x.example, role: ceo}]", "{address, name}"),
        (V + "org_addresses: [{name: Pat Lee}]", "{address, name}"),
        (V + "org_addresses: [{address: not-an-address}]", "not an email address"),
        (V + "org_addresses: [{address: patlee@gmail.com}, {address: Pat.Lee+x@googlemail.com}]",
         "listed twice"),
        (V + f"org_addresses: [{', '.join(f'{{address: a{i}@x.example}}' for i in range(51))}]",
         "at most 50"),
        ("version: 1\norg_domains: []", "can't be empty while you watch an address at"),
        ("version: 1\norg_addresses: default\norg_domains: default", "no default"),
    ],
)  # fmt: skip
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
    assert issued.prompt.startswith("ecf: apply config: forward_allow_list: +ap_lead;")  # riskiest
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


def _apply(conn: sqlite3.Connection, clock: FakeClock, doc: str) -> config.Result:
    with pytest.raises(StepupRequiredError) as ei:
        config.apply(conn, clock, FakeNotifier(), doc, dry_run=False, nonce=None)
    return config.apply(conn, clock, FakeNotifier(), doc, dry_run=False,
                        nonce=_nonce(conn, clock, ei.value))  # fmt: skip


def test_default_returns_each_section_to_its_shipped_value(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _setup(conn, clock)
    _apply(conn, clock, DOC + "rules: {version: 1, rules: [{id: only, then: [flag]}]}\n")
    reset = ("version: 1\nforward_allow_list: default\nmove_folders: default\n"
             "action_policy: default\nrules: default\ntemplates: default\n")  # fmt: skip
    r = _apply(conn, clock, reset)
    changes = {c["section"]: c["change"] for c in r.changes}
    assert changes == {
        "forward_allow_list": "reset to none: -ap_lead",
        "move_folders": "reset to none: -Receipts",
        "action_policy": "reset to the default policy: archive: approve to auto",
        "rules": changes["rules"],
        "templates": changes["templates"],
    }
    assert changes["rules"].startswith("reset to the starter rules: +fraud_guard")
    assert changes["templates"].startswith("reset to the shipped templates: ")
    now = config.current(conn)
    assert all(now[s] is None for s in config.SECTIONS if s != "org_domains")
    assert config.current_rules(conn).rules[0].id == "fraud_guard"
    assert addresses.get_org_domains(conn) == ["acme-group.example", "acme.example"]
    # already at the shipped values: nothing to apply, no step-up asked
    again = config.apply(conn, clock, FakeNotifier(), reset, dry_run=False, nonce=None)
    assert again.changed is False


def test_a_reset_must_keep_the_other_sections_valid(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _setup(conn, clock)
    with pytest.raises(InvalidInputError, match="org_domains has no default"):
        config.parse(conn, "version: 1\norg_domains: default")
    moves = "rules: {version: 1, rules: [{id: r, then: [{move: Receipts}]}]}"
    _apply(conn, clock, f"version: 1\nmove_folders: [Receipts]\n{moves}")
    with pytest.raises(InvalidInputError, match="isn't in move_folders"):
        config.parse(conn, "version: 1\nmove_folders: default")  # the applied rule moves there
    r = _apply(conn, clock, "version: 1\nmove_folders: default\nrules: default")  # both at once
    assert {c["section"] for c in r.changes} == {"move_folders", "rules"}


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


def test_the_quick_favour_opener_escalates_without_the_model(
    tmp_path: Path, clock: FakeClock
) -> None:
    """OD-479: `mid-exec-quick-favor` escalates through rule 1's bec_opener clause even when the
    model rates its fraud risk low (it did once at num_ctx 6144)."""
    (tmp_path / "eml").mkdir()
    row = next(json.loads(line) for line in (SYNTHETIC / "labels.jsonl").read_text().splitlines()
               if json.loads(line)["id"] == "mid-exec-quick-favor")  # fmt: skip
    (tmp_path / row["file"]).write_bytes((SYNTHETIC / row["file"]).read_bytes())
    row["expected"]["labels"]["fraud_risk"] = "low"
    (tmp_path / "labels.jsonl").write_text(json.dumps(row) + "\n")
    starter = rules.load_starter_rules(_schema())
    [case] = ruletest.run(clock, starter, STARTER.read_text("utf-8"), tmp_path)["cases"]
    assert case["current"]["rule"] == "fraud_guard" and "escalate" in case["current"]["actions"]


def _schema() -> Any:
    from ecf.schema import load_schema  # noqa: PLC0415

    return load_schema()


def _drop_rule(text: str, rule_id: str) -> str:
    from ecf.yamlio import load_yaml  # noqa: PLC0415

    doc = load_yaml(text, source="starter")
    doc["rules"] = [r for r in doc["rules"] if r["id"] != rule_id]
    return json.dumps(doc)


def _case_set(root: Path, cards: dict[str, str]) -> None:
    """Build `cards` (id -> card text) into a set under `root`, as `ecf eval build` does."""
    from ecf.eval.builder import build_all  # noqa: PLC0415

    (root / "cases").mkdir(parents=True)
    for cid, text in cards.items():
        (root / "cases" / f"{cid}.md").write_text(text)
    assert not build_all(root).findings


def test_rules_test_profiles_set_the_address_and_the_internal_set(
    tmp_path: Path, clock: FakeClock
) -> None:
    """OD-443: a freemail case goes to a personal account with listed colleagues; the scratch
    treats freemail.example as public; the org profile lists one named staff member."""
    head = '---\nid: {id}\ntitle: t\nfrom: "{frm}"\nsubject: Invoice\n{extra}---\n{body}\n'
    pay = "Please wire the payment for the overdue invoice today."
    _case_set(tmp_path, {
        "free-imp": head.format(id="free-imp", frm="Sam Rivera <sam-rivera-desk@freemail.example>",
                                extra="profile: freemail\n", body=pay),
        "free-near": head.format(id="free-near", frm="Pat <pat-lee@freemai1.example>",
                                 extra="profile: freemail\n", body="Lunch?"),
        "org-imp": head.format(id="org-imp", frm="Dana Chief <dana-chief-ceo@freemail.example>",
                               extra="", body=pay),
    })  # fmt: skip
    starter = rules.load_starter_rules(_schema())
    by_id = {c["id"]: c for c in ruletest.run(clock, starter, STARTER.read_text("utf-8"),
                                               tmp_path)["cases"]}  # fmt: skip
    assert by_id["free-imp"]["current"]["rule"] == by_id["org-imp"]["current"]["rule"]
    scratch = ruletest.Scratch(clock)
    try:
        f = scratch.facts((tmp_path / "eml" / "free-imp.eml").read_bytes(), "freemail")
        assert f["impersonates_internal"] and f["sender_origin"] == "external"
        assert any("Sam Rivera" in w for w in f["triggers"]["fraud"])
        near = scratch.facts((tmp_path / "eml" / "free-near.eml").read_bytes(), "freemail")
        assert near["triggers"]["lookalikes"] == ["freemai1.example looks like freemail.example"]
        org = scratch.facts((tmp_path / "eml" / "org-imp.eml").read_bytes())
        assert org["impersonates_internal"]  # Dana Chief is the org profile's listed name
        assert not scratch.facts((tmp_path / "eml" / "free-imp.eml").read_bytes())[
            "impersonates_internal"]  # fmt: skip
    finally:
        scratch.close()


def test_rules_test_refuses_internal_facts_and_unknown_profiles(
    tmp_path: Path, clock: FakeClock
) -> None:
    starter = rules.load_starter_rules(_schema())
    (tmp_path / "a.eml").write_bytes(b"From: a@vendor-a.example\r\n\r\nhi\r\n")
    bad: list[tuple[dict[str, Any], str]] = [
        ({"profile": "corporate"}, "no profile"),
        ({"expected": {"facts": {"sender_origin": "internal"}}}, "OD-443"),
    ]
    for extra, why in bad:
        line: dict[str, Any] = {"id": "a", "file": "a.eml", "expected": {"labels": {"x": 1}}}
        line |= extra
        (tmp_path / "labels.jsonl").write_text(json.dumps(line) + "\n")
        with pytest.raises(InvalidInputError, match=why):
            ruletest.run(clock, starter, STARTER.read_text("utf-8"), tmp_path)


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


# ---- org addresses (V1.6) ---------------------------------------------------------------------


def test_org_addresses_are_stored_lowercased_with_names_tidied(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _setup(conn, clock)
    doc = config.parse(conn, V + "org_addresses: [{address: Pat.Lee@Gmail.com, name: ' Pat  Lee '},"
                             " {address: dana@acme.example}]")  # fmt: skip
    assert doc == {"org_addresses": [{"address": "pat.lee@gmail.com", "name": "Pat Lee"},
                                     {"address": "dana@acme.example"}]}  # fmt: skip
    changes = config.diff(config.current(conn), doc)
    assert changes == [{"section": "org_addresses",
                        "change": "+pat.lee@gmail.com, +dana@acme.example"}]  # fmt: skip
    assert config.parse(conn, V + "org_addresses: default") == {"org_addresses": "default"}


def test_org_domains_may_be_empty_when_every_watched_address_is_at_a_public_provider(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    """OD-441."""
    _setup(conn, clock)
    with write_tx(conn):
        conn.execute("UPDATE addresses SET email = 'me@gmail.com'")
    assert config.parse(conn, "version: 1\norg_domains: []") == {"org_domains": []}


def test_a_forward_to_a_listed_personal_account_is_allowed_and_named_in_the_dialog(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    """OD-437: a forward target is in org_domains or exactly in org_addresses; one at a public
    provider is named first in the step-up dialog."""
    _setup(conn, clock)
    n = FakeNotifier()
    text = (V + "org_addresses: [{address: pat.lee@gmail.com, name: Pat Lee}]\n"
            "forward_allow_list: [{id: pat, address: pat.lee@gmail.com}]")  # fmt: skip
    with pytest.raises(StepupRequiredError) as ei:
        config.apply(conn, clock, n, text, dry_run=False, nonce=None)
    issued = stepup.issue(conn, clock, FakeStepper(), "config_apply", ei.value.extra["target"])
    want = "ecf: apply config: pat.lee@gmail.com is a personal account your organization"
    assert issued.prompt.startswith(want + " doesn't control; forward_allow_list:")
    stepup.verify(conn, clock, FakeStepper(), issued.nonce_id)
    assert config.apply(conn, clock, n, text, dry_run=False, nonce=issued.nonce_id).applied
    with pytest.raises(InvalidInputError, match=r"or exactly in org_addresses .* other dots"):
        config.parse(conn, V + "forward_allow_list: [{id: x, address: patlee@gmail.com}]")  # exact
    # OD-452: removing the address a forward needs is refused until the forward goes too
    with pytest.raises(InvalidInputError, match="remove the forward too"):
        config.parse(conn, V + "org_addresses: []")
    with pytest.raises(InvalidInputError, match="remove the forward too"):
        config.parse(conn, V + "org_addresses: default")
    assert config.parse(conn, V + "org_addresses: []\nforward_allow_list: []")


def test_an_org_domain_forward_isnt_named_as_personal(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _setup(conn, clock)
    doc = config.parse(conn, V + "forward_allow_list: [{id: a, address: lead@acme.example}]")
    assert config.personal_targets(config.current(conn), doc) == ""


def test_the_summary_puts_the_riskiest_change_first_and_counts_what_it_cuts() -> None:
    changes = [{"section": "move_folders", "change": "+Receipts"},
               {"section": "org_domains", "change": "+x" * 40},
               {"section": "forward_allow_list", "change": "+payables"}]  # fmt: skip
    full = config.summary(changes)
    assert full.startswith("forward_allow_list: +payables; org_domains:")
    assert full.endswith("move_folders: +Receipts")
    cut = config.summary(changes, 60)
    assert cut == "forward_allow_list: +payables; +2 more sections"
    alone = config.summary([{"section": "rules", "change": "+r" * 100}, changes[0]], 50)
    assert alone.startswith("rules: +r") and alone.endswith("…; +1 more section")
    assert len(alone) <= 50


# ---- schema extensions (OD-478) -------------------------------------------------------------

SCHEMA_DOC = """version: 1
schema:
  fields:
    contract_stage:
      type: enum
      description: Where a contract discussed in the email stands.
      values: {none: No contract discussed., draft: A draft is being exchanged.}
  category_values:
    legal_notice: Letter from a lawyer or court about us.
"""
LABEL_STAGE = ("rules: {version: 1, rules: [{id: stage, when: {field: contract_stage,"
               " eq: draft}, then: [{label: {field: contract_stage}}]},"
               " {id: rest, then: [leave]}]}\n")  # fmt: skip


def test_schema_section_order_and_key() -> None:
    assert config.SECTIONS.index("schema") < config.SECTIONS.index("rules")
    assert config.RISK_ORDER.index("schema") == config.RISK_ORDER.index("rules") - 1
    assert config.KEY["schema"] == "config.schema"


@pytest.mark.usefixtures("extensions_on")
def test_a_schema_extension_dry_run_step_up_apply_and_notice(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _setup(conn, clock)
    n = FakeNotifier()
    base = config.current_schema(conn)
    plan = config.apply(conn, clock, n, SCHEMA_DOC, dry_run=True, nonce=None)
    assert plan.changes == [{"section": "schema", "change": f"{config.SCHEMA_LEAD}:"
                             " fields +contract_stage; category +legal_notice"}]  # fmt: skip
    preview = plan.to_json()["schema"]
    assert preview["budget"].startswith("schema: 1 of 8 fields, 1 of 4 category values, ")
    assert preview["near"] == []
    assert (
        "+ - contract_stage (one of): Where a contract discussed in the email stands."
        in (preview["prompt_diff"])
    )
    assert "+     legal_notice: Letter from a lawyer or court about us." in preview["prompt_diff"]
    with pytest.raises(StepupRequiredError) as ei:
        config.apply(conn, clock, n, SCHEMA_DOC, dry_run=False, nonce=None)
    issued = stepup.issue(conn, clock, FakeStepper(), "config_apply", ei.value.extra["target"])
    assert "changes the classifier prompt" in issued.prompt
    stepup.verify(conn, clock, FakeStepper(), issued.nonce_id)
    r = config.apply(conn, clock, n, SCHEMA_DOC, dry_run=False, nonce=issued.nonce_id)
    assert r.applied
    s = config.current_schema(conn)
    assert s.digest != base.digest and s is config.current_schema(conn)  # cached
    assert "contract_stage" in s.fields and "legal_notice" in s.fields["category"].values
    assert config.current_rules(conn).schema is s
    [row] = conn.execute("SELECT data FROM audit WHERE event = 'config.applied'").fetchall()
    assert json.loads(row[0])["changes"][0]["section"] == "schema"
    assert "changes the classifier prompt" in n.sent[-1][1]
    # `schema: default` removes it
    r = _apply(conn, clock, "version: 1\nschema: default\n")
    assert r.changes[0]["change"].startswith(f"reset to built-in schema: {config.SCHEMA_LEAD}:"
                                             " fields -contract_stage")  # fmt: skip
    assert r.schema is not None and r.schema["prompt_diff"][0].startswith("- ")
    assert config.current_schema(conn).digest == base.digest


@pytest.mark.usefixtures("extensions_on")
def test_a_cap_violation_lists_every_one_before_step_up(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _setup(conn, clock)
    fields = "".join(f"    f{i}: {{type: boolean, description: Added.}}\n" for i in range(9))
    cats = "".join(f"    c{i}: Added.\n" for i in range(5))
    doc = f"version: 1\nschema:\n  fields:\n{fields}  category_values:\n{cats}"
    with pytest.raises(SchemaLimitError) as ei:
        config.apply(conn, clock, FakeNotifier(), doc, dry_run=True, nonce=None)
    assert ei.value.extra["violations"] == ["schema: 9 fields, limit 8",
                                            "schema: 5 category values, limit 4"]  # fmt: skip
    assert ei.value.detail.startswith("config: schema: 9 fields, limit 8; ")


@pytest.mark.usefixtures("extensions_on")
def test_near_a_cap_the_budget_line_says_so(conn: sqlite3.Connection, clock: FakeClock) -> None:
    _setup(conn, clock)
    fields = "".join(f"    f{i}: {{type: boolean, description: Added.}}\n" for i in range(7))
    plan = config.apply(conn, clock, FakeNotifier(), f"version: 1\nschema:\n  fields:\n{fields}",
                        dry_run=True, nonce=None)  # fmt: skip
    assert plan.schema is not None and plan.schema["near"] == ["fields"]
    assert plan.schema["budget"].endswith(" (near the limit)")


@pytest.mark.usefixtures("extensions_on")
def test_rules_use_extension_fields_and_a_removal_is_refused_naming_the_rule(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _setup(conn, clock)
    with pytest.raises(InvalidInputError, match="unknown field 'contract_stage'"):
        config.parse(conn, "version: 1\n" + LABEL_STAGE)  # no extension yet
    _apply(conn, clock, SCHEMA_DOC + LABEL_STAGE)  # schema validated first, then the rules
    assert [r.id for r in config.current_rules(conn).rules] == ["stage", "rest"]
    with pytest.raises(InvalidInputError, match="rule stage: unknown field 'contract_stage'"):
        config.parse(conn, "version: 1\nschema: default")
    other = SCHEMA_DOC.replace("contract_stage", "deal_stage")
    with pytest.raises(InvalidInputError, match=r"the rules in force don't compile.*rule stage"):
        config.parse(conn, other)
    _apply(conn, clock, "version: 1\nschema: default\nrules: default")  # both at once


@pytest.mark.usefixtures("extensions_on")
def test_an_added_value_cant_be_a_rule_id_or_a_built_in_label(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _setup(conn, clock)
    with pytest.raises(InvalidInputError, match="built-in label or a rule id"):
        config.parse(conn, "version: 1\nschema: {category_values: {fraud_guard: x}}")  # starter
    with pytest.raises(InvalidInputError, match="built-in label or a rule id"):
        config.parse(conn, "version: 1\nschema: {category_values: {alert_echo: x}}")
    _apply(conn, clock, SCHEMA_DOC)
    with pytest.raises(InvalidInputError, match="rule id legal_notice is a value"):
        config.parse(conn, "version: 1\nrules: {version: 1, rules: [{id: legal_notice,"
                           " then: [leave]}]}")  # fmt: skip


@pytest.mark.usefixtures("extensions_on")
def test_the_effective_schema_moves_the_gate_key(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _setup(conn, clock)
    before = claude_pins.address_key(conn, "ap")
    _apply(conn, clock, SCHEMA_DOC)
    assert claude_pins.address_key(conn, "ap") != before
    assert claude_pins.pins(conn, "A")["digest"].endswith(config.current_schema(conn).digest)
    assert claude_pins.pins(conn, "C")["schema"] == config.current_schema(conn).digest


@pytest.mark.usefixtures("extensions_on")
def test_the_schema_route(conn: sqlite3.Connection, clock: FakeClock, db_path: Path) -> None:
    _setup(conn, clock)
    st = make_state(db_path, None)
    r = call(st, "GET", "/v1/schema")
    assert r.status_code == 200
    assert r.json() == {"version": 2, "extension": None, "digest": load_schema().digest}
    _apply(conn, clock, SCHEMA_DOC)
    got = call(st, "GET", "/v1/schema").json()
    assert got["extension"]["category_values"] == {
        "legal_notice": "Letter from a lawyer or court about us."}  # fmt: skip
    assert got["digest"] == config.current_schema(conn).digest != load_schema().digest


# ---- review fixes (OD-478) ---------------------------------------------------------------------


@pytest.mark.usefixtures("extensions_on")
@pytest.mark.parametrize("empty", ["{}", "[]", "''", "0", "false", "null"])
def test_an_empty_or_wrong_typed_schema_is_refused(
    conn: sqlite3.Connection, clock: FakeClock, empty: str
) -> None:
    _setup(conn, clock)
    with pytest.raises(InvalidInputError, match="schema"):
        config.parse(conn, f"version: 1\nschema: {empty}\n")


@pytest.mark.usefixtures("extensions_on")
def test_the_extension_keeps_the_operators_order(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _setup(conn, clock)
    doc = ("version: 1\nschema:\n  fields:\n    zeta: {type: boolean, description: Zeta.}\n"
           "    alpha: {type: boolean, description: Alpha.}\n")  # fmt: skip
    _apply(conn, clock, doc)
    s = config.current_schema(conn)
    assert s.extension_fields == ("zeta", "alpha")
    swapped = doc.replace("zeta", "tmp").replace("alpha", "zeta").replace("tmp", "alpha")
    swapped = swapped.replace("description: Zeta.", "description: X.").replace(
        "description: Alpha.", "description: Zeta.").replace("description: X.",
                                                             "description: Alpha.")  # fmt: skip
    r = config.apply(conn, clock, FakeNotifier(), swapped, dry_run=True, nonce=None)
    assert r.changed  # a reorder changes the prompt, so it is a change
    _apply(conn, clock, swapped)
    assert config.current_schema(conn).extension_fields == ("alpha", "zeta")
    assert config.current_schema(conn).digest != s.digest


@pytest.mark.usefixtures("extensions_on")
def test_a_schema_change_leads_the_dialog_and_notice_and_demotes_live_at_once(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _setup(conn, clock)
    with write_tx(conn):
        conn.execute("UPDATE addresses SET stage = 'live'")
    long_rules = "rules: {version: 1, rules: [" + ", ".join(
        f"{{id: r{i:02d}, when: {{field: priority, eq: high}}, then: [flag]}}" for i in range(30)
    ) + ", {id: rest, then: [leave]}]}\n"  # fmt: skip
    doc = SCHEMA_DOC + long_rules + "forward_allow_list: [{id: bob, address: bob@acme.example}]\n"
    with pytest.raises(StepupRequiredError) as ei:
        config.apply(conn, clock, FakeNotifier(), doc, dry_run=False, nonce=None)
    issued = stepup.issue(conn, clock, FakeStepper(), "config_apply", ei.value.extra["target"])
    assert issued.prompt.startswith(f"ecf: apply config: {config.SCHEMA_LEAD}; ")
    stepup.verify(conn, clock, FakeStepper(), issued.nonce_id)
    n = FakeNotifier()
    config.apply(conn, clock, n, doc, dry_run=False, nonce=issued.nonce_id)
    assert conn.execute("SELECT stage FROM addresses").fetchone()[0] == "assist"
    [data] = [json.loads(r[0]) for r in conn.execute(
        "SELECT data FROM audit WHERE event = 'stage.changed'")]  # fmt: skip
    assert data["to"] == "assist" and "classification schema changed" in data["reason"]
    assert any(f"Security-relevant config changed: {config.SCHEMA_LEAD}; " in body
               for _t, body in n.sent)  # fmt: skip


@pytest.mark.usefixtures("extensions_on")
def test_rules_default_rechecks_the_starter_ids_against_added_values(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _setup(conn, clock)
    _apply(conn, clock, "version: 1\nrules: {version: 1, rules: [{id: only, then: [leave]}]}\n")
    # with custom rules in force, an added value may be a starter rule id ...
    _apply(conn, clock, "version: 1\nschema: {category_values: {fraud_guard: Added.}}\n")
    # ... but going back to the starter rules is refused while it is
    with pytest.raises(InvalidInputError, match="rule id fraud_guard is a value"):
        config.parse(conn, "version: 1\nrules: default\n")


@pytest.mark.usefixtures("extensions_on")
def test_removing_an_added_category_confirmed_senders_use_is_refused(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _setup(conn, clock)
    _apply(conn, clock, SCHEMA_DOC)
    with write_tx(conn):
        conn.execute("INSERT INTO senders (address_id, sender_hash, domain, confirmed_category)"
                     " VALUES ('ap', 'h1', 'law.example', 'legal_notice')")  # fmt: skip
    with pytest.raises(InvalidInputError, match=r"legal_notice: a sender at law.example \(ap\)"):
        config.parse(conn, "version: 1\nschema: default\n")
    with pytest.raises(InvalidInputError, match="confirmed senders use"):
        config.parse(conn, "version: 1\nschema: {fields: {f: {type: boolean, description: F.}}}")


@pytest.mark.usefixtures("extensions_on")
def test_a_stored_extension_that_no_longer_compiles_fails_closed(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    """A later release adds a shipped value the extension also adds (simulated by storing one
    that clashes): the service classifies with the shipped schema under its own key, raises a
    System Error, and `schema: default` still applies."""
    _setup(conn, clock)
    before = claude_pins.address_key(conn, "ap")
    with write_tx(conn):
        conn.execute("INSERT INTO settings (key, value, updated_at, updated_by) VALUES"
                     " ('config.schema', ?, ?, 'test')",
                     (json.dumps({"category_values": {"invoice": "Clash."}}),
                      to_ts(clock.now())))  # fmt: skip
    s = config.current_schema(conn)
    assert s.fields == load_schema().fields and s.digest != load_schema().digest
    assert claude_pins.address_key(conn, "ap") != before  # no old gate carries over
    assert config.extension_problem(conn) is not None
    n = FakeNotifier()
    config.schema_tick(conn, clock, n)
    assert conn.execute("SELECT kind FROM alerts WHERE resolved_at IS NULL").fetchone()[0] == (
        "schema_extension")  # fmt: skip
    r = _apply(conn, clock, "version: 1\nschema: default\n")
    assert r.applied and r.schema is not None
    config.schema_tick(conn, clock, n)
    assert conn.execute("SELECT count(*) FROM alerts WHERE resolved_at IS NULL").fetchone()[0] == 0
    assert config.current_schema(conn).digest == load_schema().digest


# ---- extensions off in v2.0.0 (OD-481) ---------------------------------------------------------


def _open_alerts(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT kind FROM alerts WHERE resolved_at IS NULL")]


def test_with_extensions_off_a_schema_section_is_refused(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _setup(conn, clock)
    with pytest.raises(InvalidInputError, match=r"schema extensions arrive in v2\.1\.0"):
        config.parse(conn, SCHEMA_DOC)
    with pytest.raises(InvalidInputError, match="remove the `schema` section"):
        config.apply(conn, clock, FakeNotifier(), SCHEMA_DOC, dry_run=True, nonce=None)
    with pytest.raises(InvalidInputError, match=r"arrive in v2\.1\.0"):  # empty is refused too
        config.parse(conn, "version: 1\nschema: {}\n")
    assert config.current(conn)["schema"] is None
    assert config.current_schema(conn).digest == load_schema().digest


def test_with_extensions_off_a_stored_extension_is_ignored_and_default_removes_it(
    conn: sqlite3.Connection, clock: FakeClock, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Applied on an rc (switch on), then this version (switch off): the shipped schema and its
    gate key, no System Error, and `schema: default` removes it without demoting anyone."""
    _setup(conn, clock)
    shipped_key = claude_pins.address_key(conn, "ap")
    monkeypatch.setattr(ecf.schema, "EXTENSIONS_ENABLED", True)
    _apply(conn, clock, SCHEMA_DOC)
    assert claude_pins.address_key(conn, "ap") != shipped_key
    monkeypatch.setattr(ecf.schema, "EXTENSIONS_ENABLED", False)
    assert config.current(conn)["schema"] is not None  # kept
    s = config.current_schema(conn)
    assert s.extension is None and s.digest == load_schema().digest
    assert claude_pins.address_key(conn, "ap") == shipped_key
    assert call(make_state(db_path, None), "GET", "/v1/schema").json()["extension"] is None
    assert config.extension_problem(conn) is None and config.rules_problem(conn) is None
    config.schema_tick(conn, clock, FakeNotifier())
    assert _open_alerts(conn) == []
    assert ops_doctor.schema(conn)["level"] == "warn"
    with write_tx(conn):
        conn.execute("UPDATE addresses SET stage = 'live' WHERE address_id = 'ap'")
    r = _apply(conn, clock, "version: 1\nschema: default\n")
    assert r.applied
    assert r.changes == [{"section": "schema", "change": "reset to built-in schema: unused while"
                          " extensions are off: fields -contract_stage; category"
                          " -legal_notice"}]  # fmt: skip
    assert config.current(conn)["schema"] is None
    stage = conn.execute("SELECT stage FROM addresses WHERE address_id = 'ap'").fetchone()[0]
    assert stage == "live"  # the effective schema didn't change
    assert ops_doctor.schema(conn)["level"] == "ok"


def test_with_extensions_off_rules_that_use_a_stored_extension_give_way_to_the_starter_rules(
    conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup(conn, clock)
    monkeypatch.setattr(ecf.schema, "EXTENSIONS_ENABLED", True)
    _apply(conn, clock, SCHEMA_DOC + LABEL_STAGE)
    monkeypatch.setattr(ecf.schema, "EXTENSIONS_ENABLED", False)
    starter = [r.id for r in rules.load_starter_rules(load_schema()).rules]
    assert [r.id for r in config.current_rules(conn).rules] == starter  # mail is still decided
    assert "unknown field 'contract_stage'" in (config.rules_problem(conn) or "")
    n = FakeNotifier()
    config.schema_tick(conn, clock, n)
    assert _open_alerts(conn) == ["schema_extension"]
    with pytest.raises(InvalidInputError, match="the rules in force don't compile"):
        config.parse(conn, "version: 1\nschema: default\n")
    _apply(conn, clock, "version: 1\nschema: default\nrules: default\n")
    config.schema_tick(conn, clock, n)
    assert _open_alerts(conn) == []
    assert config.rules_problem(conn) is None
