"""Test helpers shared by several test files, kept here so a test file can be deleted without
taking another's helpers with it (v3.0.0 removal plan, `planning-docs/REMOVE-LOCAL-MODELS.md`)."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from typing import Any

import httpx

from ecf_server import ollama
from ecf_server.clock import FakeClock, to_ts
from ecf_server.db import write_tx
from ecf_server.ollama import Client

PIN = ollama.load_pin()
LSOF_OK = "p4242\nn127.0.0.1:11434\n"
PS_ENV = ("/opt/homebrew/bin/ollama serve HOME=/Users/me PATH=/usr/bin OLLAMA_NUM_PARALLEL=1"
          " OLLAMA_NO_CLOUD=1")  # fmt: skip
GOOD = {"category": "invoice", "priority": "medium", "requires_action": True,
        "requires_reply": False, "payment_related": True, "deadline_mentioned": False,
        "sender_type": "vendor", "fraud_risk": "none"}  # fmt: skip
BEC = {"category": "vendor_change_request", "priority": "high", "requires_action": True,
       "requires_reply": False, "payment_related": True, "deadline_mentioned": False,
       "sender_type": "vendor", "fraud_risk": "high"}  # fmt: skip


def _run(lsof: str | Exception = LSOF_OK, ps: str = PS_ENV) -> Callable[[list[str]], str]:
    def run(cmd: list[str]) -> str:
        out = lsof if "lsof" in cmd[0] else ps
        if isinstance(out, Exception):
            raise out
        return out

    return run


def check_kw(**kw: Any) -> dict[str, Any]:
    return {"run": _run(**kw), "platform": "darwin"}


class FakeOllama:
    """Enough of Ollama's API for these tests: tags, version, ps, pull and copy."""

    def __init__(self, *, installed: bool = True, upstream: str = PIN.digest) -> None:
        self.models: dict[str, str] = {PIN.tag: upstream}
        if installed:
            self.models[PIN.ecf_tag] = PIN.digest
        self.upstream = upstream
        self.pulls = 0

    def handler(self, req: httpx.Request) -> httpx.Response:  # noqa: PLR0911 - one per endpoint
        path = req.url.path
        if path == "/api/version":
            return httpx.Response(200, json={"version": "0.35.0"})
        if path == "/api/tags":
            listed = [{"name": k, "digest": v} for k, v in self.models.items()]
            return httpx.Response(200, json={"models": listed})
        if path == "/api/ps":
            return httpx.Response(200, json={"models": [
                {"name": PIN.ecf_tag, "digest": PIN.digest, "size_vram": 9_000_000_000,
                 "context_length": 6144, "expires_at": "2026-10-09T10:05:00Z"}]})  # fmt: skip
        if path == "/api/pull":
            self.pulls += 1
            self.models[PIN.tag] = self.upstream
            lines = [{"status": "pulling manifest"},
                     {"status": "pulling bb72", "total": 100, "completed": 50},
                     {"status": "success"}]  # fmt: skip
            return httpx.Response(200, content="\n".join(json.dumps(x) for x in lines).encode())
        if path == "/api/copy":
            body = json.loads(req.content)
            self.models[body["destination"]] = self.models[body["source"]]
            return httpx.Response(200)
        if path == "/api/delete":
            self.models.pop(json.loads(req.content)["model"], None)
            return httpx.Response(200)
        return httpx.Response(404, json={"error": "not found"})

    def client(self) -> Client:
        return Client(transport=httpx.MockTransport(self.handler))


class ChatOllama(FakeOllama):
    def __init__(self, reply: str | Exception, prompt_tokens: int = 588) -> None:
        super().__init__()
        self.reply, self.prompt_tokens = reply, prompt_tokens
        self.bodies: list[dict[str, Any]] = []

    def handler(self, req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/chat":
            self.bodies.append(json.loads(req.content))
            if isinstance(self.reply, Exception):
                raise self.reply
            return httpx.Response(200, json={
                "message": {"role": "assistant", "content": self.reply}, "done_reason": "stop",
                "prompt_eval_count": self.prompt_tokens, "prompt_eval_cached_count": 519,
                "eval_count": 57, "prompt_eval_duration": 1, "eval_duration": 1_900_000_000,
                "load_duration": 1, "total_duration": 2})  # fmt: skip
        if req.url.path == "/api/generate":
            return httpx.Response(200, json={"done": True})
        return super().handler(req)


def add_address(
    conn: sqlite3.Connection, clock: FakeClock, aid: str, *, paused: bool = False
) -> None:
    with write_tx(conn):
        conn.execute("INSERT INTO addresses (address_id, email, sensitivity, preset, created_at,"
                     " paused) VALUES (?, ?, 'standard', 'A', ?, ?)",
                     (aid, f"{aid}@acme.example", to_ts(clock.now()), int(paused)))  # fmt: skip
