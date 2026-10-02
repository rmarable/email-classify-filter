"""The local model as the service sees it: status, install, and the alert when it can't be used
(SPEC §4.3, §7.5, §13.2; V1.3 step 1b).

- **`ecf models install`** pulls the pinned tag, checks that its manifest digest is the pinned one
  (if the tag moved upstream, no ecf release moves the pin yet, so it stops and says so), copies it
  to ecf's own name and checks the copy. It runs in a background thread; `ecf models status` shows
  its progress.
- **The model check** runs `ollama.readiness` and keeps one alert open while it fails. Ollama not
  running, the model missing or changed: System Error naming the cause and the fix. The listener
  not loopback-only, a check that can't run, or request logging on: the same, loud (the Slack post
  mentions you; OD-240, OD-242, OD-245). Resolved when it passes again. Model work never runs
  while it fails (I6).
- The check runs each tick once models have been installed on this install (before that there is
  nothing to watch), and before each model round from V1.3 step 2.
- **After an ecf upgrade** ecf's copy has a new name (`ecf/gemma4-12b:<release>`). When it's
  missing but Ollama still holds the pinned tag with the pinned digest, the check copies it again
  by itself (audited) instead of stopping model work until `ecf models install`; an install
  removes ecf's copies for other releases.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ecf.errors import ConflictError
from ecf_server import health, ollama, slack_admin
from ecf_server.clock import Clock, to_ts
from ecf_server.db import write_tx
from ecf_server.notify import Notifier
from ecf_server.ollama import Client, OllamaError

INSTALLED_KEY = "models.installed_at"
ALERT = "local_model"  # System Error; not running, missing or changed
LOUD = "local_model_unsafe"  # System Error with a mention; can't be confirmed safe
LOUD_CAUSES = frozenset({"not_loopback", "unconfirmed", "logs_requests", "digest_mismatch"})
SHOWN_ENV = ("OLLAMA_NUM_PARALLEL", "OLLAMA_ORIGINS", "OLLAMA_NO_CLOUD", "OLLAMA_FLASH_ATTENTION",
             "OLLAMA_KV_CACHE_TYPE", "OLLAMA_DEBUG", "OLLAMA_DEBUG_LOG_REQUESTS")  # fmt: skip


# ---------------------------------------------------------------------------- the check


def fault_text(e: OllamaError) -> str:
    return f"{fault_summary(e)} Model work is stopped until it's fixed: {e.fix}."


def fault_summary(e: OllamaError) -> str:
    what = {
        "not_running": "Ollama isn't running",
        "model_missing": "the local model isn't installed",
        "digest_mismatch": "the local model isn't the pinned one",
        "not_loopback": "Ollama listens beyond this computer",
        "unconfirmed": "ecf can't confirm Ollama is safe to use",
        "logs_requests": "Ollama is set to write every request, email text included, to disk",
        "timeout": "Ollama doesn't answer in time",
        "http": "Ollama returned an error",
        "server": "Ollama can't run the local model now",
    }[e.cause]
    detail = f" ({e.detail})" if e.detail else ""
    return f"{what}{detail}."


def check(
    conn: sqlite3.Connection, clock: Clock, notifier: Notifier, client: Client, **kw: Any
) -> ollama.Ready | None:
    """Readiness, keeping the alert in step: None (and an open alert) when model work must not
    run."""
    try:
        try:
            ready = ollama.readiness(client, **kw)
        except OllamaError as e:
            if e.cause != "model_missing" or not installed(conn) or not recopy(conn, clock, client):
                raise
            ready = ollama.readiness(client, **kw)
    except OllamaError as e:
        kind, other = (LOUD, ALERT) if e.cause in LOUD_CAUSES else (ALERT, LOUD)
        health.resolve_alert(conn, clock, notifier, other, None)
        health.open_alert(conn, clock, notifier, kind, None, fault_text(e))
        return None
    for kind in (ALERT, LOUD):
        health.resolve_alert(conn, clock, notifier, kind, None)
    return ready


def recopy(conn: sqlite3.Connection, clock: Clock, client: Client) -> bool:
    """Copy the pinned tag to this release's ecf name when Ollama still holds it unchanged (after
    an ecf upgrade); True if it did. Never pulls."""
    pin = ollama.load_pin()
    try:
        if client.digests().get(pin.tag) != pin.digest:
            return False
        client.copy(pin.tag, pin.ecf_tag)
        if client.digests().get(pin.ecf_tag) != pin.digest:
            return False
    except OllamaError:
        return False
    now = to_ts(clock.now())
    with write_tx(conn):
        conn.execute(
            "INSERT INTO audit (ts, address_id, event, actor, outcome, data)"
            " VALUES (?, NULL, 'models.installed', 'service', 'ok',"
            " json_object('tag', ?, 'digest', ?, 'copied_after_upgrade', 1))",
            (now, pin.ecf_tag, pin.digest),
        )
    return True


def prune(client: Client) -> list[str]:
    """Remove ecf's copies for other releases (best effort); the names removed."""
    pin = ollama.load_pin()
    gone: list[str] = []
    for name in client.digests():
        if name.startswith(f"{pin.ecf_name}:") and name != pin.ecf_tag:
            try:
                client.delete(name)
                gone.append(name)
            except OllamaError:
                continue
    return gone


def needed(conn: sqlite3.Connection) -> bool:
    """Some address uses a preset that runs Ollama (A or B, §4)."""
    return conn.execute("SELECT 1 FROM addresses WHERE removed_at IS NULL AND preset IN"
                        " ('A', 'B') LIMIT 1").fetchone() is not None  # fmt: skip


def quiet(conn: sqlite3.Connection, clock: Clock, notifier: Notifier) -> None:
    """No address uses the local model any more: its alerts no longer apply."""
    for kind in (ALERT, LOUD):
        health.resolve_alert(conn, clock, notifier, kind, None)


def installed(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT 1 FROM settings WHERE key = ?", (INSTALLED_KEY,)).fetchone()
    return row is not None


def waiting_since(conn: sqlite3.Connection) -> str | None:
    """When the open model alert opened (the daily summary's "waiting for the local model
    since")."""
    row = conn.execute(
        "SELECT min(opened_at) FROM alerts WHERE kind IN (?, ?) AND resolved_at IS NULL",
        (ALERT, LOUD),
    ).fetchone()
    return row[0] if row else None


# ---------------------------------------------------------------------------- status


def status(conn: sqlite3.Connection, client: Client, **kw: Any) -> dict[str, Any]:
    pin = ollama.load_pin()
    out: dict[str, Any] = {
        "pin": {"tag": pin.tag, "digest": pin.digest, "ecf_tag": pin.ecf_tag},
        "installed_at": _setting(conn, INSTALLED_KEY),
        "install": INSTALLS.snapshot(),
    }
    try:
        ready = ollama.readiness(client, pin, **kw)
    except OllamaError as e:
        return out | {"ready": False, "fault": {"cause": e.cause, "detail": e.detail,
                                                "fix": e.fix, "text": fault_text(e),
                                                "summary": fault_summary(e)}}  # fmt: skip
    env = {k: ready.env[k] for k in SHOWN_ENV if k in ready.env}
    return out | {"ready": True, "version": ready.version, "digest": ready.digest,
                  "listener": list(ready.listener.addresses), "env": env}  # fmt: skip


def _setting(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return str(json.loads(row[0])) if row else None


# ---------------------------------------------------------------------------- install


@dataclass
class Progress:
    state: str = "idle"  # idle | pulling | copying | done | failed
    status: str = ""
    completed: int = 0
    total: int = 0
    error: str = ""
    started_at: str | None = None
    ended_at: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {"state": self.state, "status": self.status, "completed": self.completed,
                    "total": self.total, "error": self.error, "started_at": self.started_at,
                    "ended_at": self.ended_at}  # fmt: skip

    def set(self, **kw: Any) -> None:
        with self.lock:
            for k, v in kw.items():
                setattr(self, k, v)


INSTALLS = Progress()


Spawn = Callable[[Callable[[], None]], None]


def _thread(work: Callable[[], None]) -> None:
    threading.Thread(target=work, name="ecf-models-install", daemon=True).start()


def start_install(
    connect: Callable[[], sqlite3.Connection],
    clock: Clock,
    client_factory: Callable[[], Client],
    *,
    spawn: Spawn = _thread,
) -> dict[str, Any]:
    """Start `install` in a thread (`spawn` runs it inline in tests); refuse a second one."""
    with INSTALLS.lock:
        if INSTALLS.state in ("pulling", "copying"):
            raise ConflictError("a model install is already running; see `ecf models status`")
        INSTALLS.state, INSTALLS.error, INSTALLS.status = "pulling", "", "starting"
        INSTALLS.completed = INSTALLS.total = 0
        INSTALLS.started_at, INSTALLS.ended_at = to_ts(clock.now()), None

    def work() -> None:
        client = client_factory()
        try:
            conn = connect()
            try:
                install(conn, clock, client, INSTALLS)
            finally:
                conn.close()
        except OllamaError as e:
            INSTALLS.set(state="failed", error=fault_text(e), ended_at=to_ts(clock.now()))
        except Exception as e:  # reported to the CLI, never raised into the thread
            INSTALLS.set(state="failed", error=type(e).__name__, ended_at=to_ts(clock.now()))
        finally:
            client.close()

    spawn(work)
    return INSTALLS.snapshot()


def install(conn: sqlite3.Connection, clock: Clock, client: Client, progress: Progress) -> None:
    pin = ollama.load_pin()
    for event in client.pull(pin.tag):
        progress.set(status=str(event.get("status", ""))[:60],
                     completed=int(event.get("completed") or 0),
                     total=int(event.get("total") or 0))  # fmt: skip
    got = client.digests().get(pin.tag)
    if got != pin.digest:
        was = (got or "missing")[:12]
        raise OllamaError("digest_mismatch", f"{pin.tag} upstream is {was}, pinned"
                          f" {pin.digest[:12]}; no ecf release moves the pin yet")  # fmt: skip
    progress.set(state="copying", status=f"copying to {pin.ecf_tag}")
    client.copy(pin.tag, pin.ecf_tag)
    if client.digests().get(pin.ecf_tag) != pin.digest:
        raise OllamaError("digest_mismatch", f"{pin.ecf_tag} after the copy")
    prune(client)
    now = to_ts(clock.now())
    with write_tx(conn):
        slack_admin.put_setting(conn, INSTALLED_KEY, now, now, actor="os_user")
        conn.execute(
            "INSERT INTO audit (ts, address_id, event, actor, outcome, data)"
            " VALUES (?, NULL, 'models.installed', 'os_user', 'ok',"
            " json_object('tag', ?, 'digest', ?))",
            (now, pin.ecf_tag, pin.digest),
        )
    from ecf_server import modelq  # noqa: PLC0415 - modelq imports this module

    modelq.retry_all(conn, clock, actor="os_user")  # what it gave up on gets another try
    progress.set(state="done", status="installed", ended_at=now)
