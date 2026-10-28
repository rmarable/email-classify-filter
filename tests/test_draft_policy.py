"""When a draft is offered (M8 plan §2, Step 4): `policy.draft_refusal` (P-a, P-b), the email's
`language` fact it reads, the monitored-recipient refusal (P-c) and the withheld code (P-d)."""

from __future__ import annotations

import json
import sqlite3
from email.message import EmailMessage
from typing import Any

import pytest

from ecf.cli_items import proposal_lines
from ecf_server import actor, decide, draftcheck, fetch, isolate, outbound_plan, policy, ruletest
from ecf_server.clock import FakeClock
from ecf_server.db import write_tx
from ecf_server.dnscache import DnsCache
from ecf_server.isolate import Isolated
from ecf_server.mail.fake import FakeMailSource
from ecf_server.message import parse
from ecf_server.senderauth import AuthOutcome, evaluate
from tests.test_decide import item_row, make_address, make_classified
from tests.test_fetch import ADDR, Fn, run, setup, started
from tests.test_policy import _ctx  # pyright: ignore[reportPrivateUsage]
from tests.test_senderauth import FakeDns, mail

__all__ = ["setup"]  # the fixture, imported

REPLY: dict[str, Any] = {"category": "invoice", "priority": "normal", "requires_action": True,
         "requires_reply": True, "payment_related": False, "deadline_mentioned": False,
         "sender_type": "vendor", "fraud_risk": "none"}  # fmt: skip
ALLOWED: dict[str, Any] = {"triggers": {}, "auth_result": "pass", "sender_seen_before": True,
           "from_count": 1, "language": "en"}  # fmt: skip
ENGLISH = "Hello Pat,\n\nCould you send me the signed contract by Friday? Thanks for your help.\n"


def _reply_ctx(facts: dict[str, Any] | None = None, **cls: Any) -> policy.Context:
    return _ctx(classification=REPLY | cls, facts=ALLOWED | (facts or {}))


# Every clause failing at once, in the table's order: fixing each in turn shows the next code.
FAILING: list[tuple[str, dict[str, Any], dict[str, Any]]] = [
    ("not_authenticated", {"auth_result": "none"}, {}),
    ("quarantined", {"quarantined": True}, {}),
    ("unscanned", {"content_unscanned": True}, {}),
    ("bulk", {"bulk_signal": True}, {}),
    ("from_ambiguous", {"from_ambiguous": True}, {}),
    ("fraud", {"triggers": {"fraud": ["x"], "fraud_weak": ["y"], "regulator": ["z"]}}, {}),
    ("fraud_weak", {"triggers": {"fraud_weak": ["y"], "regulator": ["z"]}}, {}),
    ("regulator", {"triggers": {"regulator": ["z"]}}, {}),
    ("risk", {}, {"fraud_risk": "medium"}),
    ("risky_attachment", {"risky_attachment": True}, {}),
    ("language_other", {"language": "other"}, {}),
]


def test_draft_refusal_names_the_first_failing_clause_in_order() -> None:
    """Each code's precedence (§2's table): with every clause failing, the first is reported;
    fix it and the next one is."""
    for i, (code, _f, _c) in enumerate(FAILING):
        facts: dict[str, Any] = {}
        cls: dict[str, Any] = {}
        for _code, f, c in FAILING[i:]:
            facts |= f
            cls |= c
        trig = {k: v for _code, f, _c in FAILING[i:] for k, v in f.get("triggers", {}).items()}
        facts["triggers"] = trig
        assert policy.draft_refusal(_reply_ctx(facts, **cls)) == code, code
    assert policy.draft_refusal(_reply_ctx()) is None


@pytest.mark.parametrize(
    ("facts", "cls", "code"),
    [
        ({"from_count": 2}, {}, "from_ambiguous"),
        ({"from_count": 0}, {}, "from_ambiguous"),
        ({"triggers": {"lookalikes": ["vendor-a.example"]}}, {}, "fraud"),
        ({"triggers": {"unverified_payment": True}}, {}, "fraud"),
        ({}, {"regulator_claim": True}, "regulator"),
        ({}, {"fraud_risk": "high"}, "risk"),
        ({}, {"phishing": True}, "risk"),
        ({}, {"bank_detail_change": True}, "risk"),
        ({"sender_origin": "external"}, {"claims_internal": True}, "risk"),
        ({"language": "unknown"}, {}, "language_unknown"),
        ({"language": None}, {}, "language_unknown"),
    ],
)  # fmt: skip
def test_each_clause_refuses(facts: dict[str, Any], cls: dict[str, Any], code: str) -> None:
    ctx = _reply_ctx(facts, **cls)
    if "language" in facts and facts["language"] is None:
        ctx.facts.pop("language")  # an item fetched before the fact existed
    assert policy.draft_refusal(ctx) == code


def test_low_risk_and_an_internal_colleague_are_allowed() -> None:
    """P18 allows `fraud_risk` low, which `hide_blockers` counts as a fraud signal; and a
    colleague's own claim from inside isn't the outside-claims blocker."""
    assert policy.draft_refusal(_reply_ctx(fraud_risk="low")) is None
    assert policy.hide_blockers(_reply_ctx(fraud_risk="low"))  # the hide rule differs
    internal = _reply_ctx({"sender_origin": "internal"}, claims_internal=True)
    assert policy.draft_refusal(internal) is None


def test_check_language_false_skips_only_the_language_clause() -> None:
    old = _reply_ctx()
    old.facts.pop("language")
    assert policy.draft_refusal(old, check_language=False) is None
    unauth = _reply_ctx({"auth_result": "fail"})
    unauth.facts.pop("language")
    assert policy.draft_refusal(unauth, check_language=False) == "not_authenticated"


def test_the_actor_is_offered_a_draft_only_where_one_is_allowed() -> None:
    assert "draft_reply" in actor.offered(_reply_ctx())
    refused = actor.offered(_reply_ctx({"auth_result": "none"}))
    assert "draft_reply" not in refused and refused[-1] == "needs_clarification"


def test_a_proposed_draft_policy_refuses_is_dropped_with_its_code() -> None:
    ctx = _reply_ctx({"language": "other"})
    got = policy.proposal(ctx, policy.plan(ctx, frozenset()), "draft_reply", None, frozenset())
    assert isinstance(got, policy.Dropped) and got.why == "language_other"


# The email's language fact: computed in the isolate child, the eval's scratch alike.


@pytest.mark.parametrize(
    ("text", "want"),
    [
        (ENGLISH, "en"),
        ("Thanks!", "en"),  # short: the script test only
        ("", "unknown"),
        ("12345 -- 678 / 90", "unknown"),
        ("请把合同在星期五之前寄给我谢谢你的帮助 Pat", "other"),
        ("Hola Pat, por favor envíe el contrato firmado para el viernes. Gracias por su ayuda.",
         "other"),
        ("Bonjour, merci de nous envoyer le contrat signé pour vendredi. Nous vous remercions.",
         "other"),
        (ENGLISH + "Gruß, 王小明", "en"),  # a quoted name in another script
    ],
)  # fmt: skip
def test_email_language(text: str, want: str) -> None:
    assert draftcheck.email_language(text) == want


def test_the_child_returns_the_language_and_reads_it_back(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    parsed = parse(mail())
    result = isolate.isolated(parsed, evaluate(mail(), parsed, DnsCache(conn, clock,
                              lookup=FakeDns())))  # fmt: skip
    assert result.language == draftcheck.email_language(result.excerpts[1])
    assert isolate.decode(isolate.encode(result)) == result
    data = isolate.encode(result)
    del data["language"]  # a child of an earlier version
    assert isolate.decode(data).language == "unknown"
    data["language"] = "fr"  # a subverted child can't set another value
    with pytest.raises(ValueError, match="language"):
        isolate.decode(data)


def _english(n: int) -> bytes:
    m = EmailMessage()
    m["From"] = f"Sender {n} <s{n}@vendor-a.example>"
    m["To"] = "ap@acme.example"
    m["Subject"] = f"Contract {n}"
    m["Message-ID"] = f"<c{n}@vendor-a.example>"
    m.set_content(ENGLISH)
    return m.as_bytes()


def test_the_fetch_stores_the_childs_language(setup: sqlite3.Connection, clock: FakeClock) -> None:
    src = fetch_src(setup, clock)
    src.deliver(_english(0))

    def isolator(raw: bytes, max_scan_bytes: int) -> Isolated:
        return isolate.isolated(parse(raw, max_scan_bytes=max_scan_bytes), AuthOutcome("none", "x"))

    run(setup, clock, src, analyzer=Fn(lambda _p, _r: {}), isolator=isolator)
    facts = json.loads(setup.execute("SELECT facts FROM items").fetchone()["facts"])
    assert facts["language"] == "en" and facts["from_ambiguous"] is False


def test_without_the_child_the_language_is_unknown(
    setup: sqlite3.Connection, clock: FakeClock
) -> None:
    """The no-isolator path and the oversized path get `unknown`, so no draft (§1)."""
    src = fetch_src(setup, clock)
    src.deliver(_english(0))
    setup.execute("UPDATE addresses SET overrides = ? WHERE address_id = ?",
                  (json.dumps({"max_message_bytes": 300}), ADDR))  # fmt: skip
    src.deliver(_english(1) + b"\n" + b"x" * 400)
    run(setup, clock, src)
    rows = setup.execute("SELECT facts FROM items ORDER BY uid").fetchall()
    facts = [json.loads(r["facts"]) for r in rows]
    assert [f["language"] for f in facts] == ["unknown", "unknown"]
    assert "oversized" not in facts[0] and facts[1]["oversized"]


def fetch_src(conn: sqlite3.Connection, clock: FakeClock) -> FakeMailSource:
    src = FakeMailSource()
    started(conn, clock, src)
    return src


def test_the_message_facts_carry_from_ambiguous() -> None:
    two = mail().replace(b"From: ", b"From: a@vendor-a.example, ", 1)
    assert fetch.message_facts(parse(two))["from_ambiguous"] is True
    assert fetch.message_facts(parse(mail()))["from_ambiguous"] is False


def test_the_eval_scratch_computes_the_language_as_the_child_does() -> None:
    raw = _english(3)
    got = ruletest.Scratch(FakeClock()).facts(raw)
    actor_text = parse(raw).excerpts(lambda t: t)[1]
    assert got["language"] == "en" == draftcheck.email_language(actor_text)
    assert ruletest.FACTS_VERSION == 3


# P-c and P-d on the item.


@pytest.fixture
def live(conn: sqlite3.Connection, clock: FakeClock) -> None:
    make_address(conn, clock, "live")


def _propose_draft(conn: sqlite3.Connection, clock: FakeClock, sid: str) -> dict[str, Any]:
    item = item_row(conn, sid)
    ctx, p = decide.plan_for(conn, item)
    got = {"action": "draft_reply", "target": "", "reason": "r", "text": "Thanks, will do."}
    actor.decide_one(conn, clock, item, ctx, p, got, frozenset())
    return json.loads(item_row(conn, sid)["proposal"])


def test_a_draft_to_a_monitored_address_is_dropped_and_flagged(
    conn: sqlite3.Connection, clock: FakeClock, live: None
) -> None:
    with write_tx(conn):
        conn.execute("INSERT INTO addresses (address_id, email, sensitivity, preset, created_at)"
                     " VALUES ('nv', 'NEWS@vendor-a.example', 'standard', 'C', 't')")  # fmt: skip
    sid = make_classified(conn, clock, REPLY, ALLOWED)
    with pytest.raises(outbound_plan.Unresolved, match="monitored_recipient"):
        outbound_plan.resolve(conn, item_row(conn, sid), "draft_reply", None, "Thanks.")
    doc = _propose_draft(conn, clock, sid)
    assert doc["plan"]["dropped"] == [{"name": "draft_reply", "target": None,
                                       "why": "monitored_recipient"}]  # fmt: skip
    assert any(a["name"] == "flag" for a in doc["plan"]["actions"])


def test_a_draft_policy_drops_flags_the_item(
    conn: sqlite3.Connection, clock: FakeClock, live: None
) -> None:
    """P-d: a draft the policy drops (here the actor proposed one it wasn't offered) flags the
    item, with the code."""
    sid = make_classified(conn, clock, REPLY, ALLOWED | {"auth_result": "none"})
    doc = _propose_draft(conn, clock, sid)
    assert doc["plan"]["dropped"][0]["why"] == "not_authenticated"
    assert any(a["name"] == "flag" for a in doc["plan"]["actions"])
    assert doc["draft_withheld"] == "not_authenticated"


@pytest.mark.parametrize(
    ("cls", "facts", "want"),
    [
        (REPLY, ALLOWED | {"auth_result": "none"}, "not_authenticated"),
        (REPLY, ALLOWED, None),  # a draft is allowed
        (REPLY | {"requires_reply": False}, ALLOWED | {"auth_result": "none"}, None),  # no reply
    ],
)  # fmt: skip
def test_apply_records_why_no_draft_was_offered(
    conn: sqlite3.Connection, clock: FakeClock, live: None, cls: dict[str, Any],
    facts: dict[str, Any], want: str | None,
) -> None:  # fmt: skip
    sid = make_classified(conn, clock, cls, facts)
    stale = json.dumps({"draft_withheld": "stale", "questions": ["q"]})
    with write_tx(conn):  # an earlier plan's code never survives the merge
        conn.execute("UPDATE items SET proposal = ? WHERE stable_id = ?", (stale, sid))
    decide.apply(conn, clock, sid)
    doc = json.loads(item_row(conn, sid)["proposal"])
    assert "draft_withheld" in doc and doc["draft_withheld"] == want
    assert doc["questions"] == ["q"]


def test_the_actors_question_keeps_the_withheld_code(
    conn: sqlite3.Connection, clock: FakeClock, live: None
) -> None:
    """`decide.record` (the question path) computes the code too, so it never nulls it (P7)."""
    sid = make_classified(conn, clock, REPLY, ALLOWED | {"triggers": {"fraud_weak": ["y"]}})
    _, p = decide.plan_for(conn, item_row(conn, sid))
    decide.record(conn, clock, sid, p, "actor")
    assert json.loads(item_row(conn, sid)["proposal"])["draft_withheld"] == "fraud_weak"


def test_item_show_says_why_no_draft_was_offered() -> None:
    d: dict[str, Any] = {"proposal": {"draft_withheld": "language_other",
                      "plan": {"actions": [], "dropped": []}}}  # fmt: skip
    assert proposal_lines(d) == ["no draft offered: language_other"]
    assert proposal_lines({"proposal": {"draft_withheld": None}}) == []
