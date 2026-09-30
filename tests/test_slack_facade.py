"""The slack_sdk facade (V1.2 step 1), offline: error mapping and the listener guard."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from slack_sdk.errors import SlackApiError, SlackRequestError

from ecf_server import _slack
from ecf_server._slack import Envelope, SlackError, SlackNetworkError, Socket, Web

TOKEN = "xox" + "b-" + "0" * 30  # never used against Slack


def test_errors_keep_only_slacks_code(monkeypatch: pytest.MonkeyPatch) -> None:
    web = Web(TOKEN)
    response = SimpleNamespace(data={"ok": False, "error": "not_in_channel", "needed": None,
                                     "echo": "Invoice 42 subject"})  # fmt: skip

    def fails(**_kw: Any) -> None:
        raise SlackApiError("The server responded with: {'echo': 'Invoice 42 subject'}", response)

    monkeypatch.setattr(web.sdk_client, "chat_postMessage", fails)
    with pytest.raises(SlackError) as info:
        web.call("chat.postMessage", channel="C1", text="x")
    assert info.value.code == "not_in_channel"
    assert "Invoice 42" not in str(info.value) and info.value.__cause__ is None


def test_network_failures_are_their_own_error(monkeypatch: pytest.MonkeyPatch) -> None:
    web = Web(TOKEN)

    def down(**_kw: Any) -> None:
        raise SlackRequestError("connection refused")

    monkeypatch.setattr(web.sdk_client, "auth_test", down)
    with pytest.raises(SlackNetworkError):
        web.call("auth.test")


def test_a_handler_bug_never_kills_the_listener() -> None:
    got: list[Envelope] = []

    def handler(env: Envelope) -> None:
        got.append(env)
        raise RuntimeError("bug")

    sock = Socket("xa" + "pp-1-" + "0" * 30, Web(TOKEN), handler)  # nothing connects here
    req = SimpleNamespace(envelope_id="e1", type="interactive", payload={"type": "block_actions"},
                          retry_attempt=None, retry_reason=None)  # fmt: skip
    sock._dispatch(None, req)  # pyright: ignore[reportPrivateUsage]
    assert got == [Envelope("e1", "interactive", {"type": "block_actions"}, None, None)]
    sock.close()


def test_sdk_loggers_are_quiet() -> None:
    assert _slack._log.name.startswith("slack_sdk")  # pyright: ignore[reportPrivateUsage]
