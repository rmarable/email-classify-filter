"""Schema v3, stage S2 (OD-498; schema doc draft 3 §9, §10, §15): v2 plus the fixed safety items,
preset A still asked v2, the migration, derived eval labels."""

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
    load_schema,
    load_schema_v2,
    local_fields,
    local_schema,
    shipped_fallback,
    with_fixed,
)
from ecf_server import actor, classifier, claude_pins, db, evalrun
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
V2_ANSWER: dict[str, Any] = {
    "category": "invoice", "priority": "high", "requires_action": True, "requires_reply": False,
    "payment_related": True, "deadline_mentioned": False, "sender_type": "vendor",
    "fraud_risk": "none",
}  # fmt: skip


# ---- the schema --------------------------------------------------------------------------------


def test_v3_is_v2_plus_the_fixed_items_and_priority_texts() -> None:
    v2, v3 = load_schema_v2(), load_schema()
    assert v3.version == 3 and v3 is load_schema()
    check_safety_core(v3)
    assert list(v3.fields) == [*v2.fields, *FIXED_ITEMS]  # after v2's: v2's prefix order kept
    for name, f in v2.fields.items():
        g = v3.fields[name]
        assert (g.kind, g.description, g.values) == (f.kind, f.description, f.values), name
        if name != "priority":
            assert g.value_descriptions == f.value_descriptions, name
    assert v2.fields["priority"].value_descriptions == ()
    assert v3.fields["priority"].value_descriptions == PRIORITY
    for name, text in WORDING.items():
        assert v3.fields[name].kind.value == "boolean"
        assert v3.fields[name].description == text
        assert SAFETY_FIELDS[name] == ("boolean", ())
    for name in ("fraud_risk", "payment_related", "requires_action", "requires_reply",
                 "deadline_mentioned", "category", "sender_type"):  # fmt: skip
        assert v3.fields[name] == v2.fields[name]  # v2 wording exactly


def test_an_extension_cant_take_a_fixed_item_name() -> None:
    got = extend_schema(load_schema(), None)
    assert got is load_schema()
    from ecf.schema import check_extension  # noqa: PLC0415

    for name in FIXED_ITEMS:
        bad = check_extension(
            load_schema(), {"fields": {name: {"type": "boolean", "description": "x"}}}
        )
        assert bad and "can't change one" in bad[0]


# ---- the §9 table --------------------------------------------------------------------------


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
    load_schema().validate(full)
    assert full["phishing"] is True and full["bank_detail_change"] is False
    kept = with_fixed(V2_ANSWER | {"phishing": False, "category": "spam_or_phishing"})
    assert kept["phishing"] is False
    assert local_fields(full) == V2_ANSWER | {"category": "spam_or_phishing"}


# ---- eval labels (§10, P8) -------------------------------------------------------------------


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
    assert mapped == 1 and fields["claims_internal"] is True and "rule" not in fields
    assert evalrun._mapped({"rule": None}) == ({}, 0)  # pyright: ignore[reportPrivateUsage]
    v3 = with_fixed(V2_ANSWER) | {"phishing": True}
    assert evalrun._mapped(v3)[0] == v3  # pyright: ignore[reportPrivateUsage]
    r = CaseResult(id="x", correct=True, got=V2_ANSWER)
    assert evalrun._lacks_fixed(r.got)  # pyright: ignore[reportPrivateUsage]


# ---- preset A: still asked v2 (P6) ---------------------------------------------------------


def test_the_local_model_is_asked_v2_and_its_prompt_digest_and_key_dont_move() -> None:
    v2, v3 = load_schema_v2(), load_schema()
    assert local_schema(v3) is v2 and local_schema(v2) is v2
    release = json.loads((ROOT / "src/ecf_server/data/release.json").read_text())
    # the v2.2.3 classifier prompt digest, unchanged by schema v3
    assert release["prompt_digest"] == classifier.prompt_digest(v3) == classifier.prompt_digest(v2)
    assert classifier.system_prompt(v3) == classifier.INSTRUCTIONS + v2.prompt_block
    for name in FIXED_ITEMS:
        assert f"- {name} " not in classifier.system_prompt(v3)
    assert claude_pins.local_key(v3) == claude_pins.local_key(v2)


def test_a_fallback_or_extended_schema_is_asked_as_v2_with_the_same_extension() -> None:
    ext: dict[str, Any] = {
        "fields": {"mentions_nda": {"type": "boolean", "description": "Mentions an NDA."}}
    }
    asked = local_schema(extend_schema(load_schema(), ext))
    assert asked.digest == extend_schema(load_schema_v2(), ext).digest
    assert asked.version == 2 and "mentions_nda" in asked.fields
    fb = local_schema(shipped_fallback("x"))
    assert fb.fallback_for == shipped_fallback("x").fallback_for and fb.version == 2


def test_a_v2_reply_is_completed_and_a_v3_shaped_reply_refused() -> None:
    v3 = load_schema()
    got = classifier.parse(json.dumps(V2_ANSWER | {"sender_type": "family"}), v3)
    assert got is not None and got["private_individual"] is True and got["phishing"] is False
    v3.validate(got)
    # the model is asked v2 strictly: an answer with the fixed items isn't what it was asked
    assert classifier.parse(json.dumps(with_fixed(V2_ANSWER)), v3) is None
    assert classifier.parse(json.dumps(V2_ANSWER), load_schema_v2()) == V2_ANSWER


def test_the_local_actor_sees_only_v2_fields() -> None:
    msg = actor.user_message("hi", with_fixed(V2_ANSWER), [], "tok")
    shown = json.loads(msg.split("Classification: ", 1)[1].split("\n", 1)[0])
    assert shown == V2_ANSWER


# ---- migration 0036 --------------------------------------------------------------------------


def _v2_db(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    every = db._migration_files()  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(db, "_migration_files", lambda: [m for m in every if m[0] < 36])
    c = db.connect(db_path)
    db.migrate(c)
    c.execute("INSERT INTO addresses (address_id, email, sensitivity, preset, created_at)"
              " VALUES ('ap', 'ap@acme.example', 'standard', 'A', 't')")  # fmt: skip
    monkeypatch.setattr(db, "_migration_files", lambda: every)
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
