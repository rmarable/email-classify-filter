"""`ecf approve` and `ecf item show` show what is proposed, a draft in full, before anything is
approved (§8.4; found in the V1.5 closing run, step 15b). The service is faked: these tests cover
what the CLI prints and when it calls the approve route."""

from __future__ import annotations

from typing import Any

import pytest
from typer.testing import CliRunner

from ecf import cli_items
from ecf.cli import app

DRAFT = "Hello,\n\nThanks, we got it.\x1b[31m\n\nBest regards,"


def _item(actions: list[dict[str, Any]], *, done: bool = False) -> dict[str, Any]:
    proposal: dict[str, Any] = {
        "actions": [{k: v for k, v in a.items() if k != "mode"} for a in actions],
        "plan": {"actions": actions, "actor": {"action": "x", "reason": "asked for a reply"}},
    }
    if done:
        proposal["done"] = []
    return {"id": "f" * 64, "address_id": "ap", "sender": "Ann <ann@example.com>",
            "subject": "Hello", "proposal": proposal}  # fmt: skip


DRAFT_ITEM = _item([
    {"mode": "auto", "name": "label", "target": "customer_request"},
    {"mode": "approve", "name": "draft_reply", "target": None,
     "payload": {"to": "ann@example.com", "text": DRAFT}},
])  # fmt: skip
SEND_ITEM = _item([
    {"mode": "approve", "name": "forward_internal", "target": "lead",
     "payload": {"entry": "lead", "to": "lead@acme.example"}},
    {"mode": "approve", "name": "reply_template", "target": "received",
     "payload": {"to": "ann@example.com", "template": "received", "rendered": "ab"}},
])  # fmt: skip


def test_a_draft_is_shown_in_full_with_its_recipient() -> None:
    lines = cli_items.proposal_lines(DRAFT_ITEM)
    assert lines[0] == "proposed:"
    assert "  label customer_request" in lines
    assert "  save a draft reply to ann@example.com (never sent)   (needs your approval)" in lines
    assert "    | Thanks, we got it.\x1b[31m" in lines and "    | Best regards," in lines
    assert "  model's reason: asked for a reply" in lines


def test_sends_name_their_recipient_and_template() -> None:
    lines = cli_items.proposal_lines(SEND_ITEM)
    assert "  forward to lead@acme.example (lead)   (needs your approval)" in lines
    assert "  send template 'received' to ann@example.com   (needs your approval)" in lines


def test_nothing_is_shown_once_done_or_without_a_proposal() -> None:
    done = _item(DRAFT_ITEM["proposal"]["plan"]["actions"], done=True)
    assert cli_items.proposal_lines(done) == []
    assert cli_items.proposal_lines({"proposal": None}) == []


class FakeClient:
    def __init__(self, item: dict[str, Any]) -> None:
        self.item = item
        self.posted: list[str] = []

    def __enter__(self) -> FakeClient:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def get(self, path: str) -> dict[str, Any]:
        assert path == "/v1/items/ffffffff"
        return self.item

    def request(self, method: str, path: str, body: Any = None) -> dict[str, Any]:
        self.posted.append(path)
        return {"status": "executing"}


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeClient:
    client = FakeClient(DRAFT_ITEM)

    def make(_paths: object) -> FakeClient:
        return client

    monkeypatch.setattr(cli_items, "LocalClient", make)
    return client


def test_approve_shows_the_draft_and_does_nothing_on_no(fake: FakeClient) -> None:
    r = CliRunner().invoke(app, ["approve", "ffffffff"], input="n\n")
    assert r.exit_code == 1
    assert "| Thanks, we got it." in r.output and "\x1b" not in r.output  # escapes removed
    assert "save a draft reply to ann@example.com" in r.output
    assert fake.posted == []


def test_approve_on_yes_or_with_the_flag(fake: FakeClient) -> None:
    r = CliRunner().invoke(app, ["approve", "ffffffff"], input="y\n")
    assert r.exit_code == 0 and "approved: running now" in r.output
    r = CliRunner().invoke(app, ["approve", "ffffffff", "--yes"])
    assert r.exit_code == 0 and "Approve?" not in r.output
    assert fake.posted == ["/v1/items/ffffffff/approve"] * 2
