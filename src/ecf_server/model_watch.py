"""The weekly model watch (SPEC §7.6; V1.4 step 10; OD-053, OD-299 to OD-303).

- **Retirement** (no network): `models.lock` records each pinned Claude ID's state and retirement
  date as the release found them on Anthropic's deprecations page; the weekly CI canary
  (`scripts/model_canary.py`) fails when the page changes, so a release follows. A pin with a firm
  date raises `[ecf-alert] Model Retirement Scheduled` when first seen, again 30 days before and
  again 7 days before (a one-off alert each time, desktop and Slack), while an address uses
  Claude. From the date on, `ecf claude` refuses to open (`refuse_retired`). An override that
  replaces the pin's family ends it. The alert doesn't look at ecf's releases (the release check
  below doesn't read their pins), so it says no release moving the pin is known.
- **Models API** (only with the optional key, `ecf models api-key set --provider anthropic`;
  secret `models-api-key`):
  `GET /v1/models` once a week. It has no deprecation fields (verified 2026-10-02, platform.claude
  .com List Models reference), so it adds two things: a pinned ID it doesn't list (retired early,
  or the key's workspace can't use it) keeps a System Error open; a newer model in a family ecf
  pins (`created_at` after the pin's) goes on the next daily summary, once. A failure raises
  System Error (`models_api`), cleared by the next success; offline (the host doesn't resolve)
  waits an hour and raises nothing.
- **Ollama library:** the tags-page watch went with the local model in v3.0.0 (ADR 0031).
- **TypeSafe's models** (only with the TypeSafe key while preset D is available, so not in
  v3.0.0 (OD-516); step 4, hand-off 6): `GET /v1/models`
  once a week, recording each listed alias's `release_date` (top key `models`; items `name`,
  `description`, `release_date`; SPEC §21.2's live check). The listing shows aliases only, so it
  can't see the pinned version retire (`jev.lock`); a failure is shown in `ecf models status`
  only, offline waits an hour.
- **ecf releases** (operator decision D7, 2026-10-06): the release list of ecf's GitHub
  repository through `gh` (ecf/release_source.py; metadata only, nothing downloaded), once a
  week. The newest stable release newer than this ecf goes on the next daily summary, once,
  naming `ecf upgrade`. A failure (no `gh`, not signed in, offline) is shown in `ecf models
  status` only. The service runs it only when given a lister (`service.py`).

Nothing here ever changes a pin: a newer model is never adopted automatically. The watch sends no
mail content anywhere: an API key to api.anthropic.com or api.typesafe.ai, and to GitHub only
`gh`'s own sign-in.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any, cast

import httpx

from ecf import __version__, release_source
from ecf.errors import ConflictError, EcfError, InvalidInputError
from ecf_server import addresses, alerts, claude_pins, health, jev_keys
from ecf_server.clock import Clock, from_ts, to_ts
from ecf_server.db import write_tx
from ecf_server.log_bridge import log
from ecf_server.notify import Notifier
from ecf_server.secretstore import SecretStore

API_HOST = "api.anthropic.com"
MODELS_URL = f"https://{API_HOST}/v1/models"
API_VERSION = "2023-06-01"
KEY_SECRET = "models-api-key"  # noqa: S105 - the secret store's entry name, not a secret
TIMEOUT_S = 10.0  # §15.4
EVERY = timedelta(days=7)
OFFLINE_RETRY = timedelta(hours=1)
MAX_PAGES = 5  # 1,000 models a page
REMIND_DAYS = (30, 7)
NEXT, CLAUDE, RETIRE = ("model_watch.next_at", "model_watch.claude",
                        "model_watch.retirement")  # fmt: skip
RELEASE = "model_watch.release"
JEV = "model_watch.jev"  # TypeSafe's aliases and their release dates (step 4)
JEV_MODELS_URL = f"https://{jev_keys.HOST}/v1/models"
GITHUB_HOST = "api.github.com"
API_ALERT, MISSING_ALERT, RETIRE_KIND = "models_api", "models_missing", "model_retirement"
HttpFactory = Callable[[], httpx.Client]
Spawn = Callable[[Callable[[], None]], None]
Releases = Callable[[], list[release_source.ReleaseInfo]]


def gh_releases() -> list[release_source.ReleaseInfo]:
    """The service's lister: ecf's GitHub releases through `gh` (§7.6)."""
    return release_source.list_releases(release_source.Gh())


def http_client() -> httpx.Client:
    """No proxy or certificate settings from the environment; no redirects (a moved page reads as
    a failure, never as some other site's content)."""
    return httpx.Client(timeout=TIMEOUT_S, trust_env=False, follow_redirects=False)


def _get(conn: sqlite3.Connection, key: str) -> Any:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return json.loads(row[0]) if row else None


def _put(conn: sqlite3.Connection, key: str, value: Any, now: str) -> None:
    conn.execute(
        "INSERT INTO settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, 'service')"
        " ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at,"
        " updated_by = excluded.updated_by",
        (key, json.dumps(value, sort_keys=True), now),
    )


def _audit(conn: sqlite3.Connection, now: str, event: str, data: dict[str, Any],
           actor: str = "service") -> None:  # fmt: skip
    conn.execute(
        "INSERT INTO audit (ts, address_id, event, actor, outcome, data)"
        " VALUES (?, NULL, ?, ?, 'ok', ?)",
        (now, event, actor, json.dumps(data)),
    )


# Retirement: pinned models with a retirement date, the notices before it, and refusing to
# open `ecf claude` with a pin past it.


def _pinned(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """Each pinned ID in force (overrides applied) -> the roles it serves."""
    out: dict[str, list[str]] = {}
    for role, mid in claude_pins.effective(conn).items():
        out.setdefault(mid, []).append(role)
    return out


def retiring(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Pins in force with a firm retirement date, soonest first."""
    life = claude_pins.lifecycle()
    out: list[dict[str, Any]] = []
    for mid, roles in _pinned(conn).items():
        e = life.get(mid)  # an override's ID has no lifecycle here
        if e is not None and e.retires is not None:
            out.append({"id": mid, "roles": roles, "retires": e.retires.isoformat(),
                        "replacement": e.replacement})  # fmt: skip
    return sorted(out, key=lambda r: (r["retires"], r["id"]))


def _today(clock: Clock) -> date:
    return clock.now().date()  # UTC: Anthropic's dates carry no time zone


def retirement_text(r: dict[str, Any], today: date) -> str:
    days = (date.fromisoformat(r["retires"]) - today).days
    when = "today" if days == 0 else f"in {days} days" if days > 0 else f"{-days} days ago"
    instead = f" (Anthropic recommends {r['replacement']})" if r.get("replacement") else ""
    gate = ("" if set(r["roles"]) <= {"main_session"} else  # not a gate pin (OD-474)
            "; addresses then go back to assist until their gate passes again")  # fmt: skip
    return (f"{r['id']} ({', '.join(r['roles'])}) retires on {r['retires']} ({when}). No ecf"
            " release that moves this pin is known yet; upgrade ecf when one is out. Until then"
            f" `ecf settings set claude_model_override <id>`{instead} keeps reviews"
            f" going{gate}.")  # fmt: skip


def retirement_tick(conn: sqlite3.Connection, clock: Clock, notifier: Notifier) -> int:
    """Announce each retiring pin when first seen, then 30 and 7 days before (§7.6); only while
    an address uses Claude. Returns how many alerts went."""
    if not claude_pins.in_use(conn):
        return 0
    today, now = _today(clock), to_ts(clock.now())
    sent: dict[str, list[str]] = _get(conn, RETIRE) or {}
    went = 0
    for r in retiring(conn):
        key = f"{r['id']}:{r['retires']}"
        days = (date.fromisoformat(r["retires"]) - today).days
        due = {"announced"} | {f"d{d}" for d in REMIND_DAYS if days <= d}
        new = due - set(sent.get(key, []))
        if not new or days < 0:  # past the date `ecf claude` refuses instead
            continue
        alerts.event(conn, clock, notifier, RETIRE_KIND, retirement_text(r, today))
        sent[key] = sorted(due)
        went += 1
        with write_tx(conn):
            _put(conn, RETIRE, sent, now)
            _audit(
                conn,
                now,
                "models.retirement_alert",
                {"model": r["id"], "retires": r["retires"], "days": days},
            )
    return went


def refuse_retired(conn: sqlite3.Connection, clock: Clock) -> None:
    """`ecf claude` doesn't open with a pin past its retirement date (§7.5, OD-273)."""
    today = _today(clock)
    gone = [r for r in retiring(conn) if date.fromisoformat(r["retires"]) <= today]
    if gone:
        r = gone[0]
        instead = r.get("replacement") or "<id>"
        raise ConflictError(
            f"the pinned model {r['id']} ({', '.join(r['roles'])}) retired on {r['retires']};"
            f" upgrade ecf, or run `ecf settings set claude_model_override {instead}`"
        )


# The weekly run: when it is due, listing the models the API key can use, and checking for a
# newer ecf release.


def due(conn: sqlite3.Connection, clock: Clock) -> bool:
    nxt = _get(conn, NEXT)
    return nxt is None or from_ts(str(nxt)) <= clock.now()


_RUNNING = threading.Lock()


def _thread(fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, name="ecf-model-watch", daemon=True).start()


def start(connect: Callable[[], sqlite3.Connection], clock: Clock, notifier: Notifier,
          store: SecretStore | None, http: HttpFactory = http_client, *,
          spawn: Spawn = _thread, releases: Releases | None = None) -> bool:  # fmt: skip
    """Run the watch in its own thread (the timer never waits on the network); False when one is
    already running."""
    if not _RUNNING.acquire(blocking=False):
        return False

    def work() -> None:
        try:
            conn = connect()
            try:
                run(conn, clock, notifier, store, http, releases=releases)
            finally:
                conn.close()
        except Exception as exc:  # retried at the next due time; never raised into the thread
            log.error("models.watch_failed", error_type=type(exc).__name__)
        finally:
            _RUNNING.release()

    spawn(work)
    return True


def run(conn: sqlite3.Connection, clock: Clock, notifier: Notifier, store: SecretStore | None,
        http: HttpFactory = http_client, *,
        releases: Releases | None = None) -> dict[str, Any]:  # fmt: skip
    """One watch: the Models API (with a key) and, given a lister, ecf's releases. The next run
    is a week on, or an hour on when a host didn't resolve."""
    offline = False
    key = _key(store)
    # TypeSafe is asked only while preset D is available (held back, OD-516)
    jev_key = _key(store, jev_keys.KEY_SECRET) if addresses.D_AVAILABLE else None
    with http() as client:
        if key:
            offline |= _claude(conn, clock, notifier, client, key) == "offline"
        if jev_key:
            offline |= _jev(conn, clock, client, jev_key) == "offline"
    if releases is not None:
        offline |= _release(conn, clock, releases) == "offline"
    now = clock.now()
    with write_tx(conn):
        _put(conn, NEXT, to_ts(now + (OFFLINE_RETRY if offline else EVERY)), to_ts(now))
    return status(conn)


def _key(store: SecretStore | None, name: str = KEY_SECRET) -> str | None:
    if store is None:
        return None
    try:
        return store.get(name)
    except Exception:  # a locked store: run without the key this time
        return None


def _claude(conn: sqlite3.Connection, clock: Clock, notifier: Notifier, client: httpx.Client,
            key: str) -> str:  # fmt: skip
    now = to_ts(clock.now())
    if not health.resolves(API_HOST):
        return "offline"
    try:
        listed = list_models(client, key)
    except ModelsApiError as e:
        with write_tx(conn):
            _put(conn, CLAUDE, (_get(conn, CLAUDE) or {}) | {"error": str(e), "failed_at": now},
                 now)  # fmt: skip
            _audit(conn, now, "models.watch", {"part": "claude", "error": e.code})
        health.open_alert(
            conn,
            clock,
            notifier,
            API_ALERT,
            None,
            f"The weekly model watch couldn't use the Models API: {e}.{e.fix}",
        )
        return "failed"
    health.resolve_alert(conn, clock, notifier, API_ALERT, None)
    pinned = _pinned(conn)
    missing = sorted(set(pinned) - set(listed))
    prev: dict[str, Any] = _get(conn, CLAUDE) or {}
    newer: dict[str, Any] = dict(prev.get("newer", {}))
    for mid in pinned:
        fam, made = claude_pins.family(mid), listed.get(mid)
        if made is None:
            continue
        for other, when in listed.items():
            if other not in pinned and claude_pins.family(other) == fam and when > made:
                newer.setdefault(other, {"family": fam, "pinned": mid, "created_at": when,
                                         "reported": False})  # fmt: skip
    with write_tx(conn):
        _put(conn, CLAUDE, {"checked_at": now, "error": None, "listed": len(listed),
                            "missing": missing, "newer": newer}, now)  # fmt: skip
        _audit(
            conn,
            now,
            "models.watch",
            {"part": "claude", "listed": len(listed), "missing": missing, "newer": sorted(newer)},
        )
    if missing:
        health.open_alert(conn, clock, notifier, MISSING_ALERT, None,
                          f"The Models API doesn't list the pinned model(s) {', '.join(missing)}:"
                          " retired, or your API key's workspace can't use them. Claude reviews"
                          " may fail; see ecf models status.")  # fmt: skip
    else:
        health.resolve_alert(conn, clock, notifier, MISSING_ALERT, None)
    return "ok"


def _jev(conn: sqlite3.Connection, clock: Clock, client: httpx.Client, key: str) -> str:
    """TypeSafe's aliases and their `release_date` (hand-off 6); numbers and names only."""
    now = to_ts(clock.now())
    if not health.resolves(jev_keys.HOST):
        return "offline"
    try:
        headers = {"Authorization": f"Bearer {key}", "User-Agent": "ecf"}
        r = client.get(JEV_MODELS_URL, headers=headers, timeout=TIMEOUT_S)
        if r.status_code != 200:
            raise ModelsApiError(f"http_{r.status_code}", f"HTTP {r.status_code}")
        listed: Any = r.json().get("models")
        aliases = {str(m["name"]): str(m.get("release_date") or "")
                   for m in cast("list[dict[str, Any]]", listed)}  # fmt: skip
    except httpx.TimeoutException:
        err = f"no answer in {TIMEOUT_S:.0f} s"
    except httpx.TransportError as e:
        err = f"network error ({type(e).__name__})"
    except ModelsApiError as e:
        err = str(e)
    except (ValueError, KeyError, TypeError, AttributeError):
        err = "an answer ecf can't read"
    else:
        with write_tx(conn):
            _put(conn, JEV, {"checked_at": now, "error": None, "aliases": aliases}, now)
            _audit(conn, now, "models.watch", {"part": "jev", "listed": len(aliases)})
        return "ok"
    with write_tx(conn):
        _put(conn, JEV, (_get(conn, JEV) or {}) | {"error": err, "failed_at": now}, now)
        _audit(conn, now, "models.watch", {"part": "jev", "error": err})
    return "failed"


class ModelsApiError(Exception):
    def __init__(self, code: str, text: str, fix: str = "") -> None:
        super().__init__(text)
        self.code, self.fix = code, fix


def list_models(client: httpx.Client, key: str, timeout_s: float = TIMEOUT_S) -> dict[str, str]:
    """Every model the key can use -> its `created_at` (RFC 3339, compared as text after
    normalizing to UTC seconds). `timeout_s` per request (the key route's check: 5 s)."""
    out: dict[str, str] = {}
    after: str | None = None
    for _ in range(MAX_PAGES):
        params: dict[str, str | int] = {"limit": 1000} | ({"after_id": after} if after else {})
        try:
            headers = {"x-api-key": key, "anthropic-version": API_VERSION}
            r = client.get(MODELS_URL, params=params, headers=headers, timeout=timeout_s)
        except httpx.TimeoutException as e:
            raise ModelsApiError("timeout", f"no answer in {timeout_s:.0f} s") from e
        except httpx.TransportError as e:
            raise ModelsApiError("network", f"network error ({type(e).__name__})") from e
        if r.status_code in (401, 403):
            raise ModelsApiError(f"http_{r.status_code}", f"the API key was refused (HTTP"
                                 f" {r.status_code})", " Replace it with `ecf models api-key set"
                                 " --provider anthropic` or remove it with `ecf models api-key"
                                 " clear --provider anthropic`.")  # fmt: skip
        if r.status_code != 200:
            raise ModelsApiError(f"http_{r.status_code}", f"HTTP {r.status_code}")
        try:
            body = r.json()
            for m in body["data"]:
                out[str(m["id"])] = _utc(str(m["created_at"]))
            more, after = bool(body.get("has_more")), body.get("last_id")
        except (ValueError, KeyError, TypeError) as e:
            raise ModelsApiError("format", "an answer ecf can't read") from e
        if not more or not after:
            return out
    return out


def _utc(ts: str) -> str:
    return to_ts(datetime.fromisoformat(ts.replace("Z", "+00:00")))


def _release(conn: sqlite3.Connection, clock: Clock, releases: Releases) -> str:
    """The newest stable ecf release newer than this one, for the daily summary (§7.6)."""
    now = to_ts(clock.now())
    if not health.resolves(GITHUB_HOST):
        return "offline"
    prev: dict[str, Any] = _get(conn, RELEASE) or {}
    try:
        found = release_source.newest(releases(), __version__)
    except EcfError as e:
        with write_tx(conn):
            _put(conn, RELEASE, prev | {"error": e.detail, "failed_at": now}, now)
            _audit(conn, now, "models.watch", {"part": "release", "error": e.code.value})
        return "failed"
    tag = found.tag if found else None
    reported = bool(prev.get("reported")) and prev.get("newest") == tag
    with write_tx(conn):
        _put(conn, RELEASE, {"checked_at": now, "error": None, "current": __version__,
                             "newest": tag, "reported": reported}, now)  # fmt: skip
        _audit(conn, now, "models.watch", {"part": "release", "newest": tag})
    return "ok"


# What it says: the findings for the daily summary, and the status `ecf models status` and
# `ecf doctor` show.


def daily_lines(conn: sqlite3.Connection) -> list[str]:
    """Findings not yet in a daily summary (marked by `mark_reported` once it's queued)."""
    out: list[str] = []
    cl: dict[str, Any] = _get(conn, CLAUDE) or {}
    fresh = sorted((m, v) for m, v in cl.get("newer", {}).items() if not v["reported"])
    if fresh:
        out.append("Newer Claude model(s) in a family ecf uses: " + ", ".join(
            f"{m} (ecf pins {v['pinned']})" for m, v in fresh)
            + ". ecf never switches by itself; a release moves the pins.")  # fmt: skip
    rel: dict[str, Any] = _get(conn, RELEASE) or {}
    if rel.get("newest") and not rel.get("reported") and rel.get("current") == __version__:
        out.append(f"ecf {release_source.tag_version(rel['newest'])} is out (this is"
                   f" {__version__}): `ecf upgrade` checks it, then asks.")  # fmt: skip
    return out


def mark_reported(conn: sqlite3.Connection, now: str) -> None:
    """Inside the daily summary's write transaction."""
    v: dict[str, Any] | None = _get(conn, CLAUDE)
    if v and any(not e["reported"] for e in v.get("newer", {}).values()):
        v["newer"] = {k: e | {"reported": True} for k, e in v["newer"].items()}
        _put(conn, CLAUDE, v, now)
    rel: dict[str, Any] | None = _get(conn, RELEASE)
    if rel and rel.get("newest") and not rel.get("reported"):
        _put(conn, RELEASE, rel | {"reported": True}, now)


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def _release_status(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """The last release check; its newest release is dropped once this ecf isn't the version
    that checked (upgraded since)."""
    rel: dict[str, Any] | None = _get(conn, RELEASE)
    if rel and rel.get("current") != __version__:
        return rel | {"newest": None}
    return rel


def status(conn: sqlite3.Connection) -> dict[str, Any]:
    """For `ecf models status` and `ecf doctor`."""
    pinned = _pinned(conn)
    life = {m: e for m, e in claude_pins.lifecycle().items() if m in pinned}
    return {
        "next_at": _get(conn, NEXT),
        "claude_used": claude_pins.in_use(conn),
        "api_key": bool(_get(conn, "model_watch.api_key_set")),
        "claude": _get(conn, CLAUDE),
        "release": _release_status(conn),
        "jev": _get(conn, JEV),  # TypeSafe's aliases (step 4)
        "jev_pin": jev_keys.pin(),
        "retiring": retiring(conn),
        "lifecycle": {
            m: {
                "state": e.state,
                "retires": _iso(e.retires),
                "not_sooner_than": _iso(e.not_sooner_than),
            }
            for m, e in life.items()
        },
    }


# The optional Models API key (stored after a call shows it works, or removed), and
# `ecf models watch`, which makes the watch due at the next tick.


def set_key(conn: sqlite3.Connection, clock: Clock, store: SecretStore, key: str | None,
            http: HttpFactory = http_client,
            timeout_s: float = TIMEOUT_S) -> dict[str, Any]:  # fmt: skip
    """Store the optional Models API key after one call shows it works, or remove it (`None`).
    No step-up: the key only lets ecf read Anthropic's model list (OD-302). Audited, with
    whether it replaced one and never the key; the next watch runs at the next tick. The key
    route checks it within 5 s (`timeout_s`, v3.0.0 step 2a, P12)."""
    now = to_ts(clock.now())
    if key is None:
        store.delete(KEY_SECRET)
        with write_tx(conn):
            conn.execute("DELETE FROM settings WHERE key IN (?, ?)",
                         ("model_watch.api_key_set", CLAUDE))  # fmt: skip
            _audit(conn, now, "models.api_key_cleared", {}, actor="os_user")
        return status(conn)
    if not key or any(c.isspace() for c in key) or len(key) > 400:
        raise InvalidInputError("an Anthropic API key (sk-ant-...), without spaces")
    try:
        with http() as client:
            listed = list_models(client, key, timeout_s)
    except ModelsApiError as e:
        raise InvalidInputError(f"the Models API didn't accept it: {e}") from e
    replaced = store.get(KEY_SECRET) is not None
    store.set(KEY_SECRET, key)
    with write_tx(conn):
        _put(conn, "model_watch.api_key_set", True, now)
        conn.execute("DELETE FROM settings WHERE key = ?", (NEXT,))  # watch at the next tick
        _audit(conn, now, "models.api_key_set", {"listed": len(listed), "replaced": replaced},
               actor="os_user")  # fmt: skip
    return status(conn) | {"listed": len(listed)}


def run_now(conn: sqlite3.Connection) -> None:
    """`ecf models watch`: due at the next tick."""
    with write_tx(conn):
        conn.execute("DELETE FROM settings WHERE key = ?", (NEXT,))
