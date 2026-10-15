"""Schema v3, stage S2 (OD-498; schema doc draft 3 §9, §10, §15): v2 plus the fixed safety items,
the migration, derived eval labels. Preset A, which was still asked v2, went in v3.0.0 (ADR
0031)."""

import itertools
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from ecf.eval import fixed_labels
from ecf.eval.results import CaseResult
from ecf.ids import AddressId, StableId
from ecf.schema import (
    FIXED_FROM_V2,
    FIXED_ITEMS,
    SAFETY_FIELDS,
    check_safety_core,
    derive_fixed,
    derive_labels,
    extend_schema,
    from_v2,
    load_schema,
    load_schema_v2,
    with_fixed,
)
from ecf_server import db, evalrun
from ecf_server.clock import FakeClock
from ecf_server.items import create_item

ROOT = Path(__file__).resolve().parents[1]

# Operator-approved wording (2026-10-13), verbatim
WORDING = {
    "bank_detail_change": "Says the bank account, payee or check address that money goes to or"
    " comes from has changed, asks us to update it, or asks us to switch from bank transfer or"
    " check to another method. Yes even when the email is also an invoice, a lure or a"
    " statement.",
    "regulator_claim": "Comes from, or claims to come from, a regulator, government agency, court"
    " or tax authority acting toward us (a notice, audit, inquiry or filing demand), genuine or"
    " not. No for an email that only mentions one, such as a newsletter.",
    "phishing": "An attempt to deceive us: the sender or the premise is false. For example a"
    " fake invoice, notice or alert; a fake security or sign-in alert; someone posing as a"
    " colleague, executive, relative or company we know; a request to change where money is"
    " sent; or an extortion threat. It often asks for a code, password, payment, or personal,"
    " staff or bank details, or asks us to click to verify or call a number. No when a genuine"
    " sender asks for these through their real channel, and no for plain unsolicited marketing."
    " Instructions aimed at an automated assistant don't by themselves make an email phishing.",
    "claims_internal": "Presents itself as from a colleague, co-worker or executive of our"
    " organization, genuine or not. (Whether the sender really is internal is computed, not"
    " judged.)",
    "private_individual": "A private person writing for themselves from a personal address, not"
    " on behalf of an organization: a friend, a relative, or someone we don't know.",
}
PRIORITY = (
    "Can wait; nobody needs to see it soon.",
    "Look at it within a working day.",
    "Look at it today.",
    "Needs attention within hours: a deadline today, an outage, or money or access at risk.",
)
# S3b (OD-500; plan §12, operator-approved 2026-10-13, verbatim): the reworded texts
S3B_TEXTS = {
    ("category", None): "What this email is primarily about. A fake email gets the value of what"
    " it pretends to be (a fake invoice is an invoice). When several fit, choose in this order: a"
    " sign-in or security alert; money (a bill to pay, then a payment made or received, then an"
    " account or policy we hold); then what the email is about; a plain notice, a personal note"
    " or none of these last.",
    ("category", "account_security"): "Sign-in alert, password reset, verification code, or"
    " security warning about an account, genuine or fake.",
    ("category", "marketing"): "Sent to many: newsletters, promotions, advertising, and other"
    " unsolicited bulk mail.",
    ("sender_type", "unknown"): "Can't tell the role, or a colleague or co-worker of ours,"
    " genuine or not. Otherwise an impostor takes the role they claim.",
}
# two operator decisions of 2026-10-13 (EDT), after the S3b runs (plan §12), verbatim
FRAUD_MEDIUM = ("Any request to move money, data, credentials or access on this email's say-so,"
                " whatever the pattern. A request to call a number or follow a link to secure an"
                " account counts too.")  # fmt: skip
REMOVED = {"category": ("vendor_change_request", "regulatory", "spam_or_phishing"),
           "sender_type": ("team",)}  # fmt: skip
V2_ANSWER: dict[str, Any] = {
    "category": "invoice", "priority": "high", "requires_action": True, "requires_reply": False,
    "payment_related": True, "deadline_mentioned": False, "sender_type": "vendor",
    "fraud_risk": "none",
}  # fmt: skip


# The v3 schema itself: v2 plus the fixed items and priority texts, and an extension can't take a
# fixed item's name.


def test_v3_is_v2_plus_the_fixed_items_and_priority_texts() -> None:
    v2, v3 = load_schema_v2(), load_schema()
    assert v3.version == 3 and v3 is load_schema()
    check_safety_core(v3)
    assert list(v3.fields) == [*v2.fields, *FIXED_ITEMS]  # after v2's: v2's prefix order kept
    for name, f in v2.fields.items():
        if name in REMOVED:  # category and sender_type: checked below (S3b)
            continue
        g = v3.fields[name]
        assert (g.kind, g.description, g.values) == (f.kind, f.description, f.values), name
        if name not in ("priority", "fraud_risk"):  # fraud_risk: checked below
            assert g.value_descriptions == f.value_descriptions, name
    assert v2.fields["priority"].value_descriptions == ()
    assert v3.fields["priority"].value_descriptions == PRIORITY
    for name, text in WORDING.items():
        assert v3.fields[name].kind.value == "boolean"
        assert v3.fields[name].description == text
        assert SAFETY_FIELDS[name] == ("boolean", ())
    for name in ("payment_related", "requires_action", "requires_reply",
                 "deadline_mentioned"):  # fmt: skip
        assert v3.fields[name] == v2.fields[name]  # v2 wording exactly
    fr2, fr3 = v2.fields["fraud_risk"], v3.fields["fraud_risk"]  # S3b: medium gains a cue
    assert (fr3.kind, fr3.description, fr3.values) == (fr2.kind, fr2.description, fr2.values)
    assert fr3.value_descriptions[2] == FRAUD_MEDIUM
    assert fr3.value_descriptions[:2] + fr3.value_descriptions[3:] == (
        fr2.value_descriptions[:2] + fr2.value_descriptions[3:]
    )
    for name, gone in REMOVED.items():  # S3b: four values removed, four texts reworded
        f, g = v2.fields[name], v3.fields[name]
        assert g.values == tuple(v for v in f.values if v not in gone), name
        assert g.description == S3B_TEXTS.get((name, None), f.description), name
        for v, d in zip(g.values, g.value_descriptions, strict=True):
            assert d == S3B_TEXTS.get((name, v), f.value_descriptions[f.values.index(v)]), v
        for removed in gone:  # P12: no text names a removed value
            assert removed not in g.description + " ".join(g.value_descriptions)


def test_an_extension_cant_take_a_fixed_item_name() -> None:
    got = extend_schema(load_schema(), None)
    assert got is load_schema()
    from ecf.schema import check_extension  # noqa: PLC0415

    for name in FIXED_ITEMS:
        bad = check_extension(
            load_schema(), {"fields": {name: {"type": "boolean", "description": "x"}}}
        )
        assert bad and "can't change one" in bad[0]


# The §9 derivation table: each v2 value sets exactly its item, other values derive nothing, and a
# v2 answer is completed to v3 without overwriting items it already gives.


ROWS = [  # (field, value, the item it sets)
    ("category", "vendor_change_request", "bank_detail_change"),
    ("category", "regulatory", "regulator_claim"),
    ("category", "spam_or_phishing", "phishing"),
    ("sender_type", "team", "claims_internal"),
    ("sender_type", "staff", "claims_internal"),  # v1's name for team
    ("sender_type", "person", "private_individual"),
    ("sender_type", "friend", "private_individual"),
    ("sender_type", "family", "private_individual"),
    ("sender_type", "regulator", "regulator_claim"),
]


@pytest.mark.parametrize(("field", "value", "item"), ROWS)
def test_each_table_row_sets_its_item_and_no_other(field: str, value: str, item: str) -> None:
    got = derive_fixed(V2_ANSWER | {field: value})
    assert got == {k: k == item for k in FIXED_ITEMS}


def test_every_other_v2_value_derives_nothing() -> None:
    v2 = load_schema_v2()
    mapped = {(f, v) for f, v, _ in ROWS}
    for field in ("category", "sender_type"):
        for value in v2.fields[field].values:
            if (field, value) not in mapped:
                assert not any(derive_fixed(V2_ANSWER | {field: value}).values()), value
    assert derive_fixed({}) == dict.fromkeys(FIXED_ITEMS, False)  # absent source: false
    both = derive_fixed({"category": "regulatory", "sender_type": "team"})
    assert both["regulator_claim"] and both["claims_internal"]


def test_with_fixed_completes_a_v2_answer_and_keeps_given_items() -> None:
    full = with_fixed(V2_ANSWER | {"category": "spam_or_phishing"})
    assert full["phishing"] is True and full["bank_detail_change"] is False
    assert full["category"] == "spam_or_phishing"  # values left as they are (from_v2 maps them)
    kept = with_fixed(V2_ANSWER | {"phishing": False, "category": "spam_or_phishing"})
    assert kept["phishing"] is False


def test_from_v2_derives_the_items_then_reads_removed_values_as_least_informative() -> None:
    """S3b (D3): a v2 answer brought to v3: the items first (they carry what the removed value
    said), then category -> other, sender_type team/staff -> unknown; valid v3."""
    for field, value, item in ROWS:
        got = from_v2(V2_ANSWER | {field: value})
        load_schema().validate(got)
        assert got[item] is True, (field, value)
        if value in REMOVED.get(field, ()) or value == "staff":
            assert got[field] == {"category": "other", "sender_type": "unknown"}[field]
        else:
            assert got[field] == value  # a kept value stays
    kept = from_v2(V2_ANSWER | {"category": "vendor_change_request", "bank_detail_change": False})
    assert kept["bank_detail_change"] is False and kept["category"] == "other"  # given item kept
    assert from_v2(V2_ANSWER) == with_fixed(V2_ANSWER)  # nothing removed: only the items


# Eval labels (§10, P8): fixed-item labels are derived from the v2 labels, the committed set's files
# stay unchanged, and v2 results are completed to v3 when rescored.


def test_labels_are_derived_and_a_missing_source_is_unlabelled() -> None:
    assert derive_labels({}) == {}
    assert derive_labels({"category": "invoice"}) == {"bank_detail_change": False}
    # regulator_claim has two sources: false needs both labelled, true needs one
    assert derive_labels({"category": "invoice", "sender_type": "vendor"}) == {
        "bank_detail_change": False, "regulator_claim": False, "claims_internal": False,
        "private_individual": False}  # fmt: skip
    assert derive_labels({"sender_type": "regulator"}) == {
        "regulator_claim": True,
        "claims_internal": False,
        "private_individual": False,
    }
    assert derive_labels({"category": "regulatory"})["regulator_claim"] is True
    # `unknown` is unlabelled, never false
    assert derive_labels({"category": "other", "sender_type": "unknown"}) == {
        "bank_detail_change": False
    }
    # phishing is never derived (split by hand), even from spam_or_phishing
    assert "phishing" not in derive_labels({"category": "spam_or_phishing"})
    # a label the card states itself is kept
    assert "bank_detail_change" not in derive_labels(
        {"category": "invoice", "bank_detail_change": True}
    )


def test_the_eval_set_gets_derived_labels_without_changing_its_files() -> None:
    root = ROOT / "tests/eval/synthetic"
    before = (root / "labels.jsonl").read_bytes()
    cases, version = evalrun.load(root, fraud_only=False)
    assert (root / "labels.jsonl").read_bytes() == before
    assert version == evalrun.set_version(root)
    rows = {json.loads(line)["id"]: json.loads(line) for line in before.decode().splitlines()}
    hand, _ = fixed_labels.current(root)  # hand-set fixed items (fixed_labels.jsonl)
    for c in cases:
        raw: dict[str, Any] = rows[c.id]["expected"].get("labels") or {}
        got: dict[str, Any] = c.expected.get("labels") or {}
        base = hand.get(c.id, {}) | raw  # card-stated > hand-set > derived (S2 review F4)
        assert got == base | derive_labels(base)
        # phishing is never derived: only the card or the hand-set file gives it
        assert "phishing" not in got or "phishing" in raw or "phishing" in hand.get(c.id, {})
        if "sender_type" not in raw:
            assert "claims_internal" not in got and "private_individual" not in got


def test_an_injection_target_extends_to_the_fixed_items() -> None:
    assert evalrun.injection_hit("spam_or_phishing", {"category": "billing_inquiry",
                                                      "phishing": True})  # fmt: skip
    assert not evalrun.injection_hit("spam_or_phishing", {"category": "billing_inquiry",
                                                          "phishing": False})  # fmt: skip
    assert evalrun.injection_hit("phishing", {"phishing": True})
    assert evalrun.injection_hit("notification", {"category": "notification"})
    assert not evalrun.injection_hit("archive", {"category": "notification"})
    assert evalrun.TARGET_ITEMS == {"vendor_change_request": "bank_detail_change",
                                    "regulatory": "regulator_claim",
                                    "spam_or_phishing": "phishing"}  # fmt: skip


def test_a_v2_result_is_completed_to_v3_when_rescored() -> None:
    fields, mapped = evalrun._mapped(V2_ANSWER | {"sender_type": "staff", "rule": "x"})  # pyright: ignore[reportPrivateUsage]
    # staff -> team (v1), then team (removed in S3b) -> unknown, the item kept: two mapped
    assert mapped == 2 and fields["claims_internal"] is True and "rule" not in fields
    assert fields["sender_type"] == "unknown"
    assert evalrun._mapped({"rule": None}) == ({}, 0)  # pyright: ignore[reportPrivateUsage]
    v3 = with_fixed(V2_ANSWER) | {"phishing": True}
    assert evalrun._mapped(v3)[0] == v3  # pyright: ignore[reportPrivateUsage]
    # an S2-era v3 answer (items given) with a removed value: the value is read, and its implied
    # item set true even where the answer said false, as 0037 does (B2), and counted
    s2 = with_fixed(V2_ANSWER) | {"category": "regulatory", "regulator_claim": False}
    got, n = evalrun._mapped(s2)  # pyright: ignore[reportPrivateUsage]
    assert n == 1 and got["category"] == "other" and got["regulator_claim"] is True
    assert got["phishing"] is False  # other items as answered
    assert evalrun._raised(s2 | {"rule": "x"})  # pyright: ignore[reportPrivateUsage]
    assert not evalrun._raised(s2 | {"regulator_claim": True})  # pyright: ignore[reportPrivateUsage]
    assert not evalrun._raised(with_fixed(V2_ANSWER))  # pyright: ignore[reportPrivateUsage]
    staff = V2_ANSWER | {"sender_type": "staff", "claims_internal": False}  # v1 staff -> team
    assert evalrun._raised(staff)  # pyright: ignore[reportPrivateUsage]
    assert evalrun._mapped(staff)[0]["claims_internal"] is True  # pyright: ignore[reportPrivateUsage]
    r = CaseResult(id="x", correct=True, got=V2_ANSWER)
    assert evalrun._lacks_fixed(r.got)  # pyright: ignore[reportPrivateUsage]


# Migration 0036 backfills the fixed items on stored v2 classifications exactly as the Python
# table derives them.


def _v2_db(db_path: Path, monkeypatch: pytest.MonkeyPatch, *, before: int = 36,
           then: int = 36) -> sqlite3.Connection:  # fmt: skip
    """A database migrated to just before `before`, with an address; the next `db.migrate`
    runs up to `then` (0036 alone by default; 0037, S3b, has its own tests below)."""
    every = db._migration_files()  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(db, "_migration_files", lambda: [m for m in every if m[0] < before])
    c = db.connect(db_path)
    db.migrate(c)
    c.execute("INSERT INTO addresses (address_id, email, sensitivity, preset, created_at)"
              " VALUES ('ap', 'ap@acme.example', 'standard', 'A', 't')")  # fmt: skip
    monkeypatch.setattr(db, "_migration_files", lambda: [m for m in every if m[0] <= then])
    return c


def test_0036_backfills_every_v2_combination_as_the_table_does(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The SQL rows equal `derive_fixed`, an item absent from storage read as false (P13); only
    rows with a mapped value are written."""
    c = _v2_db(db_path, monkeypatch)
    v2 = load_schema_v2()
    combos = list(itertools.product(v2.fields["category"].values,
                                    (*v2.fields["sender_type"].values, "staff")))  # fmt: skip
    for n, (cat, st) in enumerate(combos):
        cls = json.dumps(V2_ANSWER | {"category": cat, "sender_type": st})
        create_item(c, FakeClock(), stable_id=StableId(f"s{n}"), address_id=AddressId("ap"),
                    content_hash=f"h{n}", classification=cls)  # fmt: skip
        c.execute("INSERT INTO fallback_shadow (stable_id, address_id, digest, outcome,"
                  " classification, created_at) VALUES (?, 'ap', 'd', 'ok', ?, 't')",
                  (f"s{n}", cls))  # fmt: skip
    create_item(c, FakeClock(), stable_id=StableId("none"), address_id=AddressId("ap"),
                content_hash="none")  # fmt: skip
    assert db.migrate(c) == ["0036_schema_v3.sql"]
    for n, (cat, st) in enumerate(combos):
        want = derive_fixed({"category": cat, "sender_type": st})
        for table in ("items", "fallback_shadow"):
            raw = c.execute(f"SELECT classification FROM {table} WHERE stable_id = ?",  # noqa: S608
                            (f"s{n}",)).fetchone()[0]  # fmt: skip
            got = json.loads(raw)
            assert {k: got.get(k, False) for k in FIXED_ITEMS} == want, (table, cat, st)
            assert set(got) - set(FIXED_ITEMS) == set(V2_ANSWER)
            assert all(got[k] is True for k in FIXED_ITEMS if k in got)  # only trues written
    row = c.execute("SELECT classification, schema_version FROM items WHERE stable_id = 'none'"
                    ).fetchone()  # fmt: skip
    assert row[0] is None
    assert {r[0] for r in c.execute("SELECT schema_version FROM items")} == {3}
    assert db.migrate(c) == []
    c.close()


@pytest.mark.parametrize(
    ("model", "fix", "want"),
    [
        # a Fix that changes the category away from a mapped value clears its item
        ({"category": "vendor_change_request", "sender_type": "vendor"},
         {"category": "invoice"}, {"bank_detail_change": False, "phishing": False,
                                   "regulator_claim": False}),
        ({"category": "invoice", "sender_type": "vendor"}, {"category": "spam_or_phishing"},
         {"bank_detail_change": False, "phishing": True, "regulator_claim": False}),
        # regulator_claim reads the Fix over the model's answer
        ({"category": "invoice", "sender_type": "regulator"}, {"category": "other"},
         {"bank_detail_change": False, "phishing": False, "regulator_claim": True}),
        ({"category": "regulatory", "sender_type": "vendor"}, {"sender_type": "team"},
         {"claims_internal": True, "private_individual": False, "regulator_claim": True}),
        ({"category": "other", "sender_type": "friend"}, {"sender_type": "unknown"},
         {"claims_internal": False, "private_individual": False, "regulator_claim": False}),
        ({"category": "other", "sender_type": "friend"}, {"priority": "low"}, {}),  # no source
    ],
)  # fmt: skip
def test_0036_gives_a_fix_the_items_its_fields_decide(
    db_path: Path, monkeypatch: pytest.MonkeyPatch, model: dict[str, str], fix: dict[str, str],
    want: dict[str, bool],
) -> None:  # fmt: skip
    c = _v2_db(db_path, monkeypatch)
    create_item(c, FakeClock(), stable_id=StableId("s"), address_id=AddressId("ap"),
                content_hash="h", classification=json.dumps(V2_ANSWER | model),
                human_correction=json.dumps(fix))  # fmt: skip
    db.migrate(c)
    raw = c.execute("SELECT classification, human_correction FROM items").fetchone()
    assert json.loads(raw[1]) == fix | want
    merged = json.loads(raw[0]) | json.loads(raw[1])
    view = derive_fixed(V2_ANSWER | model | fix)
    assert {k: merged.get(k, False) for k in FIXED_ITEMS} == view
    c.close()


def test_the_python_table_names_only_v2_values() -> None:
    v2 = load_schema_v2()
    for sources in FIXED_FROM_V2.values():
        for field, values in sources:
            assert values - {"staff"} <= set(v2.fields[field].values)


# Migration 0037 (S3b, OD-500, P9, P4): removed values are read on values only, gate passes are
# cleared, and the release names the checkpoint.


def test_0037_reads_removed_values_on_values_only_and_clears_gate_passes(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With 0036 applied, S2-era v3 rows (schema_version 3) holding a removed value: the item it
    implied set true (a Fix's own answer kept), then the value read as `from_v2` reads it; rows
    with no removed value untouched; `gate.passed_at` cleared, counts kept; idempotent."""
    c = _v2_db(db_path, monkeypatch, before=37, then=37)
    v3 = with_fixed(V2_ANSWER)
    rows = {
        "vcr": v3 | {"category": "vendor_change_request"},  # item false: the old value says it
        "reg": v3 | {"category": "regulatory", "regulator_claim": True},
        "spam": v3 | {"category": "spam_or_phishing"},
        "team": v3 | {"sender_type": "team"},
        "staff": v3 | {"sender_type": "staff"},
        "plain": v3 | {"category": "invoice", "sender_type": "regulator"},
    }
    for sid, cls in rows.items():
        create_item(c, FakeClock(), stable_id=StableId(sid), address_id=AddressId("ap"),
                    content_hash=sid, classification=json.dumps(cls))  # fmt: skip
        c.execute("INSERT INTO fallback_shadow (stable_id, address_id, digest, outcome,"
                  " classification, created_at) VALUES (?, 'ap', 'd', 'ok', ?, 't')",
                  (sid, json.dumps(cls)))  # fmt: skip
    c.execute("UPDATE items SET schema_version = 3")
    fixes = {"vcr": {"category": "vendor_change_request", "bank_detail_change": False},
             "spam": {"category": "spam_or_phishing"}, "team": {"sender_type": "team"},
             "plain": {"priority": "low"}}  # fmt: skip
    for sid, fix in fixes.items():
        c.execute("UPDATE items SET human_correction = ? WHERE stable_id = ?",
                  (json.dumps(fix), sid))  # fmt: skip
    c.execute("INSERT INTO gate (address_id, pair_key, reviewed, correct, passed_at)"
              " VALUES ('ap', 'A', 120, 110, '2026-10-01T00:00:00Z')")  # fmt: skip
    for h, cat in (("s1", "regulatory"), ("s2", "spam_or_phishing"), ("s3", "marketing")):
        c.execute("INSERT INTO senders (address_id, sender_hash, confirmed_category,"
                  " confirmed_at, dmarc_pass_count) VALUES ('ap', ?, ?, 't', 3)",
                  (h, cat))  # fmt: skip
    plain_before = c.execute("SELECT classification, human_correction FROM items"
                             " WHERE stable_id = 'plain'").fetchone()  # fmt: skip
    assert db.migrate(c) == ["0037_schema_v3_values_removed.sql"]
    want_item = {"vcr": "bank_detail_change", "reg": "regulator_claim", "spam": "phishing",
                 "team": "claims_internal", "staff": "claims_internal"}  # fmt: skip
    for sid, item in want_item.items():
        for table in ("items", "fallback_shadow"):
            got = json.loads(c.execute(f"SELECT classification FROM {table}"  # noqa: S608
                                       " WHERE stable_id = ?", (sid,)).fetchone()[0])  # fmt: skip
            assert got[item] is True, (table, sid)
            load_schema().validate(got)  # a valid v3 classification now
            assert got == from_v2(rows[sid]) | {item: True}, (table, sid)
    fix = {sid: json.loads(c.execute("SELECT human_correction FROM items WHERE stable_id = ?",
                                     (sid,)).fetchone()[0]) for sid in fixes}  # fmt: skip
    assert fix["vcr"] == {"category": "other", "bank_detail_change": False}  # the Fix's answer
    assert fix["spam"] == {"category": "other", "phishing": True}
    assert fix["team"] == {"sender_type": "unknown", "claims_internal": True}
    after = c.execute("SELECT classification, human_correction FROM items"
                      " WHERE stable_id = 'plain'").fetchone()  # fmt: skip
    assert tuple(after) == tuple(plain_before)  # no removed value: untouched
    g = c.execute("SELECT reviewed, correct, passed_at FROM gate").fetchone()
    assert tuple(g) == (120, 110, None)  # P4, A3: no category-era pass carries over
    senders = {r[0]: (r[1], r[2]) for r in c.execute(
        "SELECT sender_hash, confirmed_category, dmarc_pass_count FROM senders")}  # fmt: skip
    assert senders == {"s1": (None, 3), "s2": (None, 3), "s3": ("marketing", 3)}  # B4
    assert db.migrate(c) == []
    c.close()


def test_0037_checkpoints_and_the_release_names_it() -> None:
    assert 37 in db.CHECKPOINT_AFTER
    release = json.loads((ROOT / "src/ecf_server/data/release.json").read_text())
    newest = max(m[0] for m in db._migration_files())  # pyright: ignore[reportPrivateUsage]
    assert release["schema_version"] == newest >= 37  # 38 from v3.0.0 (ADR 0031), 39 OD-507
