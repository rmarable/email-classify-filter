"""Setting up `ecf claude` (SPEC §13.1, §13.2; V1.4 step 11): its own Claude login, and that the
installed `ecf-mcp` and plugin templates are the ones the package was installed with.

The login is Claude Code's own (`claude auth login` and `claude auth status`, in ecf's config
folder; `claude auth --help` of 2.1.287); ecf never sees it. `auth status --json` also names the
account's email and organization; only `loggedIn`, `authMethod` and `subscriptionType` are kept.

The package's RECORD (written by the installer) holds a SHA-256 of each installed file, the
`ecf-mcp` script too, so a script or template changed after install shows up. An editable
install (development) lists neither the templates nor the source tree: they are not checked.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from importlib import metadata, resources
from pathlib import Path
from typing import Any, cast

from ecf import __version__
from ecf.claude_wrapper import SKILLS, Layout, base_env, find_claude, layout
from ecf.paths import Paths

DIST = "email-classify-filter"
# The templates `render_plugin` reads.
TEMPLATES = (".claude-plugin/plugin.json", *(f"skills/{k}/SKILL.md" for k in SKILLS),
             "agents/classifier.md", "agents/actor.md")  # fmt: skip


@dataclass(frozen=True)
class Login:
    logged_in: bool
    method: str | None = None
    plan: str | None = None

    def describe(self) -> str:
        if not self.logged_in:
            return "not logged in"
        return f"logged in ({', '.join(x for x in (self.method, self.plan) if x)})"


def auth_status(claude: str, lay: Layout) -> Login | None:
    """The login in ecf's config folder; None when Claude Code didn't answer. No folder yet
    means no login (`claude auth status` would create one)."""
    if not lay.config_dir.exists():
        return Login(False)
    try:
        out = subprocess.run([claude, "auth", "status", "--json"], capture_output=True,  # noqa: S603
                             text=True, timeout=30, check=False, env=base_env(lay))  # fmt: skip
        loaded: Any = json.loads(out.stdout)  # exit 1 when logged out, with the same JSON
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    if not isinstance(loaded, dict):
        return None
    d = cast(dict[str, Any], loaded)
    method, plan = d.get("authMethod"), d.get("subscriptionType")
    return Login(d.get("loggedIn") is True, method if isinstance(method, str) else None,
                 plan if isinstance(plan, str) else None)  # fmt: skip


def login(claude: str, lay: Layout) -> int:
    """`claude auth login` in ecf's config folder (interactive: it opens the browser)."""
    for d in (lay.config_dir, lay.work_dir):
        d.mkdir(mode=0o700, parents=True, exist_ok=True)
        d.chmod(0o700)
    args = [claude, "auth", "login"]
    return subprocess.run(args, cwd=lay.work_dir, env=base_env(lay), check=False).returncode  # noqa: S603


def run_login(paths: Paths, echo: Callable[[str], None] = print) -> int:
    """`ecf claude --login`: log in once; a login already there is left alone."""
    claude = find_claude()
    lay = layout(paths)
    before = auth_status(claude, lay)
    if before is not None and before.logged_in:
        echo(f"ecf's Claude configuration is already {before.describe()}.")
        return 0
    echo("Logging in to Claude for `ecf claude`, in its own configuration folder; your usual"
         " Claude Code login is unchanged.")  # fmt: skip
    rc = login(claude, lay)
    after = auth_status(claude, lay)
    echo(f"ecf claude: {after.describe() if after else 'login state unknown (claude auth status)'}")
    return 0 if after is not None and after.logged_in else (rc or 1)


# ---------------------------------------------------------------------------- integrity


def _sha256(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).digest()
    return "sha256=" + base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _recorded(f: metadata.PackagePath) -> str | None:
    return f"{f.hash.mode}={f.hash.value}" if f.hash else None


def installed() -> metadata.Distribution | None:
    try:
        return metadata.distribution(DIST)
    except metadata.PackageNotFoundError:
        return None


def editable(dist: metadata.Distribution) -> bool:
    try:
        loaded: Any = json.loads(dist.read_text("direct_url.json") or "{}")
    except ValueError:
        return False
    info: Any = cast(dict[str, Any], loaded).get("dir_info") if isinstance(loaded, dict) else None
    return isinstance(info, dict) and cast(dict[str, Any], info).get("editable") is True


def _entries(dist: metadata.Distribution) -> dict[Path, metadata.PackagePath]:
    out: dict[Path, metadata.PackagePath] = {}
    for f in dist.files or []:
        try:
            out[Path(str(f.locate())).resolve()] = f
        except OSError:
            continue
    return out


def file_problem(entries: dict[Path, metadata.PackagePath], path: Path) -> str | None:
    """Why `path` isn't the file the package installed, or None."""
    f = entries.get(path.resolve())
    want = _recorded(f) if f is not None else None
    if want is None:
        return "not in the installed package's RECORD"
    try:
        found = _sha256(path)
    except OSError as exc:
        return f"unreadable ({exc.strerror})"
    return None if found == want else "changed since install (its hash differs from RECORD)"


def mcp_problem(dist: metadata.Distribution, ecf_mcp: Path) -> str | None:
    if not os.access(ecf_mcp, os.X_OK):
        return "not executable"
    return file_problem(_entries(dist), ecf_mcp)


def plugin_problems(dist: metadata.Distribution) -> list[str]:
    """Each template that isn't the one installed, and a version the plugin would get that
    isn't the installed package's (the plugin carries `ecf.__version__`, OD-283)."""
    out: list[str] = []
    if dist.version != __version__:
        out.append(f"package {dist.version}, plugin {__version__}")
    if editable(dist):
        return out
    entries = _entries(dist)
    root = resources.files("ecf.data").joinpath("plugin")
    for t in TEMPLATES:
        problem = file_problem(entries, Path(str(root.joinpath(*t.split("/")))))
        if problem:
            out.append(f"{t}: {problem}")
    return out
