"""`ecf destroy`, the service's part (SPEC §11.11; OD-383 to OD-393; V1.5 step 12a).

`POST /v1/destroy` (or `ecf-server destroy` in the foreground when the service can't run, OD-387)
takes the install name typed again and a step-up bound to this install, refuses while anything is
executing or undoing, a lease is held or an `ecf claude` session is open (OD-388), then, in order
(OD-384):

1. marks `destroy.started` and pauses every address, so nothing fetches or sends;
2. sends the Security Notice straight away (desktop, Slack DM and summary channel, alert email),
   not through the queues, while Slack and the app passwords still work;
3. deletes the dead-man's scheduled message;
4. archives the summary channel and every recorded address channel (`already_archived`,
   `channel_not_found`, `is_archived` and `not_in_channel` count as done);
5. deletes the Slack app with `apps.manifest.delete` when a configuration token is given;
   otherwise, or if that fails, revokes the bot token with `auth.revoke`, which "will not
   uninstall the bot user or the app" but deactivates the bot and removes its channel
   memberships (docs.slack.dev, verified 2026-10-03; OD-385);
6. deletes every secret ecf keeps (`regrant.names`).

Each step's outcome goes into `<data root>/destroyed/<install>.json` (0600, no secrets) as it
finishes, so a second `ecf destroy` skips what's done (OD-386, OD-387). Slack being unreachable
stops the run before any secret is deleted (a retry still has the bot token); a Slack refusal is
recorded as residue and the run goes on. The record outlives the data folder: with the Security
Notice it is what remains of the destroy, since the audit lines go with the database. Afterwards
the service exits 0 (launchd and systemd don't restart a clean exit), and while the record says
the service's part is done and the CLI's isn't, the service refuses to start.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from ecf.errors import ConflictError, InvalidInputError, ServiceUnavailableError
from ecf_server import (
    alert_mail,
    alerts,
    deadman,
    install_identity,
    pause,
    regrant,
    scheduled_export,
    slack_admin,
    slack_routes,
    stepup,
)
from ecf_server._slack import SlackError, SlackNetworkError
from ecf_server.chat import Card, RouteGoneError, RouteRef
from ecf_server.clock import Clock, to_ts
from ecf_server.db import write_tx
from ecf_server.export_keys import export_dir
from ecf_server.notify import Notifier
from ecf_server.secretstore import SecretStore, SecretStoreNeedsYouError
from ecf_server.slack_chat import SlackChat, WebLike

RECORD_DIR = "destroyed"
STARTED = "destroy.started"  # settings: when the destroy began
SERVICE_DONE, DONE = "service_done", "done"  # the record's phases after "started"
# a channel already archived, deleted, or one ecf was removed from: nothing left to archive
GONE = frozenset({"already_archived", "channel_not_found", "is_archived", "not_in_channel"})
# the bot token is already unusable: nothing left to revoke
REVOKED = frozenset({"token_revoked", "invalid_auth", "account_inactive", "not_authed"})


@dataclass(frozen=True)
class Context:
    """What the service (or the foreground command) gives a destroy."""

    install: str
    root: Path  # the data root: the record lives outside the install's folder
    clock: Clock
    notifier: Notifier
    store: SecretStore
    make_web: Callable[[str], WebLike]
    sender_for: alert_mail.SenderFor


def record_path(root: Path, install: str) -> Path:
    return root / RECORD_DIR / f"{install}.json"


def read_record(root: Path, install: str) -> dict[str, Any] | None:
    try:
        return json.loads(record_path(root, install).read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {"install": install, "phase": "unreadable", "steps": {}}


def write_record(root: Path, install: str, rec: dict[str, Any]) -> None:
    path = record_path(root, install)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(rec, f, indent=2, sort_keys=True)
        f.write("\n")
    tmp.replace(path)


def blocks_start(root: Path, install: str) -> bool:
    """The service's part is done and the CLI's isn't: starting again would only fail."""
    rec = read_record(root, install)
    return rec is not None and rec.get("phase") == SERVICE_DONE


def busy(conn: sqlite3.Connection, clock: Clock, sessions: int) -> list[str]:
    """Why a destroy must wait (OD-388): the same as `ecf upgrade`, checked by the service."""
    executing = conn.execute("SELECT count(*) FROM items WHERE status IN ('executing',"
                             " 'undoing')").fetchone()[0]  # fmt: skip
    leases = conn.execute("SELECT count(*) FROM leases WHERE expires_at > ?",
                          (to_ts(clock.now()),)).fetchone()[0]  # fmt: skip
    out: list[str] = []
    if executing:
        out.append(f"{executing} action(s) are running; wait for them")
    if leases:
        out.append(f"{leases} lease(s) are held (a check or review is running); wait for them")
    if sessions:
        out.append(f"{sessions} `ecf claude` session(s) are open; close them first")
    return out


def preview(conn: sqlite3.Connection, clock: Clock, install: str, root: Path,
            sessions: int) -> dict[str, Any]:  # fmt: skip
    """`GET /v1/destroy`: what the CLI shows before asking (and whether to offer an export)."""
    last = conn.execute("SELECT max(ts) FROM audit WHERE event = 'export.completed' AND"
                        " outcome = 'ok'").fetchone()[0]  # fmt: skip
    sched = cast("dict[str, Any]", scheduled_export.status(conn).get("last_ok") or {})
    last_export = max((t for t in (last, sched.get("at")) if t), default=None)
    return {
        "install": install,
        "install_id": install_identity.install_id(conn),
        "role": _role(conn),
        "addresses": _addresses(conn),
        "slack": {"app_id": slack_admin.setting(conn, slack_admin.APP_ID) or None,
                  "channels": len(slack_routes.list_channels(conn))},
        "export_dir": export_dir(conn),
        "last_export_at": last_export,
        "busy": busy(conn, clock, sessions),
        "record": read_record(root, install),
    }  # fmt: skip


@stepup.purpose("destroy")
def _describe(conn: sqlite3.Connection, target: dict[str, Any]) -> stepup.Bound:
    install = str(target.get("install", ""))
    iid = install_identity.install_id(conn)
    return stepup.Bound(stepup.digest("destroy", install, iid),
                        f"ecf: destroy the install {install} (its database, data folder,"
                        " secrets and Slack channels)")  # fmt: skip


def run(
    conn: sqlite3.Connection,
    ctx: Context,
    *,
    typed: str,
    config_token: str | None,
    nonce: str | None,
    sessions: int = 0,
    check_busy: bool = True,
) -> dict[str, Any]:
    """The service's part of a destroy; returns the record (with what is left to do by hand)."""
    if typed != ctx.install:
        raise InvalidInputError(f"type the install name exactly ({ctx.install}) to destroy it")
    if check_busy and (problems := busy(conn, ctx.clock, sessions)):
        raise ConflictError("; ".join(problems))
    stepup.consume(conn, ctx.clock, "destroy", {"install": ctx.install}, nonce)
    before: dict[str, Any] = read_record(ctx.root, ctx.install) or {}
    fresh: dict[str, Any] = {"install": ctx.install, "steps": {},
                             "install_id": install_identity.install_id(conn),
                             "started_at": to_ts(ctx.clock.now())}  # fmt: skip
    rec = fresh | before
    rec["phase"] = "started"
    rec["addresses"] = _addresses(conn)
    rec["export_dir"] = export_dir(conn)
    rec["slack_app_id"] = slack_admin.setting(conn, slack_admin.APP_ID) or None
    _start(conn, ctx.clock)
    _save(ctx, rec)
    steps = cast("dict[str, Any]", rec["steps"])
    bot = _get_bot(ctx.store)
    for name, step in (
        ("notice", lambda: _notice(conn, ctx, bot)),
        ("deadman", lambda: _deadman(conn, ctx, bot)),
        ("channels", lambda: _channels(conn, ctx, bot)),
        ("slack_app", lambda: _slack_app(conn, ctx, bot, config_token)),
        ("secrets", lambda: _secrets(conn, ctx)),
    ):
        prior = cast("dict[str, Any] | None", steps.get(name))
        if prior is not None and not (name == "slack_app" and config_token
                                      and prior.get("result") != "deleted"):  # fmt: skip
            continue  # done before (a retry with a token may still delete the app)
        got = step()
        if prior is not None and got.get("result") != "deleted":
            got = prior | {"delete_failed": got.get("delete_failed", got.get("result"))}
        steps[name] = got
        _save(ctx, rec)
    now = to_ts(ctx.clock.now())
    rec["phase"], rec["service_done_at"] = SERVICE_DONE, now
    rec["residue"] = residue(rec)  # the CLI prints it, on a resumed run too
    _save(ctx, rec)
    with write_tx(conn):
        conn.execute("INSERT INTO audit (ts, event, actor, outcome, data) VALUES (?,"
                     " 'destroy.completed', 'os_user', 'ok', ?)",
                     (now, json.dumps({"person": stepup.person(), "steps": steps})))  # fmt: skip
    return rec


def residue(rec: dict[str, Any]) -> list[str]:
    """What the service's part couldn't do or can't do (the CLI adds its own lines)."""
    out: list[str] = []
    steps = rec.get("steps", {})
    app = steps.get("slack_app", {})
    if app.get("result") != "deleted" and rec.get("slack_app_id"):
        aid = rec["slack_app_id"]
        how = "its bot token is revoked" if app.get("result") == "revoked" else (
            "its bot token may still work")  # fmt: skip
        out.append(
            f"Slack app {aid} still exists ({how}): delete it at"
            f" https://api.slack.com/apps/{aid} (Settings, Basic Information, Delete App)"
        )
    for ch in steps.get("channels", {}).get("failed", []):
        out.append(f"Slack channel {ch['name'] or ch['channel']} wasn't archived"
                   f" ({ch['code']}): archive it in Slack")  # fmt: skip
    if steps.get("deadman", {}).get("result") == "failed":
        out.append("the dead-man's message in the summary channel may still post once")
    for a in rec.get("addresses", []):
        where = f" ({a['imap_host']})" if a.get("imap_host") else ""
        out.append(f"revoke the app password for {a['email']} at your mail provider{where}")
    if rec.get("export_dir"):
        out.append(f"backups in {rec['export_dir']} are kept; delete them when you no longer"
                   " need them (they need your backup key or passphrase to open)")  # fmt: skip
    return out


# ---- the steps ----------------------------------------------------------------------------------


def _start(conn: sqlite3.Connection, clock: Clock) -> None:
    pause.set_paused(conn, clock, pause.ALL, True, actor="os_user")
    now = to_ts(clock.now())
    with write_tx(conn):
        if conn.execute("SELECT 1 FROM settings WHERE key = ?", (STARTED,)).fetchone():
            return  # a retry: started once already
        conn.execute("INSERT INTO settings (key, value, updated_at, updated_by) VALUES (?, ?, ?,"
                     " 'os_user')", (STARTED, json.dumps(now), now))  # fmt: skip
        conn.execute("INSERT INTO audit (ts, event, actor, outcome, data) VALUES (?,"
                     " 'destroy.started', 'os_user', 'ok', ?)",
                     (now, json.dumps({"person": stepup.person()})))  # fmt: skip


def _notice(conn: sqlite3.Connection, ctx: Context, bot: str | None) -> dict[str, Any]:
    """Straight to each surface (OD-384): the queues won't run again."""
    text = (f"ecf is destroying the install {ctx.install} on this computer: its Slack channels"
            " are archived and its database, data folder and secrets deleted. If this wasn't you,"
            " check the computer ecf runs on and change your mail app passwords.")  # fmt: skip
    head = alerts.title("security_notice")
    ctx.notifier.notify(head, text)
    posted = 0
    if bot:
        chat = SlackChat(ctx.make_web(bot))
        ident = slack_admin.identity(conn)
        summary = slack_routes.summary_route(conn)
        for route in [*([RouteRef(ident.member)] if ident and ident.member else []),
                      *([summary] if summary else [])]:  # fmt: skip
            try:
                chat.post(route, Card(head, text=text))
                posted += 1
            except SlackNetworkError as exc:
                raise _unreachable() from exc
            except SlackError:
                continue  # recorded by the count; the run goes on
    emailed = alert_mail.send_now(conn, ctx.clock, ctx.sender_for, head, text)
    return {"slack_posts": posted, "email": emailed}


def _deadman(conn: sqlite3.Connection, ctx: Context, bot: str | None) -> dict[str, Any]:
    if not bot:
        return {"result": "no_slack"}
    try:
        deadman.disarm(conn, ctx.make_web(bot))
    except SlackNetworkError as exc:
        raise _unreachable() from exc
    except SlackError as exc:
        return {"result": "failed", "code": exc.code}
    return {"result": "ok"}


def _channels(conn: sqlite3.Connection, ctx: Context, bot: str | None) -> dict[str, Any]:
    listed = slack_routes.list_channels(conn)
    if not bot:
        return {"archived": [], "failed": [{"name": c["name"], "channel": c["channel"],
                                            "code": "no_token"} for c in listed]}  # fmt: skip
    chat = SlackChat(ctx.make_web(bot))
    archived: list[str] = []
    failed: list[dict[str, str]] = []
    for c in listed:
        try:
            chat.archive(RouteRef(c["channel"]))
        except SlackNetworkError as exc:
            raise _unreachable() from exc
        except RouteGoneError:
            pass
        except SlackError as exc:
            if exc.code not in GONE:
                failed.append({"name": c["name"], "channel": c["channel"], "code": exc.code})
                continue
        archived.append(c["channel"])
    return {"archived": archived, "failed": failed}


def _slack_app(conn: sqlite3.Connection, ctx: Context, bot: str | None,
               config_token: str | None) -> dict[str, Any]:  # fmt: skip
    app_id = slack_admin.setting(conn, slack_admin.APP_ID) or slack_admin.setting(
        conn, slack_admin.PENDING_APP
    )
    out: dict[str, Any] = {"app_id": app_id or None}
    if app_id and config_token:
        try:
            ctx.make_web(config_token).call("apps.manifest.delete", app_id=app_id)
        except SlackNetworkError as exc:
            raise _unreachable() from exc
        except SlackError as exc:
            if exc.code in {"app_not_found", "invalid_app_id"}:
                return out | {"result": "deleted", "note": exc.code}
            out["delete_failed"] = exc.code  # e.g. an expired token: fall back to revoking
        else:
            return out | {"result": "deleted"}
    if not bot:
        return out | {"result": "no_token" if app_id else "no_slack"}
    try:
        ctx.make_web(bot).call("auth.revoke")
    except SlackNetworkError as exc:
        raise _unreachable() from exc
    except SlackError as exc:
        if exc.code not in REVOKED:
            return out | {"result": "failed", "code": exc.code}
    return out | {"result": "revoked"}


def _secrets(conn: sqlite3.Connection, ctx: Context) -> dict[str, Any]:
    deleted: list[str] = []
    for name in regrant.names(conn):
        try:
            ctx.store.delete(name)
        except SecretStoreNeedsYouError as exc:
            raise ServiceUnavailableError(
                f"the secret store refused to delete {name} ({exc}); unlock it and run"
                " `ecf destroy` again: what's done is skipped"
            ) from None
        deleted.append(name)
    return {"deleted": deleted}


# ---- helpers ------------------------------------------------------------------------------------


def _get_bot(store: SecretStore) -> str | None:
    try:
        return store.get(slack_admin.BOT_SECRET)
    except SecretStoreNeedsYouError as exc:
        raise ServiceUnavailableError(
            f"the secret store can't be read ({exc}); unlock it and run `ecf destroy` again"
        ) from None


def _unreachable() -> ServiceUnavailableError:
    return ServiceUnavailableError("Slack couldn't be reached; nothing more was deleted. Run"
                                   " `ecf destroy` again: what's done is skipped")  # fmt: skip


def _save(ctx: Context, rec: dict[str, Any]) -> None:
    write_record(ctx.root, ctx.install, rec)


def _role(conn: sqlite3.Connection) -> str:
    from ecf_server import initsetup  # noqa: PLC0415

    return initsetup.role(conn) or "prod"


def _addresses(conn: sqlite3.Connection) -> list[dict[str, str]]:
    rows = conn.execute("SELECT a.address_id, a.email, p.host FROM addresses a LEFT JOIN probe p"
                        " USING (address_id) WHERE a.removed_at IS NULL"
                        " ORDER BY a.address_id").fetchall()  # fmt: skip
    return [
        {"address_id": r["address_id"], "email": r["email"], "imap_host": r["host"] or ""}
        for r in rows
    ]


# ---- in the foreground --------------------------------------------------------------------------


def main(install: str, confirm: str, *, ask_token: bool) -> int:
    """`ecf-server destroy --install <name> --confirm <name> [--config-token]` (OD-387): the
    service's part when the service can't run. 0 done, 1 step-up refused, 3 service running."""
    import getpass  # noqa: PLC0415
    import sys  # noqa: PLC0415

    from ecf.paths import paths_for  # noqa: PLC0415
    from ecf_server import _slack, db  # noqa: PLC0415
    from ecf_server.clock import SystemClock  # noqa: PLC0415
    from ecf_server.notify import host_notifier  # noqa: PLC0415
    from ecf_server.secretstore.select import host_probe, open_store  # noqa: PLC0415
    from ecf_server.service import (  # noqa: PLC0415
        AlreadyRunningError,
        acquire_lock,
        make_sender,
        smtp_factory,
    )
    from ecf_server.stepper import host_stepper  # noqa: PLC0415

    paths = paths_for(install, for_service=True)
    clock = SystemClock()
    if not paths.db.exists():  # nothing of the service's is left: only the CLI's part remains
        rec = read_record(paths.root, install) or {"install": install, "steps": {},
                                                   "started_at": to_ts(clock.now())}  # fmt: skip
        if rec.get("phase") != DONE:
            write_record(paths.root, install, rec | {"phase": SERVICE_DONE})
        sys.stdout.write("no database here: nothing for the service to do\n")
        return 0
    try:
        lock = acquire_lock(paths)
    except AlreadyRunningError:
        sys.stderr.write("ecf-server: the service is running; use `ecf destroy`\n")
        return 3
    try:
        conn = db.connect(paths.db)
        try:
            db.migrate(conn)
            store = open_store(install, paths.data_dir, interactive=True, probe=host_probe())
            stepper = host_stepper()
            issued = stepup.issue(conn, clock, stepper, "destroy", {"install": install})
            password = getpass.getpass("your login password: ") if issued.needs_password else None
            if stepup.verify(conn, clock, stepper, issued.nonce_id,
                             password=password) != "verified":  # fmt: skip
                sys.stderr.write("ecf-server: step-up refused; nothing was destroyed\n")
                return 1
            token = (getpass.getpass("Slack configuration token (Enter to skip): ").strip()
                     if ask_token else "") or None  # fmt: skip
            ctx = Context(install, paths.root, clock, host_notifier(), store, _slack.Web,
                          lambda c, aid: make_sender(c, store, smtp_factory, aid))  # fmt: skip
            run(
                conn,
                ctx,
                typed=confirm,
                config_token=token,
                nonce=issued.nonce_id,
                check_busy=False,
            )  # nothing runs while the service is stopped
        finally:
            conn.close()
    finally:
        lock.close()
    sys.stdout.write(f"the service's part is done; record: {record_path(paths.root, install)}\n")
    return 0
