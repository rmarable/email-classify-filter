from typing import Any

import anyio
import httpx

from ecf.errors import PROBLEM_CONTENT_TYPE
from ecf_server.api import ServiceState, create_app

STATE = ServiceState(install="t", token="secret-token", started_at="2026-10-01T12:00:00.000000Z")
APP = create_app(STATE)
AUTH = {"Authorization": "Bearer secret-token"}


def get(path: str, headers: dict[str, str] | None = None) -> httpx.Response:
    async def call() -> httpx.Response:
        transport = httpx.ASGITransport(app=APP)
        async with httpx.AsyncClient(transport=transport, base_url="http://ecf") as c:
            return await c.get(path, headers=headers or {})

    return anyio.run(call)


def body(r: httpx.Response) -> dict[str, Any]:
    data: dict[str, Any] = r.json()
    return data


def test_health_is_open() -> None:
    r = get("/v1/health")
    assert r.status_code == 200 and body(r) == {"ok": True}


def test_status_needs_the_token() -> None:
    for headers in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": "secret-token"}):
        r = get("/v1/status", headers)
        assert r.status_code == 401
        assert r.headers["content-type"].startswith(PROBLEM_CONTENT_TYPE)
        assert body(r)["code"] == "unauthorized"
    r = get("/v1/status", AUTH)
    assert r.status_code == 200
    assert body(r)["install"] == "t" and body(r)["api_version"] == 1
    assert body(r)["slack"] == {"installed": False}


def test_unknown_route_is_problem_json() -> None:
    r = get("/v1/nope", AUTH)
    assert r.status_code == 404 and body(r)["code"] == "not_found"


def request(method: str, path: str, token: str | None = None) -> httpx.Response:
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    async def call() -> httpx.Response:
        transport = httpx.ASGITransport(app=APP)
        async with httpx.AsyncClient(transport=transport, base_url="http://ecf") as c:
            return await c.request(method, path, headers=headers)

    return anyio.run(call)


def test_session_tokens() -> None:
    made = request("POST", "/v1/sessions", "secret-token")
    assert made.status_code == 201
    work, sid = body(made)["profile_token"], body(made)["session_id"]
    assert body(made)["profile"] == "work"
    assert request("GET", "/v1/status", work).status_code == 200  # WORK may read status
    refused = request("POST", "/v1/sessions", work)  # but can't mint more tokens
    assert refused.status_code == 403 and body(refused)["code"] == "forbidden_profile"
    assert request("DELETE", f"/v1/sessions/{sid}", work).status_code == 403
    assert request("DELETE", f"/v1/sessions/{sid}", "secret-token").status_code == 200
    assert request("GET", "/v1/status", work).status_code == 401  # revoked
    assert request("DELETE", f"/v1/sessions/{sid}", "secret-token").status_code == 404


def test_non_ascii_token_is_401_not_500() -> None:
    async def call() -> httpx.Response:
        transport = httpx.ASGITransport(app=APP)
        async with httpx.AsyncClient(transport=transport, base_url="http://ecf") as c:
            return await c.get("/v1/status", headers=[(b"Authorization", b"Bearer caf\xe9")])

    r = anyio.run(call)
    assert r.status_code == 401 and body(r)["code"] == "unauthorized"


def test_tokens_are_not_in_repr() -> None:
    assert "secret-token" not in repr(STATE)
