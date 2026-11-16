"""The crash and failure states inventory-1b §4 found, and D115's behaviours (SPEC §21.4,
OD-841), each found by name in the goldens: v1 behaviour the planner must keep (D114 (1))."""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

TRACES = Path(__file__).parent / "traces"


@cache
def golden(name: str) -> dict[str, Any]:
    return json.loads((TRACES / f"{name}.json").read_text(encoding="utf-8"))


def event(name: str, ev: str, nth: int = 0) -> dict[str, Any]:
    return [e for e in golden(name)["events"] if e["name"] == ev][nth]


def crashes(name: str, ev: str) -> list[dict[str, Any]]:
    return [c for c in golden(name)["crashes"] if c["name"] == ev]


def sid(n: int) -> str:
    return f'["{n:02x}{"e" * 62}"]'


def changed(diff: dict[str, Any], table: str, col: str) -> dict[str, Any]:
    """key -> new value of `col`, for rows of `table` the diff changed."""
    return {r["key"]: r["set"][col][1] for r in diff.get(table, {}).get("~", []) if col in r["set"]}


def added(diff: dict[str, Any], table: str) -> list[dict[str, Any]]:
    return [r["row"] for r in diff.get(table, {}).get("+", [])]


def merged(commits: list[dict[str, Any]]) -> dict[str, Any]:
    """The commits' changes to items' status, in order (last wins)."""
    out: dict[str, Any] = {}
    for c in commits:
        out |= changed(c, "items", "status")
    return out


def transitions(events: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    for e in events:
        for c in e["commits"]:
            for r in added(c, "audit"):
                if r["event"] == "item.transitioned":
                    d = json.loads(r["data"])
                    out.append((r["stable_id"][:2], d["from"], d["to"]))
    return out


def test_1_queued_answer_without_its_text() -> None:
    """answers._queue commits awaiting_stepup, then answer_pending: a crash between leaves an
    item that reads as an approval, and the tick's answers.expire skips it."""
    [c] = [
        c
        for c in crashes("answer_fraud_slack_then_cli", "form answer")
        if changed(c["state"], "items", "status").get(sid(0)) == "awaiting_stepup"
        and "answer_pending" not in str(changed(c["state"], "items", "proposal"))
    ]
    assert transitions([c["recovery"]["tick"]]) == []


def test_2_released_held_item_stranded_at_proposed() -> None:
    """_release_held: held->proposed committed, decide.apply not run: nothing repairs it."""
    hits = [
        c
        for c in crashes("go_live_held_run", "set_stage live run")
        if changed(c["state"], "items", "status").get(sid(1)) == "proposed"
    ]
    assert hits
    for c in hits:
        assert [t for t in transitions([c["recovery"]["tick"]]) if t[0] == "01"] == []


def test_3_undo_after_lease_loss_fails_without_imap() -> None:
    ev = event("undo_lease_lost", "check")
    assert transitions([ev]) == [("00", "executed", "undoing"), ("00", "undoing", "undo_failed")]
    assert "mail" not in ev


def test_4_classified_audit_without_a_classification() -> None:
    ev = event("store_after_resolve", "classifier.store")
    assert ev["result"] is True
    assert any(r["event"] == "item.classified" for c in ev["commits"] for r in added(c, "audit"))
    assert all("classification" not in str(c.get("items", {})) for c in ev["commits"])


def test_5_to_actor_reports_awaiting_claude_after_a_lost_move() -> None:
    ev = event("to_actor_lost_cas", "decide.apply")
    assert ev["result"] == "awaiting_claude"
    assert transitions([ev]) == [("00", "classified", "resolved_manual")]


def test_6_awaiting_approval_without_a_grant() -> None:
    """request: T(->awaiting_approval) committed, the grant not: expire and post_held_cards join
    on an issued grant, so neither sees it."""
    [c] = [
        c
        for c in crashes("approve_cli_reversible", "approvals.request")
        if changed(c["state"], "items", "status").get(sid(0)) == "awaiting_approval"
        and not added(c["state"], "grants")
    ]
    tick = c["recovery"]["tick"]
    assert (
        tick["result"]["approvals.expire"] == 0 and tick["result"]["approvals.post_held_cards"] == 0
    )
    assert transitions([tick]) == []


def test_7a_rules_run_grant_before_its_transition() -> None:
    assert any(
        added(c["state"], "grants")
        and changed(c["state"], "items", "status").get(sid(0)) == "proposed"
        for c in crashes("live_archive", "decide.apply")
    )


def test_7b_requeue_spends_the_nonce_before_its_transition() -> None:
    assert any(
        changed(c["state"], "nonces", "consumed_at")
        and added(c["state"], "grants")
        and sid(0) not in changed(c["state"], "items", "status")
        for c in crashes("fail_send_high_requeue", "approvals.requeue")
    )


def test_7c_approved_with_the_grant_still_issued() -> None:
    assert any(
        changed(c["state"], "items", "status").get(sid(0)) == "approved"
        and not changed(c["state"], "grants", "status")
        for c in crashes("approve_cli_reversible", "approvals.approve")
    )


def test_7d_countdown_deleted_before_the_start() -> None:
    assert any(
        c["state"].get("delays", {}).get("-")
        and changed(c["state"], "items", "status").get(sid(0)) is None
        for c in crashes("send_high_delay", "advance_delays 301")
    )


def test_7f_send_settled_before_its_item() -> None:
    """send.settle: sent->sent committed, T not: the item stays failed_unknown for good."""
    hits = [
        c
        for c in crashes("send_unknown_reconciled", "check")
        if "sent" in changed(c["state"], "sent", "status").values()
        and sid(0) not in changed(c["state"], "items", "status")
    ]
    assert hits
    for c in hits:
        assert transitions(list(c["recovery"].values())) == []


def test_7g_item_closed_during_its_run() -> None:
    ev = event("executor_closes_item", "execute.run_once")
    assert ev["raises"]["type"] == "ConflictError"
    statuses = [changed(c, "grants", "status") for c in ev["commits"]]
    assert "consumed" in [v for d in statuses for v in d.values()]
    assert "voided" not in [v for d in statuses[1:] for v in d.values()]


def test_d115_outbound_disable_abort_leaves_outbound_on() -> None:
    ev = event("outbound_disable_abort", "outbound.disable")
    assert ev["raises"]["type"] == "ConflictError"
    assert all("outbound" not in str(c.get("addresses", {})) for c in ev["commits"])


def test_d115_not_sent_card_on_a_consumed_send() -> None:
    ev = event("outbound_disable_not_sent", "outbound.disable")
    posts = [json.loads(r["payload"]) for c in ev["commits"] for r in added(c, "jobs")]
    assert any("Not sent" in json.dumps(p) and sid(0)[2:10] in json.dumps(p) for p in posts)
    # the consumed grant is untouched: only issued/approved grants are voided (S5)
    olds = [
        r["set"]["status"][0]
        for c in ev["commits"]
        for r in c.get("grants", {}).get("~", [])
        if "status" in r["set"]
    ]
    assert olds and "consumed" not in olds


def test_d115_settle_marks_the_claim_done_on_a_closed_item() -> None:
    ev = event("review_settle_after_resolve", "claude_review.settle")
    assert ev["result"] == {"accepted": True, "errors": []}
    assert "done" in [v for c in ev["commits"] for v in changed(c, "claims", "state").values()]
    assert transitions([ev]) == []
