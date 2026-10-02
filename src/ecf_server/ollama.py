"""The local model server, Ollama (SPEC §5.2, §7.5, §12.2, §13.4; V1.3 step 1a).

- **One server, on loopback:** the address is a constant; `OLLAMA_HOST` is never read. Requests go
  to the native HTTP API with one fixed option set (a changed `num_ctx` reloads the model and drops
  the prompt cache, measured 2026-09-30).
- **Pin** (OD-235): `data/ollama.lock` names the upstream tag, its manifest digest and the name of
  ecf's own copy (`ecf/gemma4-12b:<release>`, made with the copy API, which keeps the digest). The
  digest is compared with the file in the wheel, never with a database value (I6).
- **Readiness** (I6, OD-240, OD-242, OD-245): before model work the service confirms that the only
  listener on port 11434 is 127.0.0.1, that the server's environment doesn't log requests (which
  writes email text to disk), and that ecf's copy carries the pinned digest. Anything it can't
  confirm is a fault: no model work, and a loud error. Ollama not running and the model missing are
  faults too, each with its own cause and fix.
- **No content leaves this module in logs, errors or metrics** (I5): errors carry a cause code, an
  HTTP status and at most the first 80 characters of Ollama's own error field; model output is
  returned to the caller only; `model_calls` stores numbers and fixed codes.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from importlib import resources
from typing import Any, Literal, cast

import httpx

from ecf import __version__
from ecf_server.clock import Clock, to_ts
from ecf_server.db import write_tx

HOST = "127.0.0.1"
PORT = 11434
BASE_URL = f"http://{HOST}:{PORT}"
TIMEOUT_S = 120.0  # §15.4: 120 s, one retry
PULL_TIMEOUT_S = 3600.0
NUM_CTX = 4096
NUM_PREDICT = {"classifier": 160, "actor": 320, "probe": 8}  # measured outputs: ~60 tokens
OPTIONS: dict[str, Any] = {"num_ctx": NUM_CTX, "temperature": 0}
KEEP_ALIVE = "5m"
_ERROR_CHARS = 80

Cause = Literal[
    "not_running",  # nothing listens on the port, or it doesn't answer
    "model_missing",  # ecf's copy isn't there (`ecf models install`)
    "digest_mismatch",  # ecf's copy isn't the pinned model
    "not_loopback",  # something listens on an address other than 127.0.0.1
    "unconfirmed",  # the listener or the server's environment couldn't be checked
    "logs_requests",  # OLLAMA_DEBUG_LOG_REQUESTS is set: request bodies are written to disk
    "timeout",
    "http",
    "server",  # Ollama answers but can't run the model now (out of memory, its runner stopped)
]
FIX: dict[str, str] = {
    "not_running": "run `ecf models serve install` (ecf's login item for Ollama), or start the"
    " Ollama you run yourself",
    "model_missing": "run `ecf models install`",
    "digest_mismatch": "run `ecf models install` to restore the pinned model",
    "not_loopback": "make Ollama listen on 127.0.0.1 only (unset OLLAMA_HOST, turn off network"
    " exposure)",
    "unconfirmed": "check that `lsof` (macOS) or `ss` (Linux) works and that Ollama runs as you"
    " (on Linux, Ollama's installer adds a system service that runs as another user: disable it"
    " and run `ecf models serve install`)",
    "logs_requests": "unset OLLAMA_DEBUG_LOG_REQUESTS and restart Ollama",
    "timeout": "check that Ollama isn't overloaded",
    "http": "if it repeats, see Ollama's log (ollama/ollama.log in ecf's data folder)",
    "server": "close other large apps (the model needs about 9 GB free), then wait: ecf retries"
    " each minute",
}
# Ollama's wording for "can't run the model now" (unverified, confirm in V1.6): an HTTP 5xx
# whose error names memory or the runner
_SERVER_FAULT = re.compile(r"memory|runner", re.IGNORECASE)


class OllamaError(Exception):
    """A model-server fault; `str()` never contains message or model text."""

    def __init__(self, cause: Cause, detail: str = "") -> None:
        self.cause: Cause = cause
        self.detail = detail
        super().__init__(f"{cause}: {detail}" if detail else cause)

    @property
    def fix(self) -> str:
        return FIX[self.cause]


# ---------------------------------------------------------------------------- pin


@dataclass(frozen=True)
class Pin:
    tag: str
    digest: str
    ecf_name: str

    @property
    def ecf_tag(self) -> str:
        return f"{self.ecf_name}:{_release()}"


def _release() -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "-", __version__)


def load_pin() -> Pin:
    raw = json.loads(resources.files("ecf_server.data").joinpath("ollama.lock").read_text("utf-8"))
    return Pin(tag=raw["tag"], digest=raw["digest"], ecf_name=raw["ecf_name"])


# ---------------------------------------------------------------------------- the host side


@dataclass(frozen=True)
class Listener:
    addresses: tuple[str, ...]  # e.g. ("127.0.0.1:11434",)
    pid: int | None


LSOF = "/usr/sbin/lsof"
SS = ("/usr/sbin/ss", "/usr/bin/ss", "/sbin/ss", "/bin/ss")

# run a command and return its stdout; raises OSError or CalledProcessError
Runner = Callable[[list[str]], str]


def _run(cmd: list[str]) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=10).stdout  # noqa: S603


def find_listener(run: Runner = _run, platform: str = sys.platform) -> Listener | None:
    """Who listens on the port; None when nobody does. Raises OllamaError("unconfirmed") when the
    check itself can't run (OD-242)."""
    if platform == "darwin":
        tool = LSOF
        cmd = [tool, "-nP", f"-iTCP:{PORT}", "-sTCP:LISTEN", "-Fpn"]
    else:  # fixed paths, never PATH: this check looks for a misconfigured server
        tool = next((p for p in SS if os.path.exists(p)), SS[0])
        cmd = [tool, "-ltnpH", f"sport = :{PORT}"]
    try:
        out = run(cmd)
    except subprocess.CalledProcessError as e:
        if platform == "darwin" and e.returncode == 1 and not (e.stdout or "").strip():
            return None  # lsof exits 1 when nothing matches
        raise OllamaError("unconfirmed", f"{os.path.basename(tool)} failed") from e
    except (OSError, subprocess.SubprocessError) as e:
        raise OllamaError("unconfirmed", f"{os.path.basename(tool)} unavailable") from e
    return _parse_lsof(out) if platform == "darwin" else _parse_ss(out)


def _parse_lsof(out: str) -> Listener | None:
    pid: int | None = None
    addrs: list[str] = []
    for line in out.splitlines():
        if line.startswith("p") and line[1:].isdigit():
            pid = pid or int(line[1:])
        elif line.startswith("n"):
            addrs.append(line[1:])
    return Listener(tuple(addrs), pid) if addrs else None


def _parse_ss(out: str) -> Listener | None:
    addrs: list[str] = []
    pid: int | None = None
    for line in out.splitlines():
        cols = line.split()
        if len(cols) >= 4:
            addrs.append(cols[3])
        m = re.search(r"pid=(\d+)", line)
        if m and pid is None:
            pid = int(m.group(1))
    return Listener(tuple(addrs), pid) if addrs else None


def check_listener(listener: Listener) -> None:
    others = [a for a in listener.addresses if a != f"{HOST}:{PORT}"]
    if others:
        raise OllamaError("not_loopback", ", ".join(sorted(set(others)))[:120])


def server_env(pid: int, run: Runner = _run, platform: str = sys.platform) -> dict[str, str]:
    """The server's `OLLAMA_*` environment. Raises OllamaError("unconfirmed") when it can't be read,
    e.g. the server runs as another user (OD-245)."""
    try:
        if platform == "darwin":
            out = run(["/bin/ps", "-E", "-ww", "-o", "command=", "-p", str(pid)])
            tokens = out.split()
        else:
            with open(f"/proc/{pid}/environ", "rb") as f:
                tokens = [t.decode(errors="replace") for t in f.read().split(b"\0")]
    except (OSError, subprocess.SubprocessError) as e:
        raise OllamaError("unconfirmed", "the server's environment can't be read") from e
    if not any(t.startswith(("PATH=", "HOME=")) for t in tokens):
        raise OllamaError("unconfirmed", "the server's environment isn't visible")
    env: dict[str, str] = {}
    for t in tokens:
        k, sep, v = t.partition("=")
        if sep and k.startswith("OLLAMA_"):
            env[k] = v
    return env


def _truthy(v: str | None) -> bool:
    return v is not None and v.strip().lower() not in ("", "0", "false", "no", "off")


def check_env(env: dict[str, str]) -> None:
    if _truthy(env.get("OLLAMA_DEBUG_LOG_REQUESTS")):
        raise OllamaError("logs_requests", "OLLAMA_DEBUG_LOG_REQUESTS is set")


# ---------------------------------------------------------------------------- the HTTP client


@dataclass(frozen=True)
class Metrics:
    prompt_tokens: int | None
    cached_tokens: int | None
    output_tokens: int | None
    prompt_ns: int | None
    eval_ns: int | None
    load_ns: int | None
    total_ns: int | None

    @classmethod
    def of(cls, r: dict[str, Any]) -> Metrics:
        def n(k: str) -> int | None:
            v = r.get(k)
            return v if isinstance(v, int) else None

        return cls(n("prompt_eval_count"), n("prompt_eval_cached_count"), n("eval_count"),
                   n("prompt_eval_duration"), n("eval_duration"), n("load_duration"),
                   n("total_duration"))  # fmt: skip

    @property
    def generation_tps(self) -> float | None:
        if not self.output_tokens or not self.eval_ns:
            return None
        return self.output_tokens / (self.eval_ns / 1e9)


@dataclass(frozen=True)
class Reply:
    content: str  # model output: returned to the caller only, never logged or stored raw
    metrics: Metrics
    done_reason: str | None


class Client:
    """Ollama's native API on loopback. `transport` lets tests use `httpx.MockTransport`."""

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self._http = httpx.Client(base_url=BASE_URL, timeout=TIMEOUT_S, transport=transport,
                                  trust_env=False)  # fmt: skip

    def close(self) -> None:
        self._http.close()

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        last: Exception | None = None
        for _ in range(2):  # one retry (§15.4)
            try:
                r = self._http.request(method, path, json=body)
            except httpx.TimeoutException as e:
                last = e
                continue
            except httpx.TransportError as e:
                raise OllamaError("not_running", type(e).__name__) from e
            if r.status_code == 404:
                raise OllamaError("model_missing", _error_field(r))
            if r.is_error:
                err = _error_field(r)
                cause: Cause = ("server" if r.status_code >= 500 and _SERVER_FAULT.search(err)
                                else "http")  # fmt: skip
                raise OllamaError(cause, f"HTTP {r.status_code} {err}".strip())
            if not r.content.strip():  # copy and delete reply 200 with no body
                return {}
            return cast("dict[str, Any]", r.json())
        raise OllamaError("timeout", f"no reply in {TIMEOUT_S:.0f} s") from last

    def version(self) -> str:
        return str(self._call("GET", "/api/version").get("version", ""))

    def digests(self) -> dict[str, str]:
        """Installed model name -> manifest digest."""
        models = cast("list[dict[str, Any]]", self._call("GET", "/api/tags").get("models") or [])
        return {str(m["name"]): str(m["digest"]) for m in models if "name" in m and "digest" in m}

    def copy(self, source: str, destination: str) -> None:
        self._call("POST", "/api/copy", {"source": source, "destination": destination})

    def delete(self, name: str) -> None:
        self._call("DELETE", "/api/delete", {"model": name})

    def pull(self, tag: str) -> Iterator[dict[str, Any]]:
        """Stream pull progress (status, total, completed); raises OllamaError on failure."""
        try:
            with self._http.stream("POST", "/api/pull", json={"model": tag, "stream": True},
                                   timeout=PULL_TIMEOUT_S) as r:  # fmt: skip
                if r.is_error:
                    r.read()
                    raise OllamaError("http", f"HTTP {r.status_code} {_error_field(r)}".strip())
                for line in r.iter_lines():
                    if not line:
                        continue
                    event = cast("dict[str, Any]", json.loads(line))
                    if "error" in event:
                        raise OllamaError("http", str(event["error"])[:_ERROR_CHARS])
                    yield {k: event[k] for k in ("status", "total", "completed") if k in event}
        except httpx.TransportError as e:
            raise OllamaError("not_running", type(e).__name__) from e

    def chat(
        self,
        model: str,
        system: str,
        user: str,
        *,
        role: str,
        fmt: dict[str, Any] | None = None,
        keep_alive: str = KEEP_ALIVE,
    ) -> Reply:
        body: dict[str, Any] = {
            "model": model,
            "stream": False,
            "think": False,
            "keep_alive": keep_alive,
            "options": OPTIONS | {"num_predict": NUM_PREDICT[role]},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if fmt is not None:
            body["format"] = fmt
        r = self._call("POST", "/api/chat", body)
        message = cast("dict[str, Any]", r.get("message") or {})
        return Reply(str(message.get("content", "")), Metrics.of(r), r.get("done_reason"))

    def unload(self, model: str) -> None:
        """Ask Ollama to unload the model now (`keep_alive: 0`)."""
        self._call("POST", "/api/generate", {"model": model, "keep_alive": 0})


def _error_field(r: httpx.Response) -> str:
    try:
        err = r.json().get("error", "")
    except (ValueError, AttributeError):
        return ""
    return re.sub(r"[\x00-\x1f\x7f]", " ", str(err))[:_ERROR_CHARS]


# ---------------------------------------------------------------------------- readiness


@dataclass(frozen=True)
class Ready:
    version: str
    digest: str
    listener: Listener
    env: dict[str, str]


def readiness(
    client: Client,
    pin: Pin | None = None,
    *,
    run: Runner = _run,
    platform: str = sys.platform,
) -> Ready:
    """Everything that must hold before model work (I6, OD-240, OD-242, OD-245); raises
    OllamaError naming the first thing that doesn't."""
    pin = pin or load_pin()
    listener = find_listener(run, platform)
    if listener is None:
        raise OllamaError("not_running", f"nothing listens on port {PORT}")
    check_listener(listener)
    if listener.pid is None:
        raise OllamaError("unconfirmed", "the listening process can't be identified")
    env = server_env(listener.pid, run, platform)
    check_env(env)
    version = client.version()
    digest = client.digests().get(pin.ecf_tag)
    if digest is None:
        raise OllamaError("model_missing", pin.ecf_tag)
    if digest != pin.digest:
        raise OllamaError("digest_mismatch", f"{pin.ecf_tag} is {digest[:12]}, pinned"
                                             f" {pin.digest[:12]}")  # fmt: skip
    return Ready(version, digest, listener, env)


# ---------------------------------------------------------------------------- metrics


Role = Literal["classifier", "actor", "fallback_shadow", "eval", "probe"]
Outcome = Literal["ok", "schema_failure", "truncated", "timeout", "error"]


def record_call(  # noqa: PLR0913 - keyword-only tags after the call's result
    conn: sqlite3.Connection,
    clock: Clock,
    *,
    role: Role,
    outcome: Outcome,
    digest: str,
    metrics: Metrics | None,
    address_id: str | None = None,
    preset: str | None = None,
    stage: str | None = None,
) -> None:
    m = metrics or Metrics(None, None, None, None, None, None, None)
    with write_tx(conn):
        conn.execute(
            "INSERT INTO model_calls (ts, address_id, preset, stage, role, outcome, digest,"
            " prompt_tokens, cached_tokens, output_tokens, prompt_ns, eval_ns, load_ns, total_ns)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                to_ts(clock.now()),
                address_id,
                preset,
                stage,
                role,
                outcome,
                digest,
                m.prompt_tokens,
                m.cached_tokens,
                m.output_tokens,
                m.prompt_ns,
                m.eval_ns,
                m.load_ns,
                m.total_ns,
            ),
        )
