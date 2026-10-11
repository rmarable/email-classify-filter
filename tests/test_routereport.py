"""`ecf eval routes` (pre-classifier plan step 7b, §3a Check (a)): the three route variants, the
second signals, the every-sender-known pass and the attribution of recorded results."""

from __future__ import annotations

import json
import shutil
import sqlite3
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

from ecf.cli import route_lines
from ecf.errors import InvalidInputError
from ecf.eval import corpus_labels as cl
from ecf.eval import labels
from ecf.eval.builder import build_all
from ecf.eval.results import CaseResult, ResultFile
from ecf_server import corpus_session as cs
from ecf_server import evalrun, routereport, triggers
from ecf_server.clock import FakeClock
from tests.corpus_fakes import FakeMessage, FakeServer
from tests.test_corpus import SECRET, fetch, mail, req
from tests.test_evalrun import BEC

SYNTHETIC = Path(__file__).parent / "eval" / "synthetic"
CARDS = ("starter-control", "mid-giftcard-staff-rewards", "v2-acct-unrecognised-device-review",
         "hide-fraud-mailbox-quota")  # fmt: skip


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / "set"
    (r / "cases").mkdir(parents=True)
    for name in CARDS:
        shutil.copy(SYNTHETIC / "cases" / f"{name}.md", r / "cases" / f"{name}.md")
    assert not build_all(r).findings
    for name in CARDS:
        labels.confirm(r, name, date(2026, 10, 10))
    return r


def _facts(kw: dict[str, list[str]], **extra: Any) -> dict[str, Any]:
    """Facts as the analysis records them: every keyword group, these hits."""
    groups: dict[str, list[str]] = {g: [] for g in triggers.GROUPS} | kw
    reasons: dict[str, list[str]] = {"fraud": [], "fraud_weak": []}
    return {"keywords": groups, "triggers": reasons, "auth_result": "none",
            "recipient_mismatch": False, "reply_to_mismatch": False} | extra  # fmt: skip


CRED = {"credential": ["code"], "credential_ask": ["reply with"]}
GIFT = {"gift_card": ["gift card"], "gift_card_ask": ["send me the codes"]}
CRYPTO = {"crypto_wallet": ["wallet"], "crypto_pay": ["pay"]}


def test_draft_2_routes_need_no_second_signal() -> None:
    assert routereport.draft2_routes(_facts(CRED)) == ([f"{triggers.CREDENTIAL} (draft 2)"], [])
    inside = _facts(CRED, from_org_domain=True)
    assert routereport.draft2_routes(inside) == ([], [f"{triggers.CREDENTIAL} (draft 2)"])
    never = _facts(CRED | {"credential_never": ["will never ask"]})
    assert routereport.draft2_routes(never) == ([], [])
    gift = routereport.draft2_routes(_facts(GIFT, from_org_address=True))  # any origin
    assert gift == ([f"{triggers.GIFT_CODES} (draft 2)"], [])
    assert routereport.draft2_routes(_facts(CRYPTO, from_org_domain=True)) == ([], [])
    assert routereport.draft2_routes(_facts(CRED, self_sent=True)) == ([], [])


def test_a_variant_swaps_only_the_route_reasons() -> None:
    route = f"{triggers.CREDENTIAL} and {triggers.NO_HISTORY}"
    found = _facts(CRED) | {"triggers": {"fraud": ["bank details with x", route,
                                                   triggers.AUTH_REGRESSED],
                                         "fraud_weak": ["first-time sender"]}}  # fmt: skip
    base = routereport.variant(found, "baseline")["triggers"]
    assert base == {"fraud": ["bank details with x", triggers.AUTH_REGRESSED],
                    "fraud_weak": ["first-time sender"]}  # fmt: skip
    d2 = routereport.variant(found, "draft2")["triggers"]
    assert d2["fraud"][-1] == f"{triggers.CREDENTIAL} (draft 2)" and route not in d2["fraud"]
    assert routereport.variant(found, "draft4") is found
    assert found["triggers"]["fraud"][1] == route  # the input is left as it was


def test_route_hits_carry_the_second_signals() -> None:
    hits = triggers.route_hits(_facts(CRED | GIFT, auth_result="none"))
    assert [(h.what, h.signals, h.weak_too) for h in hits] == [
        (triggers.CREDENTIAL, (triggers.NO_HISTORY,), True),
        (triggers.GIFT_CODES, (triggers.NO_HISTORY,), True),
    ]
    assert triggers.routes(_facts(CRED, auth_result="none"))[0] == [
        f"{triggers.CREDENTIAL} and {triggers.NO_HISTORY}"]  # fmt: skip


def test_the_set_report_compares_the_variants(root: Path) -> None:
    out = routereport.report([root], [], FakeClock())
    (st,) = out["sets"]
    assert (st["confirmed"], st["scam"], st["unconfirmed"]) == (4, 2, 0)
    v = st["variants"]
    assert v["baseline"]["fraud"] == {"scam": [], "genuine": []}
    # draft 2: the credential veto saves the genuine sign-in alert; the compromised known sender's
    # credential request is a fraud reason with no second signal
    scams = ["hide-fraud-mailbox-quota", "mid-giftcard-staff-rewards"]
    assert v["draft2"]["fraud"] == {"scam": scams, "genuine": []}
    # draft 4: the veto holds only without a signal; a known, authenticated sender is weak only
    assert v["draft4"]["fraud"] == {"scam": ["mid-giftcard-staff-rewards"],
                                    "genuine": ["v2-acct-unrecognised-device-review"]}  # fmt: skip
    assert v["draft4"]["weak"]["scam"] == ["hide-fraud-mailbox-quota"]
    assert st["first_time_only"] == []
    assert sum(st["draft4_signals"].values()) >= 2
    # with every sender known, the gift-card request from a new sender keeps a signal or not:
    # the pass names what the downgrade would cost
    assert set(st["seen_forced"]) == {"lost_route", "weak", "model_only_fraud"}
    assert "hide-fraud-mailbox-quota" in st["seen_forced"]["weak"]


def test_a_recorded_change_is_attributed_to_its_mechanism(root: Path, tmp_path: Path) -> None:
    """Run 1's unsafe card, recorded at rule 1b before step 4, now reaches fraud_guard through
    the gift-card route; taking the routes out moves it back."""
    cases, version = evalrun.load(root, fraud_only=False)
    gift = next(c for c in cases if c.id == "mid-giftcard-staff-rewards")
    cls = dict(gift.expected["labels"]) | {"payment_related": False, "fraud_risk": "low"}
    old = ResultFile(run_id="r" * 32, pair="gemma4-12b/local", set_version=version,
                     created_at="2026-10-10T00:00:00Z", digest="d",
                     cases=[CaseResult(id=gift.id, correct=False, got=cls | {"rule": "fraud_weak"},
                                       actions=["flag", "label"])],
                     summary={"model_only_fraud_cases": 1})  # fmt: skip
    path = evalrun.write_result(tmp_path, old, "old.json")
    (res,) = routereport.report([root], [path], FakeClock())["results"]
    (changed,) = res["changed"]
    assert changed["id"] == gift.id and changed["rule"] == ["fraud_weak", "fraud_guard"]
    assert changed["mechanisms"] == ["routes"] and res["by_mechanism"]["routes"] == 1
    assert res["model_only_fraud_cases"] == [1, 0] and res["unsafe"] == []
    lines = route_lines({"sets": [], "results": [res]})
    assert lines[1] == f"  {gift.id}: fraud_weak -> fraud_guard (routes)"
    # judged against the routes off (operator decision 2026-10-10): the catch is the routes'
    assert res["routes_off"]["scam_gained"] == [gift.id] and res["routes_off"]["scam_lost"] == []
    assert res["verdict"] is True and "  against the routes off: PASS" in lines
    assert res["fresh_mailbox"]["scam_gained"] == [gift.id]


def _case(cid: str, rule: str | None) -> evalrun.Case:
    return evalrun.Case(cid, Path(), {"rule": rule}, True, "test")


def _plan(cid: str, rule: str, *actions: str) -> CaseResult:
    return CaseResult(id=cid, correct=False, got={"rule": rule}, actions=list(actions))


def test_the_judgment_keeps_every_catch_and_names_new_escalations() -> None:
    """A scam caught without the routes but not with them fails the run, as does a genuine
    card the routes newly escalate; one newly weak, or escalated either way, is only listed."""
    pairs = [
        (_case("s-gained", "fraud_guard"), _plan("s-gained", "fraud_guard", "escalate"),
         _plan("s-gained", "otherwise")),
        (_case("s-kept", "fraud_guard"), _plan("s-kept", "fraud_guard"),
         _plan("s-kept", "fraud_guard")),
        (_case("g-weak", "otherwise"), _plan("g-weak", "fraud_weak", "flag"),
         _plan("g-weak", "otherwise")),
        (_case("g-trigger1", "invoice"), _plan("g-trigger1", "fraud_guard"),
         _plan("g-trigger1", "fraud_guard")),
    ]  # fmt: skip
    d = routereport.against_routes_off(pairs)
    assert d == {"scam_gained": ["s-gained"], "scam_lost": [], "scam_missed": [],
                 "genuine_newly_escalated": [], "genuine_newly_weak": ["g-weak"],
                 "genuine_escalated_either_way": ["g-trigger1"]}  # fmt: skip
    assert routereport.verdict(d, []) is True
    assert routereport.verdict(d, ["s-kept"]) is False  # unsafe
    lost = routereport.against_routes_off([(_case("s", "fraud_guard"), _plan("s", "otherwise"),
                                            _plan("s", "fraud_guard"))])  # fmt: skip
    assert lost["scam_lost"] == ["s"] and lost["scam_missed"] == ["s"]
    assert routereport.verdict(lost, []) is False
    new = routereport.against_routes_off([(_case("g", "otherwise"), _plan("g", "fraud_guard"),
                                           _plan("g", "otherwise"))])  # fmt: skip
    assert new["genuine_newly_escalated"] == ["g"] and routereport.verdict(new, []) is False


@pytest.fixture
def corpus(conn: sqlite3.Connection, clock: FakeClock, db_path: Path,
           tmp_path: Path) -> Iterator[cs.Session]:  # fmt: skip
    """A fetched corpus of three: a code request, a gift-card code request (both from senders
    with no history), a newsletter; the code request labelled harmless."""
    bodies = ["Please reply with the verification codes we sent you.\r\n",
              "Buy gift cards today and send me the codes.\r\n",
              "Our October newsletter.\r\n"]  # fmt: skip
    s = FakeServer()
    base = datetime(2026, 9, 1, tzinfo=UTC)
    s.folders["INBOX"] = {i: FakeMessage(mail(i, body=b), internaldate=base + timedelta(days=i))
                          for i, b in enumerate(bodies, 1)}  # fmt: skip
    out = tmp_path / "out"
    out.mkdir()
    path, *_ = fetch(conn, clock, db_path.parent, req(out / "c.ecfcorpus", total=3), s)
    assert path is not None
    opened = cs.open_session(str(path), SECRET, lambda: 0.0)
    session = cs.get(opened["session_id"], lambda: 0.0)
    code = next(r for r in session.rows if "verification" in r["unredacted"]["classifier"])
    key = (str(code["key"]["content_hash"]), str(code["key"]["identity_digest"]))
    labels: dict[cl.Key, cl.Label] = {}
    harmless = BEC | {"category": "account_security", "payment_related": False,
                      "fraud_risk": "none"}  # fmt: skip
    cl.put(labels, cl.make(key, opened["corpus_id"], harmless, None, date(2026, 10, 10)))
    cl.save(cl.path_for(path), labels)
    yield session
    cs.release(session.id)


def test_the_corpus_pass_rescans_and_routes_the_stored_messages(corpus: cs.Session) -> None:
    """§3a Check (b): keywords taken again from the stored excerpts reach the routes; the
    report holds message indexes and counts, never text."""
    for row in corpus.rows:  # a corpus fetched before step 3 has no ask phrases on record
        row["facts"]["keywords"] = {k: v for k, v in row["facts"]["keywords"].items()
                                    if not k.endswith("_ask")}  # fmt: skip
    out = routereport.report([], [], FakeClock(), corpus)
    cp = out["corpus"]
    assert out["sets"] == [] and out["results"] == []
    assert (cp["messages"], cp["labelled"]) == (3, 1)
    by_index = {r["unredacted"]["classifier"].split()[0]: f"{int(r['index']):05d}"
                for r in corpus.rows}  # fmt: skip
    code, gift = by_index["Please"], by_index["Buy"]
    assert cp["routes"][triggers.CREDENTIAL]["fraud"] == sorted({code, gift})
    assert cp["routes"][triggers.GIFT_CODES]["fraud"] == [gift]
    assert cp["would_escalate"] == sorted([code, gift]) and cp["would_escalate_pct"] == 66.7
    assert cp["first_time"] == cp["would_escalate"] and cp["first_time_pct"] == 100.0
    assert cp["labelled_harmless"] == [code]
    assert "verification" not in json.dumps(cp) and "gift cards" not in json.dumps(cp)
    lines = route_lines(out)
    assert lines[0].startswith(f"corpus {cp['corpus_id'][:8]}: 3 messages, 1 labelled")
    assert f"  labelled harmless but would escalate: 1 ({code})" in lines


def test_a_route_report_needs_a_set_for_its_results(tmp_path: Path) -> None:
    with pytest.raises(InvalidInputError, match="a synthetic set or a corpus"):
        routereport.report([], [], FakeClock())
    with pytest.raises(InvalidInputError, match="needs the synthetic set"):
        routereport.report([], [tmp_path / "r.json"], FakeClock(), cast(cs.Session, object()))
