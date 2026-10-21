"""The Mistral API for `ecf eval grade` (eval generate plan, Adapters; build step 3; ADR 0034).

The service holds the key (`mistral-api-key`, written by `ecf models api-key set --provider
mistral`) and makes every call, so the key never reaches the client (operator decision
2026-10-20). One call grades one email: `POST /v1/chat/completions` with a `json_schema`
response format, strict, and no tools field (docs.mistral.ai/studio/conversations/
structured-output/custom, fetched 2026-10-20: the answer is the JSON text in
`choices[0].message.content`; usage in `prompt_tokens` and `completion_tokens`). Only a
generated set's grading may call it: the route checks the set before sending. One try per call:
a 429 comes back as `rate_limit` with Mistral's wait, and the client paces and stops. Nothing
here logs the prompt or the answer.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any, cast

import httpx

from ecf.errors import InvalidInputError
from ecf_server.clock import Clock, to_ts
from ecf_server.db import write_tx
from ecf_server.secretstore import SecretStore

KEY_SECRET = "mistral-api-key"  # noqa: S105 - the secret store's entry name, not a secret
HOST = "api.mistral.ai"
BASE_URL = f"https://{HOST}"
TIMEOUT_S = 60.0  # one grading call (J19)
KEY_CHECK_TIMEOUT_S = 5.0  # under the CLI's 10 s socket timeout, as Jev's
CONNECT_S, WRITE_S, POOL_S = 5.0, 10.0, 5.0
MAX_TOKENS = 2048  # the answer: labels for one email
MAX_PROMPT_CHARS = 400_000
# ecf's adapter text: format only, no guidance on content (decision 1)
SYSTEM_PROMPT = "Answer with JSON that matches the given schema, and nothing else."


def default_transport() -> httpx.BaseTransport:
    """The real HTTPS transport (tests pass their own)."""
    return httpx.HTTPTransport(trust_env=False)


def http_client(transport: httpx.BaseTransport | None = None, *,
                read_s: float = TIMEOUT_S) -> httpx.Client:  # fmt: skip
    return httpx.Client(base_url=BASE_URL,
                        timeout=httpx.Timeout(connect=min(CONNECT_S, read_s), read=read_s,
                                              write=min(WRITE_S, read_s),
                                              pool=min(POOL_S, read_s)),
                        trust_env=False, follow_redirects=False,
                        transport=transport or default_transport())  # fmt: skip


def _headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}", "Accept": "application/json"}


def check_key(key: str, transport: httpx.BaseTransport | None = None, *,
              timeout_s: float = KEY_CHECK_TIMEOUT_S) -> None:  # fmt: skip
    """One `GET /v1/models` (no email content) within `timeout_s`; raises when Mistral doesn't
    accept the key."""
    try:
        with http_client(transport, read_s=timeout_s) as client:
            r = client.get("/v1/models", headers=_headers(key))
    except httpx.TimeoutException:
        raise InvalidInputError(f"Mistral didn't accept it: no answer in {timeout_s:.0f} s;"
                                " nothing was stored") from None  # fmt: skip
    except httpx.HTTPError:
        raise InvalidInputError("Mistral didn't accept it: network error; nothing was"
                                " stored") from None  # fmt: skip
    if r.status_code in {401, 403}:
        raise InvalidInputError(f"Mistral didn't accept it: the key was refused (HTTP"
                                f" {r.status_code}); nothing was stored")  # fmt: skip
    if r.status_code != 200:
        raise InvalidInputError(f"Mistral didn't accept it: HTTP {r.status_code}; nothing was"
                                " stored")  # fmt: skip


def _audit(conn: sqlite3.Connection, now: str, event: str, data: dict[str, Any]) -> None:
    conn.execute("INSERT INTO audit (ts, address_id, event, actor, outcome, data)"
                 " VALUES (?, NULL, ?, 'os_user', 'ok', ?)",
                 (now, event, json.dumps(data)))  # fmt: skip


def set_key(conn: sqlite3.Connection, clock: Clock, store: SecretStore, key: str | None,
            transport: httpx.BaseTransport | None = None) -> dict[str, Any]:  # fmt: skip
    """Store the Mistral key after one call shows it works, or remove it (`None`). Audited
    without the key."""
    now = to_ts(clock.now())
    had = store.get(KEY_SECRET) is not None
    if key is None:
        store.delete(KEY_SECRET)
        with write_tx(conn):
            _audit(conn, now, "models.mistral_key_cleared", {"had": had})
        return {"provider": "mistral", "stored": False, "had": had}
    check_key(key, transport)
    store.set(KEY_SECRET, key)
    with write_tx(conn):
        _audit(conn, now, "models.mistral_key_set", {"replaced": had})
    return {"provider": "mistral", "stored": True, "replaced": had}


def body(model: str, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
    return {"model": model,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                         {"role": "user", "content": prompt}],
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": "answer", "schema": schema,
                                                "strict": True}},
            "max_tokens": MAX_TOKENS}  # fmt: skip


_KIND = re.compile(r"[A-Za-z0-9_.-]{1,40}")


def error_kind(r: httpx.Response) -> str:
    """The provider's error type and code, as short fixed labels only (never its message text),
    so a refusal explains itself without logging content."""
    try:
        doc: Any = json.loads(r.content)
    except ValueError:
        return ""
    if not isinstance(doc, dict):
        return ""
    d = cast(dict[str, Any], doc)
    err = d.get("error")
    inner = cast(dict[str, Any], err) if isinstance(err, dict) else d
    parts = [str(v) for k in ("type", "code") if (v := inner.get(k)) is not None]
    return "/".join(p for p in parts if _KIND.fullmatch(p))


def _wait(headers: httpx.Headers) -> float | None:
    try:
        v = float(headers.get("retry-after", ""))
    except ValueError:
        return None
    return v if 0 <= v <= 3600 else None


def complete(store: SecretStore, model: str, prompt: str,  # noqa: PLR0911 - one return a status
             schema: dict[str, Any],
             transport: httpx.BaseTransport | None = None) -> dict[str, Any]:  # fmt: skip
    """One call. Returns `{status, answer?, usage, detail, retry_after?}`: status `ok`,
    `rate_limit`, `auth`, `provider` (5xx), `failed`; `detail` is a kind, never Mistral's text."""
    if len(prompt) > MAX_PROMPT_CHARS:
        raise InvalidInputError("the prompt is too long for one grading call")
    key = store.get(KEY_SECRET)
    if key is None:
        raise InvalidInputError("no Mistral API key: set it with `ecf models api-key set"
                                " --provider mistral`")  # fmt: skip
    try:
        with http_client(transport) as client:
            r = client.post("/v1/chat/completions", json=body(model, prompt, schema),
                            headers=_headers(key))  # fmt: skip
    except httpx.TimeoutException:
        return {"status": "failed", "detail": "timed out", "usage": {}}
    except httpx.HTTPError:
        return {"status": "failed", "detail": "network error", "usage": {}}
    kind = f", {k}" if (k := error_kind(r)) else ""
    if r.status_code == 429:  # a rate limit or a plan's capacity: the type and code say which
        return {"status": "rate_limit", "detail": f"HTTP 429{kind}", "usage": {},
                "retry_after": _wait(r.headers)}  # fmt: skip
    if r.status_code == 401:
        return {"status": "auth", "detail": f"key refused (HTTP 401{kind})", "usage": {}}
    if r.status_code == 403:  # the key works (it listed models); this call is forbidden
        return {"status": "auth", "detail": f"call forbidden (HTTP 403{kind})", "usage": {}}
    if r.status_code >= 500:
        return {"status": "provider", "detail": f"HTTP {r.status_code}{kind}", "usage": {}}
    if r.status_code != 200:
        return {"status": "failed", "detail": f"HTTP {r.status_code}{kind}", "usage": {}}
    return _read(r.content)


def _read(raw: bytes) -> dict[str, Any]:  # noqa: PLR0911 - one return an outcome
    try:
        doc: Any = json.loads(raw)
    except ValueError:
        return {"status": "failed", "detail": "not JSON", "usage": {}}
    if not isinstance(doc, dict):
        return {"status": "failed", "detail": "not JSON", "usage": {}}
    d = cast(dict[str, Any], doc)
    raw_usage = d.get("usage")
    usage = ({k: v for k, v in cast(dict[str, Any], raw_usage).items()
              if k in {"prompt_tokens", "completion_tokens"} and isinstance(v, int)}
             if isinstance(raw_usage, dict) else {})  # fmt: skip
    choices = d.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return {"status": "failed", "detail": "no choice", "usage": usage}
    choice = cast(dict[str, Any], choices[0])
    message = choice.get("message")
    msg = cast(dict[str, Any], message) if isinstance(message, dict) else {}
    if msg.get("tool_calls"):
        return {"status": "failed", "detail": "tool call", "usage": usage}
    if (finish := choice.get("finish_reason")) != "stop":
        return {"status": "failed", "detail": f"finish {finish}", "usage": usage}
    content = msg.get("content")
    try:
        answer: Any = json.loads(content) if isinstance(content, str) else None
    except ValueError:
        answer = None
    if not isinstance(answer, dict):
        return {"status": "failed", "detail": "not one JSON object", "usage": usage}
    return {"status": "ok", "answer": answer, "usage": usage, "detail": ""}
