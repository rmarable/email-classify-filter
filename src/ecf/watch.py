"""`ecf watch` (SPEC §4.1, §10.2; V1.3 step 7): run the service in this terminal instead of the
background unit, and put the unit back afterwards.

1. If the background unit is running, it is stopped, and `ecf watch` waits (up to 60 s, the stop
   timeout) until the service has released its single-instance lock, so two never run at once.
2. A marker in the run folder records that `ecf watch` took over and whether to restore the unit.
3. The service runs as a child process with an allow-listed environment (the security review of the
   V1.3 plan): HOME, PATH, LANG, TZ and ECF_HOME; on macOS TMPDIR; on Linux the session variables
   the secret store and desktop notifications need. Nothing else from this shell reaches it (no
   OLLAMA_HOST, no proxy variables). Ctrl-C stops it.
4. When it ends, the marker goes and the unit is started again if it was running before.

If `ecf watch` itself is killed, the marker stays: `ecf status` and `ecf doctor` say so and
`ecf service start` restores the unit and clears it. (The design plan said `execv`; a child process
lets `ecf watch` restore the unit, so it is used instead.)
"""

from __future__ import annotations

import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ecf.errors import ConflictError
from ecf.paths import Paths
from ecf.service_unit import STOP_TIMEOUT_S, ServiceManager, ecf_server_path

MARKER = "watch.json"
ALWAYS = ("HOME", "PATH", "LANG", "TZ", "ECF_HOME")
MACOS = ("TMPDIR",)
LINUX = ("DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR", "DISPLAY", "WAYLAND_DISPLAY")


def environment(source: dict[str, str], platform: str = sys.platform) -> dict[str, str]:
    names = ALWAYS + (MACOS if platform == "darwin" else LINUX)
    return {k: source[k] for k in names if k in source}


def marker_path(paths: Paths) -> Path:
    return paths.run_dir / MARKER


def marker(paths: Paths) -> dict[str, Any] | None:
    """The marker, with `alive` telling whether the `ecf watch` that wrote it still runs."""
    try:
        data: dict[str, Any] = json.loads(marker_path(paths).read_text())
    except (OSError, ValueError):
        return None
    try:
        os.kill(int(data.get("pid", 0)), 0)
        data["alive"] = True
    except (OSError, ValueError):
        data["alive"] = False
    return data


def clear(paths: Paths) -> None:
    marker_path(paths).unlink(missing_ok=True)


def wait_for_lock(paths: Paths, timeout_s: float = STOP_TIMEOUT_S) -> None:
    """Until the service's single-instance lock is free (§11.1)."""
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            with paths.lock.open("a") as f:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(f, fcntl.LOCK_UN)
                return
        except BlockingIOError:
            if time.monotonic() > deadline:
                raise ConflictError(
                    f"the service didn't stop within {timeout_s:.0f} s; try again"
                ) from None
            time.sleep(0.2)
        except FileNotFoundError:
            return  # no data folder yet: nothing holds it


def run(paths: Paths, manager: ServiceManager, *, server: Path | None = None) -> int:
    was_running = manager.status().running
    if was_running:
        manager.stop()
    paths.run_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        wait_for_lock(paths)
    except ConflictError:
        if was_running:  # don't leave the background service down
            manager.start()
        raise
    marker_path(paths).write_text(json.dumps({
        "pid": os.getpid(), "restore_unit": was_running,
        "started_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}))  # fmt: skip
    cmd = [str(server or ecf_server_path()), "local", "--install", paths.install, "--foreground"]
    proc = subprocess.Popen(cmd, env=environment(dict(os.environ)))  # noqa: S603
    try:
        while True:
            try:
                code = proc.wait()
                break
            except KeyboardInterrupt:  # Ctrl-C reaches the child too; wait for it to stop
                proc.send_signal(signal.SIGINT)
    finally:
        clear(paths)
        if was_running:
            manager.start()
    return code
