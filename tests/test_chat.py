"""The ChatSurface port: contract tests every adapter must pass (V1.2 step 1: the fake; the Slack
adapter joins in step 3)."""

from __future__ import annotations

import pytest

from ecf_server.chat import Button, Card, ChatSurface, FakeChat, Identity, RouteRef
from ecf_server.slack_chat import SlackChat


@pytest.fixture(params=["fake", "slack"])
def chat(request: pytest.FixtureRequest) -> ChatSurface:
    if request.param == "slack":
        from tests.test_slack_out import FakeWeb  # noqa: PLC0415

        return SlackChat(FakeWeb())
    return FakeChat()


def test_create_invite_post_thread_update_pin_archive(chat: ChatSurface) -> None:
    route = chat.create_route("ecf-default-ap")
    chat.invite(route, "U123")
    card = Card(
        "Possible fraud: bank details changed",
        fields=(("From", "billing@vendor-a.example"), ("Subject", "<!channel> *urgent*")),
        buttons=(Button("show_excerpt", "Show excerpt", "ref-1"),),
        note="This acts on the email only. ecf never pays anything.",
    )
    top = chat.post(route, card, identity=Identity("AP inbox", ":inbox_tray:"))
    reply = chat.post(route, Card("Escalated"), thread=top)
    assert reply.route == route and reply.ts != top.ts
    chat.update(top, Card("Resolved"))
    chat.pin(top)
    chat.ephemeral(route, "U123", "excerpt")
    chat.archive(route)


def test_the_fake_records_plain_text_and_opaque_refs() -> None:
    chat = FakeChat()
    route = chat.create_route("ecf-default-ap")
    chat.invite(route, "U123")
    approve = Button("approve", "Approve: archive email", "g-1")
    top = chat.post(route, Card("t", fields=(("Subject", "<!channel>"),), buttons=(approve,)))
    chat.pin(top)
    post = chat.posts[0]
    assert post["card"]["fields"] == (("Subject", "<!channel>"),)  # kept as plain text
    assert post["card"]["buttons"][0]["ref"] == "g-1" and post["pinned"]
    assert chat.routes[route.channel]["members"] == ["U123"]
    with pytest.raises(KeyError):
        chat.update(type(top)(RouteRef("C-other"), top.ts), Card("x"))
