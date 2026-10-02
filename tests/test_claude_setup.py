"""`ecf claude`'s own login and the install check behind doctor (V1.4 step 11)."""

from __future__ import annotations

import base64
import hashlib
import json
from importlib import metadata
from pathlib import Path

import pytest
from typer.testing import CliRunner

import ecf
from ecf import claude_setup as cs
from ecf import claude_wrapper as cw
from ecf import doctor
from ecf.cli import app
from ecf.doctor import Level
from ecf.paths import Paths

# Claude Code's `auth status --json` (shape of 2.1.287), and `auth login` that logs in.
FAKE_CLAUDE = """#!/bin/sh
if [ "$1" = "--version" ]; then echo "2.1.287 (Claude Code)"; exit 0; fi
echo "$* CFG=$CLAUDE_CONFIG_DIR KEY=$ANTHROPIC_API_KEY PWD=$(pwd)" >> "{record}"
if [ "$1 $2" = "auth login" ]; then touch "$CLAUDE_CONFIG_DIR/in"; exit 0; fi
if [ -f "$CLAUDE_CONFIG_DIR/in" ]; then
  echo '{{"loggedIn": true, "authMethod": "claude.ai", "email": "x@example.com", "orgId": "o",
         "subscriptionType": "max"}}'; exit 0
fi
echo '{{"loggedIn": false, "authMethod": "none"}}'; exit 1
"""


@pytest.fixture
def claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[str, Path]:
    record = tmp_path / "record.txt"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    f = bin_dir / "claude"
    f.write_text(FAKE_CLAUDE.format(record=record))
    f.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-not-passed")
    return str(f), record


def test_login_status_keeps_only_the_method_and_plan(
    claude: tuple[str, Path], tmp_path: Path
) -> None:
    exe, record = claude
    lay = cw.layout(Paths("t", tmp_path / "home"))
    assert cs.auth_status(exe, lay) == cs.Login(False)  # no config folder: not asked
    assert not record.exists()
    assert cs.login(exe, lay) == 0
    assert stat_mode(lay.config_dir) == 0o700
    login = cs.auth_status(exe, lay)
    assert login == cs.Login(True, "claude.ai", "max")
    assert login is not None and login.describe() == "logged in (claude.ai, max)"
    lines = record.read_text().splitlines()
    assert lines[0].startswith(f"auth login CFG={lay.config_dir} KEY= PWD=")  # no API key passed
    assert lines[0].endswith(str(lay.work_dir.resolve()))
    assert lines[1].startswith("auth status --json CFG=")


def stat_mode(p: Path) -> int:
    return p.stat().st_mode & 0o777


def test_a_claude_that_says_nothing_useful(tmp_path: Path) -> None:
    lay = cw.layout(Paths("t", tmp_path))
    lay.config_dir.mkdir(parents=True)
    bad = tmp_path / "claude"
    bad.write_text("#!/bin/sh\necho not json\n")
    bad.chmod(0o755)
    assert cs.auth_status(str(bad), lay) is None
    assert cs.auth_status(str(tmp_path / "missing"), lay) is None


def test_ecf_claude_login(claude: tuple[str, Path], tmp_path: Path) -> None:
    home = {"ECF_HOME": str(tmp_path / "home")}
    r = CliRunner().invoke(app, ["--install", "t", "claude", "--login"], env=home)
    assert r.exit_code == 0, r.output
    assert "your usual Claude Code login is unchanged" in r.output
    assert "ecf claude: logged in (claude.ai, max)" in r.output
    r = CliRunner().invoke(app, ["--install", "t", "claude", "--login"], env=home)
    assert r.exit_code == 0 and "already logged in" in r.output
    assert claude[1].read_text().count("auth login") == 1  # not asked twice
    r = CliRunner().invoke(app, ["--install", "t", "claude", "--login", "--verbose"], env=home)
    assert r.exit_code == 2 and "no other arguments" in r.output


# ---- RECORD


def _sha(data: bytes) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


def _dist(site: Path, files: dict[str, bytes], recorded: dict[str, bytes]) -> metadata.Distribution:
    """A dist-info folder whose RECORD lists `recorded` (path -> the bytes installed)."""
    info = site / "pkg-1.0.dist-info"
    info.mkdir(parents=True)
    for rel, data in files.items():
        (site / rel).parent.mkdir(parents=True, exist_ok=True)
        (site / rel).write_bytes(data)
    rows = [f"{rel},sha256={_sha(data)},{len(data)}" for rel, data in recorded.items()]
    (info / "RECORD").write_text("\n".join([*rows, "pkg-1.0.dist-info/RECORD,,"]) + "\n")
    (info / "METADATA").write_text("Metadata-Version: 2.1\nName: pkg\nVersion: 1.0\n")
    return metadata.PathDistribution(info)


def test_file_problem_compares_with_record(tmp_path: Path) -> None:
    site = tmp_path / "site"
    dist = _dist(site, {"a.txt": b"same", "b.txt": b"edited", "c.txt": b"extra"},
                 {"a.txt": b"same", "b.txt": b"original"})  # fmt: skip
    entries = cs._entries(dist)  # pyright: ignore[reportPrivateUsage]
    assert cs.file_problem(entries, site / "a.txt") is None
    assert "changed since install" in str(cs.file_problem(entries, site / "b.txt"))
    assert "not in the installed package's RECORD" in str(cs.file_problem(entries, site / "c.txt"))


def test_this_install_is_checked() -> None:
    dist = cs.installed()
    assert dist is not None and dist.version == ecf.__version__
    assert cs.mcp_problem(dist, cw.ecf_mcp_path()) is None
    assert cs.plugin_problems(dist) == []


def test_a_version_mismatch_is_a_plugin_problem(monkeypatch: pytest.MonkeyPatch) -> None:
    dist = cs.installed()
    assert dist is not None
    monkeypatch.setattr(cs, "__version__", "9.9.9")
    assert cs.plugin_problems(dist)[0] == f"package {dist.version}, plugin 9.9.9"


def test_editable_is_read_from_direct_url(tmp_path: Path) -> None:
    dist = _dist(tmp_path, {}, {})
    assert not cs.editable(dist)
    info = tmp_path / "pkg-1.0.dist-info" / "direct_url.json"
    info.write_text(json.dumps({"url": "file:///x", "dir_info": {"editable": True}}))
    assert cs.editable(dist)


# ---- doctor


def test_doctor_fails_claude_problems_only_once_b_or_c_is_used(
    claude: tuple[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = Paths("t", tmp_path / "home")
    checks = {c.name: c for c in doctor.check_claude(paths, True)}
    assert checks["claude code"].level is Level.OK and checks["claude code"].detail == "2.1.287"
    assert checks["ecf-mcp"].level is Level.OK and checks["claude plugin"].level is Level.OK
    assert checks["claude login"].level is Level.FAIL
    assert checks["claude login"].fix == "ecf claude --login"
    cs.login(claude[0], cw.layout(paths))
    checks = {c.name: c for c in doctor.check_claude(paths, True)}
    assert checks["claude login"].level is Level.OK
    assert checks["claude login"].detail == "ecf's own: logged in (claude.ai, max)"
    assert "claude login" not in {c.name for c in doctor.check_claude(paths, False)}
    monkeypatch.setenv("PATH", "/nonexistent")
    for used, level in ((True, Level.FAIL), (False, Level.WARN), (None, Level.WARN)):
        got = {c.name: c for c in doctor.check_claude(paths, used)}
        assert got["claude code"].level is level, used
        assert "claude login" not in got


def test_doctor_names_the_pins_in_force() -> None:
    pins = {"main_session": "claude-haiku-x", "classifier": "claude-haiku-x",
            "actor": "claude-sonnet-x"}  # fmt: skip
    checks = doctor.judge_claude({"claude": {"used": True, "pins": pins, "last_review": None}})
    assert [(c.name, c.detail) for c in checks] == [
        ("claude pins", "claude-haiku-x (main_session, classifier); claude-sonnet-x (actor)")
    ]
    assert doctor.judge_claude({"claude": {"used": False, "pins": None}}) == []
