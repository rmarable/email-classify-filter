"""scripts/uninstall-ecf-macos.sh (SPEC §17.4; OD-526): `ecf destroy` for each install, then ecf
itself, then what's left; the tools install-ecf-macos.sh installed are offered, default no. Runs
under /bin/sh against fakes (tests/script_fakes.py)."""

from __future__ import annotations

import json
from pathlib import Path

from tests.script_fakes import ECF, UNINSTALL, Mac


def _installed(mac: Mac, **how: str) -> None:
    """ecf installed with uv tool, and install-ecf-macos.sh's record of how each tool came."""
    (mac.home / ".fake-uv-tools").write_text("email-classify-filter v3.0.4\n")
    mac.bin.mkdir(parents=True, exist_ok=True)
    for name in ("uv", "uvx", "gh", "claude"):
        (mac.bin / name).write_text("#!/bin/sh\n")
    (mac.root / "_installer").mkdir(parents=True, exist_ok=True)
    rec = {"installed_at": "2026-10-16T12:00:00Z", "ecf_tag": "v3.0.4", "python": "3.12",
           "bin_dir": str(mac.bin), "uv": how.get("uv", "already"), "gh": how.get("gh", "already"),
           "claude_code": how.get("claude", "already"), "zprofile_path_line": True}  # fmt: skip
    (mac.root / "_installer" / "install-record.json").write_text(json.dumps(rec, indent=2) + "\n")
    mac.install("default")
    (mac.fakes / "ecf").write_text(ECF)
    (mac.fakes / "ecf").chmod(0o755)


def test_destroys_each_install_then_removes_ecf(tmp_path: Path) -> None:
    mac = Mac(tmp_path)
    _installed(mac)
    mac.install("work")
    # destroy default (no token), destroy work (with token), keep the leftover folder
    r = mac.run(UNINSTALL, stdin="y\nn\ny\ny\nn\n")
    assert r.returncode == 0, r.stdout + r.stderr
    calls = mac.calls()
    assert "ecf --install default destroy" in calls
    assert "ecf --install work destroy --config-token" in calls
    assert "uv tool uninstall email-classify-filter" in calls
    assert "app password at your email provider" in r.stdout
    assert "uv was already here before ecf; leaving it." in r.stdout
    assert (mac.root / "_installer").exists()


def test_a_failed_destroy_exits_1(tmp_path: Path) -> None:
    mac = Mac(tmp_path)
    _installed(mac)
    (mac.fakes / "ecf").write_text('#!/bin/sh\necho "ecf $*" >>"$LOG"\nexit 1\n')
    r = mac.run(UNINSTALL, stdin="y\nn\n")
    assert r.returncode == 1
    assert "ecf destroy didn't finish for default." in r.stdout
    assert not any(c.startswith("uv tool uninstall") for c in mac.calls())


def test_a_kept_install_keeps_ecf(tmp_path: Path) -> None:
    mac = Mac(tmp_path)
    _installed(mac)
    r = mac.run(UNINSTALL, stdin="n\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Still installed: default" in r.stdout
    assert not any(c.startswith(("uv tool uninstall", "ecf ")) for c in mac.calls())


def test_tools_it_installed_are_kept_by_default_and_removed_on_yes(tmp_path: Path) -> None:
    mac = Mac(tmp_path)
    _installed(mac, uv="release", gh="release", claude="official")
    # destroy (no token), keep the folder, then: uv default, gh yes, claude default
    r = mac.run(UNINSTALL, stdin="y\nn\nn\n\ny\n\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert (mac.bin / "uv").exists() and (mac.bin / "claude").exists()
    assert not (mac.bin / "gh").exists()
    assert not any(c.startswith("uv cache") for c in mac.calls())
    assert "PATH line" in r.stdout


def test_homebrew_tools_are_removed_with_brew(tmp_path: Path) -> None:
    mac = Mac(tmp_path)
    _installed(mac, uv="homebrew", gh="homebrew", claude="homebrew")
    r = mac.run(UNINSTALL, stdin="y\nn\nn\ny\ny\ny\n")
    assert r.returncode == 0, r.stdout + r.stderr
    calls = mac.calls()
    assert "brew uninstall uv" in calls and "brew uninstall gh" in calls
    assert "brew uninstall --cask claude-code" in calls


def test_deleting_the_leftover_folder_still_reads_the_record_first(tmp_path: Path) -> None:
    mac = Mac(tmp_path)
    _installed(mac, gh="release")
    r = mac.run(UNINSTALL, stdin="y\nn\ny\ny\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert not mac.root.exists()
    assert not (mac.bin / "gh").exists()


def test_dry_run_changes_nothing(tmp_path: Path) -> None:
    mac = Mac(tmp_path)
    _installed(mac, uv="release", gh="release", claude="official")
    before = mac.files_under_home()
    r = mac.run(UNINSTALL, "--dry-run", stdin="y\nn\ny\ny\ny\ny\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert mac.files_under_home() == before
    assert not any(c.startswith(("ecf ", "uv tool uninstall", "brew")) for c in mac.calls())


def test_nothing_installed(tmp_path: Path) -> None:
    mac = Mac(tmp_path)
    r = mac.run(UNINSTALL)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "No ecf install found" in r.stdout
    assert "No install record" in r.stdout


def test_ecf_missing_with_an_install_left_stops(tmp_path: Path) -> None:
    mac = Mac(tmp_path)
    mac.install("default")
    r = mac.run(UNINSTALL)
    assert r.returncode == 1
    assert "ecf itself is missing" in r.stdout
