"""The Keychain re-grant (SPEC §11.6; OD-163, OD-348; V1.5 step 8c): `ecf-server regrant`.

The Keychain trusts an item's readers by the interpreter binary's code hash, so after a Python
change the service, which runs with prompts off, can't read its secrets (§21.2). The re-grant runs
in the foreground with the same interpreter as the service unit and prompts on, and reads every
secret ecf keeps; at each dialog you enter your login password and choose **Always Allow**, which
adds this interpreter to the item's access list for good (tested 2026-10-02; Allow once adds
nothing). Only after every read succeeds is the new interpreter hash recorded, which clears the
"Python changed" warning in `ecf status` and `ecf doctor`. It refuses while the service runs
(`ecf service regrant` stops and restarts it), so the two never race on the database.

Linux backends don't bind access to the interpreter; there it only says so.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from collections.abc import Callable
from dataclasses import dataclass

from ecf_server.clock import Clock, to_ts
from ecf_server.db import write_tx
from ecf_server.secretstore import SecretStore, SecretStoreNeedsYouError
from ecf_server.secretstore.select import interpreter_sha256, record_interpreter

FIXED_NAMES = ("slack/bot", "slack/app", "models-api-key", "export-signing-seed")


@dataclass(frozen=True)
class Outcome:
    read: list[str]  # names present and now readable
    absent: list[str]  # names ecf could keep but doesn't have
    failed: str | None  # the name whose read was refused, if any


def names(conn: sqlite3.Connection) -> list[str]:
    """Every secret ecf may keep: one app password per address (removed ones are deleted with the
    address), the Slack tokens, the models API key and the export signing seed."""
    rows = conn.execute("SELECT address_id FROM addresses WHERE removed_at IS NULL"
                        " ORDER BY address_id").fetchall()  # fmt: skip
    return [*(f"mailbox/{r[0]}" for r in rows), *FIXED_NAMES]


def run(conn: sqlite3.Connection, clock: Clock, store: SecretStore, *,
        echo: Callable[[str], None] = print, interpreter: Callable[[], str] = interpreter_sha256,
        ) -> Outcome:  # fmt: skip
    read: list[str] = []
    absent: list[str] = []
    for name in names(conn):
        echo(f"reading {name} ...")
        try:
            value = store.get(name)
        except SecretStoreNeedsYouError:
            _audit(conn, clock, "error", {"read": len(read), "failed": name})
            return Outcome(read, absent, name)
        (read if value is not None else absent).append(name)
    record_interpreter(conn, clock, interpreter(), by="regrant")
    _audit(conn, clock, "ok", {"read": len(read), "absent": len(absent)})
    return Outcome(read, absent, None)


def _audit(conn: sqlite3.Connection, clock: Clock, outcome: str, data: dict[str, object]) -> None:
    with write_tx(conn):
        conn.execute("INSERT INTO audit (ts, event, actor, outcome, data) VALUES (?,"
                     " 'secret.regranted', 'os_user', ?, ?)",
                     (to_ts(clock.now()), outcome, json.dumps(data)))  # fmt: skip


INTRO = (
    "ecf reads each of its secrets once, so this Python can read them again.\n"
    "macOS shows a dialog for each one it doesn't trust this Python for: enter your login\n"
    "password and choose Always Allow (not Allow: that works once and the service still\n"
    "can't read it)."
)


def _say(text: str) -> None:
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def main(install: str) -> int:
    """`ecf-server regrant --install <name>`: 0 done, 1 a read was refused, 3 service running."""
    from ecf.paths import paths_for  # noqa: PLC0415
    from ecf_server import db  # noqa: PLC0415
    from ecf_server.clock import SystemClock  # noqa: PLC0415
    from ecf_server.secretstore.select import (  # noqa: PLC0415
        choose_backend,
        host_probe,
        open_store,
    )
    from ecf_server.service import AlreadyRunningError, acquire_lock  # noqa: PLC0415

    backend = choose_backend(host_probe())
    if backend != "keychain":
        sys.stdout.write(f"no re-grant needed: {backend} doesn't tie access to the Python binary\n")
        return 0
    paths = paths_for(install, for_service=True)
    try:
        lock = acquire_lock(paths)
    except AlreadyRunningError:
        sys.stderr.write("ecf-server: the service is running; stop it first (`ecf service"
                         " regrant` does this for you)\n")  # fmt: skip
        return 3
    try:
        conn = db.connect(paths.db)
        try:
            db.migrate(conn)
            store = open_store(install, paths.data_dir, interactive=True, probe=host_probe())
            sys.stdout.write(INTRO + "\n")
            got = run(conn, SystemClock(), store, echo=_say)
        finally:
            conn.close()
    finally:
        lock.close()
    if got.failed:
        sys.stderr.write(f"ecf-server: reading {got.failed} was refused; nothing recorded. Run it"
                         " again and choose Always Allow.\n")  # fmt: skip
        return 1
    sys.stdout.write(f"done: {len(got.read)} secret(s) readable"
                     + (f", {len(got.absent)} not set" if got.absent else "") + "\n")  # fmt: skip
    return 0
