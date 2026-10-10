"""`ecf eval routes` (pre-classifier plan step 7b, §3a Check (a)): the three route variants, the
second signals, the every-sender-known pass and the attribution of recorded results."""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from ecf.cli import route_lines
from ecf.eval import labels
from ecf.eval.builder import build_all
from ecf.eval.results import CaseResult, ResultFile
from ecf_server import evalrun, routereport, triggers
from ecf_server.clock import FakeClock

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
