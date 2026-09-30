"""`ecf doctor` (SPEC §13.2): checks that exist as of V1.0; later milestones add their own.

Each check returns ok / warn / fail with the command that fixes it. Doctor reads SQLite directly
(read-only) so it still works when the service is down.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import stat
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from ecf import __version__
from ecf.client import LocalClient
from ecf.errors import EcfError, ServiceUnavailableError
from ecf.paths import Paths
from ecf.service_unit import ServiceManager, manager_for
from ecf.status import CHECK_FAILED

MIN_PYTHON = (3, 12)
MIN_SQLITE = (3, 37, 0)
TICK_STALE_S = 180
REGRANT_FIX = "re-grant Keychain access (`ecf upgrade` does this in V1.5)"
LOW_DISK_BYTES = 1 << 30


class Level(StrEnum):
    OK = "ok"
    WARN = "warn"
    FAIL = "FAIL"


@dataclass(frozen=True)
class Check:
    name: str
    level: Level
    detail: str
    fix: str = ""


def _v(t: tuple[int, ...]) -> str:
    return ".".join(map(str, t))


def check_python() -> Check:
    ok = sys.version_info[:2] >= MIN_PYTHON
    return Check(
        "python",
        Level.OK if ok else Level.FAIL,
        sys.version.split()[0],
        "" if ok else f"install Python {_v(MIN_PYTHON)}+ and reinstall ecf",
    )


def check_sqlite() -> Check:
    ok = sqlite3.sqlite_version_info >= MIN_SQLITE
    return Check(
        "sqlite",
        Level.OK if ok else Level.FAIL,
        sqlite3.sqlite_version,
        "" if ok else f"ecf needs SQLite {_v(MIN_SQLITE)}+",
    )


def check_data_dir(paths: Paths) -> list[Check]:
    out: list[Check] = []
    if not paths.data_dir.exists():
        return [
            Check(
                "data folder",
                Level.WARN,
                f"{paths.data_dir} doesn't exist yet",
                "run `ecf service install` (or `ecf init` from V1.2)",
            )
        ]
    mode = stat.S_IMODE(paths.data_dir.stat().st_mode)
    out.append(
        Check(
            "data folder",
            Level.OK if mode == 0o700 else Level.FAIL,
            f"{paths.data_dir} ({mode:o})",
            "" if mode == 0o700 else f"chmod 700 '{paths.data_dir}'",
        )
    )
    try:
        paths.check_socket_length()
        out.append(Check("socket path", Level.OK, f"{len(os.fsencode(paths.socket))} bytes"))
    except EcfError as exc:
        out.append(Check("socket path", Level.FAIL, exc.detail, "use a shorter ECF_HOME"))
    return out


def check_watch(paths: Paths) -> list[Check]:
    """A marker left by an `ecf watch` that ended without restoring the unit (V1.3 step 7)."""
    from ecf import watch  # noqa: PLC0415

    m = watch.marker(paths)
    if m is None:
        return []
    if m["alive"]:
        return [Check("watch", Level.OK, f"`ecf watch` runs the service (pid {m['pid']})")]
    return [Check("watch", Level.FAIL, f"`ecf watch` took over at {m['started_at']} and ended"
                  " without restoring the background service", "ecf service start")]  # fmt: skip


def check_unit(manager: ServiceManager) -> Check:
    s = manager.status()
    if not s.installed:
        return Check("service unit", Level.WARN, "not installed", "ecf service install")
    if not s.running:
        return Check(
            "service unit", Level.FAIL, f"installed, not running ({s.detail})", "ecf service start"
        )
    return Check("service unit", Level.OK, f"running (pid {s.pid})")


def check_service(paths: Paths, now: datetime) -> list[Check]:
    try:
        with LocalClient(paths) as c:
            st: dict[str, Any] = c.get("/v1/status")
    except EcfError as exc:
        return [Check("service", Level.FAIL, exc.detail, "ecf service start")]
    return judge_status(st, now)


def judge_status(st: dict[str, Any], now: datetime) -> list[Check]:
    """Turn a /v1/status reply into checks (pure, so every branch is testable)."""
    out = [Check("service", Level.OK, f"answering (pid {st['pid']})")]
    same = st["version"] == __version__
    out.append(
        Check(
            "versions",
            Level.OK if same else Level.FAIL,
            f"client {__version__}, service {st['version']}",
            "" if same else "ecf service restart (after upgrading)",
        )
    )
    if st.get("tick_failures"):  # ticking, but its work fails (V1.2 review)
        out.append(Check("timer work", Level.FAIL, f"failing for {st['tick_failures']} tick(s):"
                         f" {st.get('tick_error') or 'see ecf logs'}", "see ecf logs"))  # fmt: skip
    last = st.get("last_tick_at")
    if last is None:
        out.append(Check("timer", Level.WARN, "no tick yet (ticks every minute)"))
    else:
        age = now - datetime.strptime(last, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
        stale = age.total_seconds() > TICK_STALE_S
        out.append(
            Check(
                "timer",
                Level.FAIL if stale else Level.OK,
                f"last tick {int(age.total_seconds())} s ago",
                "ecf service restart" if stale else "",
            )
        )
    br = st.get("breaker", {})
    if br.get("tripped"):
        out.append(Check("crash breaker", Level.FAIL, "tripped", "ecf service start"))
    else:
        out.append(
            Check("crash breaker", Level.OK, f"{br.get('recent_crashes', 0)} recent crashes")
        )
    ss = st.get("secret_store", {})
    if not ss.get("backend") or ss.get("detail"):  # a backend it couldn't open (V1.2 review)
        out.append(
            Check(
                "secret store",
                Level.FAIL,
                ss.get("detail", "none usable"),
                "see the admin guide: secret-store backends",
            )
        )
    elif ss.get("interpreter_changed"):
        out.append(
            Check(
                "secret store",
                Level.WARN,
                f"{ss['backend']}: Python changed since access was granted",
                REGRANT_FIX,
            )
        )
    else:
        out.append(Check("secret store", Level.OK, ss["backend"]))
    out += judge_addresses(st)
    return out


def judge_addresses(st: dict[str, Any]) -> list[Check]:
    """Each address's last check, and open alerts (SPEC §13.2; OD-190)."""
    out: list[Check] = []
    failing = CHECK_FAILED
    for a in st.get("addresses", []):
        name = f"address {a['address_id']}"
        if a["last_status"] is None:
            out.append(Check(name, Level.WARN, "not checked yet", "ecf check " + a["address_id"]))
        elif a["last_status"] in failing:
            fix = (
                f"ecf address set {a['address_id']} --app-password"
                if a["last_status"] == "login_rejected"
                else "see `ecf logs --event check.`"
            )
            out.append(Check(name, Level.FAIL, f"{a['last_status']}: {a['last_error']}", fix))
        else:
            out.append(
                Check(name, Level.OK, f"last check {a['last_finished_at']} ({a['last_status']})")
            )
    for alert in st.get("alerts", []):
        out.append(Check("alert", Level.FAIL, f"{alert['title']}: {alert['detail']}"))
    return out


def check_slack(paths: Paths) -> list[Check]:
    """Slack and step-up, checked by the service, which holds the tokens (V1.2 step 12a)."""
    try:
        with LocalClient(paths) as c:
            rows: list[dict[str, str]] = c.get("/v1/doctor/slack")["checks"]
    except ServiceUnavailableError:
        return []  # the service check already says it isn't answering
    except EcfError as exc:  # answering, but the check failed: say so, never a silent OK
        return [Check("slack", Level.FAIL, f"can't check Slack: {exc.detail}", "see ecf logs")]
    return [Check(r["name"], Level(r["level"]), r["detail"], r["fix"]) for r in rows]


def check_org_domains(paths: Paths) -> Check:
    try:
        with LocalClient(paths) as c:
            org: list[str] = c.get("/v1/addresses")["org_domains"]
    except EcfError:
        return Check("org domains", Level.WARN, "can't ask the service")
    if not org:
        return Check(
            "org domains", Level.WARN, "not set", "ecf address add (the first address sets them)"
        )
    return Check("org domains", Level.OK, ", ".join(org))


def check_models(paths: Paths) -> list[Check]:
    """The local model, checked by the service (V1.3 step 1b; SPEC §7.5, §13.2)."""
    try:
        with LocalClient(paths) as c:
            st: dict[str, Any] = c.get("/v1/models")
    except ServiceUnavailableError:
        return []
    except EcfError as exc:
        return [Check("local model", Level.FAIL, f"can't check: {exc.detail}", "see ecf logs")]
    return judge_models(st)


# what `ecf models install`'s login item sets (OD-246); anything else is reported
EXPECTED_ENV = {"OLLAMA_NUM_PARALLEL": "1"}
UNWANTED_ENV = ("OLLAMA_FLASH_ATTENTION", "OLLAMA_KV_CACHE_TYPE", "OLLAMA_DEBUG")


def judge_models(st: dict[str, Any]) -> list[Check]:
    """Turn a /v1/models reply into checks (pure)."""
    pin = st["pin"]
    if not st.get("ready"):
        fault = st["fault"]
        never = st.get("installed_at") is None and fault["cause"] in (
            "not_running",
            "model_missing",
        )
        level = Level.WARN if never else Level.FAIL
        return [Check("local model", level, f"{fault['summary']} Model work is stopped.",
                      fault["fix"])]  # fmt: skip
    out = [Check("local model", Level.OK, f"{pin['ecf_tag']} ({pin['digest'][:12]}), Ollama"
                 f" {st['version']}, listening on {', '.join(st['listener'])} only")]  # fmt: skip
    env: dict[str, str] = st.get("env", {})
    for k, want in EXPECTED_ENV.items():
        if env.get(k) != want:
            got = env.get(k, "unset")
            out.append(Check("ollama settings", Level.WARN, f"{k} is {got}, expected {want}",
                             "restart Ollama with ecf's settings"))  # fmt: skip
    if env.get("OLLAMA_NO_CLOUD", "").lower() not in ("1", "true"):
        out.append(Check("ollama settings", Level.WARN, "Ollama's cloud feature is on",
                         "set OLLAMA_NO_CLOUD=1 for Ollama"))  # fmt: skip
    for k in UNWANTED_ENV:
        if env.get(k, "").lower() not in ("", "0", "false"):
            out.append(Check("ollama settings", Level.WARN, f"{k} is set ({env[k]})",
                             f"unset {k} for Ollama"))  # fmt: skip
    if "*" in env.get("OLLAMA_ORIGINS", ""):
        out.append(Check("ollama settings", Level.WARN, "OLLAMA_ORIGINS allows any web page",
                         "unset OLLAMA_ORIGINS for Ollama"))  # fmt: skip
    return out


DNS_PROBE = "_dmarc.gmail.com"  # a long-standing public DMARC record


def check_dns(resolve: Callable[[str], bool] | None = None) -> Check:
    """Sender authentication needs DNS (SPEC §7.3); resolve one known DMARC record."""
    ok = (resolve or _resolves_txt)(DNS_PROBE)
    if ok:
        return Check("dns", Level.OK, f"resolves {DNS_PROBE}")
    return Check(
        "dns",
        Level.FAIL,
        f"can't resolve {DNS_PROBE}",
        "check the network; without DNS every message is unverified",
    )


def _resolves_txt(name: str) -> bool:
    import dns.exception  # noqa: PLC0415 - only doctor needs it on the client side
    import dns.resolver  # noqa: PLC0415

    r = dns.resolver.Resolver()
    r.timeout, r.lifetime = 1.5, 3.0
    try:
        r.resolve(name, "TXT")
    except (dns.exception.DNSException, OSError):
        return False
    return True


def check_database(paths: Paths) -> list[Check]:
    if not paths.db.exists():
        return [Check("database", Level.WARN, "not created yet", "ecf service start")]
    try:
        conn = sqlite3.connect(f"file:{paths.db}?mode=ro", uri=True, timeout=5)
        try:
            result = conn.execute("PRAGMA quick_check").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return [Check("database", Level.FAIL, f"can't open: {exc}", "see the admin guide")]
    out = [
        Check(
            "database",
            Level.OK if result == "ok" else Level.FAIL,
            f"quick_check: {result}",
            "" if result == "ok" else "restore from a backup (admin guide)",
        )
    ]
    free = shutil.disk_usage(paths.data_dir).free
    out.append(
        Check(
            "disk space",
            Level.OK if free >= LOW_DISK_BYTES else Level.WARN,
            f"{free / (1 << 30):.1f} GB free",
            "" if free >= LOW_DISK_BYTES else "free up disk",
        )
    )
    return out


Run = Callable[[list[str]], subprocess.CompletedProcess[str]]


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(args, capture_output=True, text=True, check=False, timeout=20)  # noqa: S603
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return subprocess.CompletedProcess(args, 127, "", "")


def check_disk_encryption(run: Run = _run) -> Check:
    if sys.platform == "darwin":
        out = run(["fdesetup", "status"])
        on = "FileVault is On" in out.stdout
        return Check(
            "disk encryption",
            Level.OK if on else Level.FAIL,
            out.stdout.strip() or "fdesetup unavailable",
            "" if on else "turn on FileVault (System Settings > Privacy & Security)",
        )
    out = run(["lsblk", "-rno", "TYPE"])
    if out.returncode != 0:
        return Check("disk encryption", Level.WARN, "can't detect", "make sure the disk uses LUKS")
    crypt = "crypt" in out.stdout.split()
    return Check(
        "disk encryption",
        Level.OK if crypt else Level.WARN,
        "LUKS found" if crypt else "no LUKS volume found",
        "" if crypt else "use full-disk encryption (LUKS)",
    )


def check_claude() -> Check:
    from ecf.claude_wrapper import MIN_CLAUDE, claude_version  # noqa: PLC0415

    claude = shutil.which("claude")
    need = _v(MIN_CLAUDE)
    if claude is None:
        return Check(
            "claude code",
            Level.WARN,
            "not installed (only presets B and C need it)",
            f"install Claude Code {need}+ to use `ecf claude`",
        )
    found = claude_version(claude)
    if found is None or found < MIN_CLAUDE:
        return Check(
            "claude code",
            Level.WARN,
            f"version {_v(found) if found else 'unknown'}",
            f"update Claude Code to {need}+",
        )
    return Check("claude code", Level.OK, _v(found))


def run_checks(
    paths: Paths,
    *,
    manager: ServiceManager | None = None,
    now: datetime | None = None,
    run: Run = _run,
) -> list[Check]:
    checks = [check_python(), check_sqlite(), *check_data_dir(paths)]
    checks.append(check_unit(manager or manager_for(paths)))
    checks += check_watch(paths)
    checks += check_service(paths, now or datetime.now(UTC))
    checks += check_slack(paths)
    checks.append(check_org_domains(paths))
    checks += check_models(paths)
    checks.append(check_dns())
    checks += check_database(paths)
    checks.append(check_disk_encryption(run))
    checks.append(check_claude())
    if sys.platform.startswith("linux"):
        checks.append(Check("platform", Level.WARN, "Linux support is unverified until V1.6"))
    return checks
