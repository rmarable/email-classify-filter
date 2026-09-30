"""A typed facade over `slack_sdk` (SPEC §10.1; §17.2: one facade per untyped library). The adapter
built on it is `slack_runtime.py` (with `slack_in.py` and `slack_out.py`).

slack_sdk 3.44.1 behavior relied on (V1.2 real-service test 0a, 2026-09-29, §21.1): the built-in
Socket Mode client (`slack_sdk.socket_mode.SocketModeClient`) connects with an app-level token and
delivers `interactive` requests (`block_actions`, `view_submission`) to its request listeners on
its own threads; an acknowledgement is a `SocketModeResponse` with the envelope ID. Web API calls go
through the SDK's own method for each API method, which knows how each one is encoded.

Errors keep only Slack's short error code (`not_in_channel`, `invalid_auth`), never the SDK's
message, which embeds the response. The SDK's loggers are held at WARNING (`ecf.log`), since at
DEBUG they write request and event payloads.
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

_web: Any = importlib.import_module("slack_sdk")
_errors: Any = importlib.import_module("slack_sdk.errors")
_sm: Any = importlib.import_module("slack_sdk.socket_mode")
_sm_response: Any = importlib.import_module("slack_sdk.socket_mode.response")

TIMEOUT_S = 10
_log = logging.getLogger("slack_sdk.ecf")


class SlackError(Exception):
    """Slack answered with `ok: false`; `code` is Slack's short error code only."""

    def __init__(self, method: str, code: str, needed: str | None = None) -> None:
        super().__init__(f"{method}: {code}")
        self.method, self.code, self.needed = method, code, needed


class SlackNetworkError(Exception):
    """Slack couldn't be reached (the post is held and retried, §10.1)."""


@dataclass(frozen=True)
class Envelope:
    envelope_id: str
    type: str  # "interactive", "events_api", ...
    payload: dict[str, Any]
    retry_attempt: int | None
    retry_reason: str | None


class Web:
    def __init__(self, token: str, *, base_url: str | None = None) -> None:
        """`base_url` only in tests (a local server standing in for slack.com)."""
        kw: dict[str, Any] = {"base_url": base_url} if base_url else {}
        self._client: Any = _web.WebClient(token=token, timeout=TIMEOUT_S, logger=_log, **kw)

    @property
    def sdk_client(self) -> Any:
        """The SDK's WebClient, for the Socket Mode client only."""
        return self._client

    def call(self, method: str, **params: Any) -> dict[str, Any]:
        """`method` as Slack names it (`chat.postMessage`); returns the response data."""
        fn = getattr(self._client, method.replace(".", "_"))
        try:
            resp = fn(**params)
        except _errors.SlackApiError as exc:
            data: dict[str, Any] = dict(getattr(exc.response, "data", {}) or {})
            raise SlackError(
                method, str(data.get("error", "unknown")), data.get("needed")
            ) from None
        except (OSError, TimeoutError, _errors.SlackClientError) as exc:
            raise SlackNetworkError(f"{method}: {type(exc).__name__}") from None
        return dict(resp.data)


class Socket:
    """A Socket Mode connection. `on_envelope` runs on the SDK's threads: it must acknowledge
    at once (`ack`) and hand the work to a queue (§10.1)."""

    def __init__(self, app_token: str, web: Web, on_envelope: Callable[[Envelope], None]) -> None:
        self._client: Any = _sm.SocketModeClient(
            app_token=app_token,
            web_client=web.sdk_client,
            logger=_log,
            auto_reconnect_enabled=True,
        )
        self._on_envelope = on_envelope
        self._client.socket_mode_request_listeners.append(self._dispatch)

    def connect(self) -> None:
        """Slack's refusal (a bad app-level token) comes back as `SlackError` with its code."""
        try:
            self._client.connect()
        except _errors.SlackApiError as exc:
            data: dict[str, Any] = dict(getattr(exc.response, "data", {}) or {})
            raise SlackError("apps.connections.open", str(data.get("error", "unknown"))) from None

    def close(self) -> None:
        self._client.close()

    def is_connected(self) -> bool:
        return bool(self._client.is_connected())

    def ack(self, envelope_id: str, payload: dict[str, Any] | None = None) -> None:
        resp = _sm_response.SocketModeResponse(envelope_id=envelope_id, payload=payload)
        self._client.send_socket_mode_response(resp)

    def _dispatch(self, _client: Any, req: Any) -> None:
        env = Envelope(
            envelope_id=str(req.envelope_id),
            type=str(req.type),
            payload=dict(req.payload or {}),
            retry_attempt=req.retry_attempt,
            retry_reason=req.retry_reason,
        )
        try:
            self._on_envelope(env)
        except Exception as exc:  # never let a handler bug kill the SDK's listener thread
            _log.warning("slack.handler_failed %s", type(exc).__name__)
