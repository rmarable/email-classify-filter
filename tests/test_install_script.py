"""scripts/install-ecf-macos.sh (SPEC §17.4; OD-526): the Mac checks, installing uv, gh and Claude
Code from their vendors' own files, verifying the ecf release before anything is installed, and
the record the uninstaller reads. Runs under /bin/sh against fakes (tests/script_fakes.py)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tests.script_fakes import INSTALL, Mac, leftover_temp, sha

pytestmark = pytest.mark.skipif(shutil.which("unzip") is None, reason="needs unzip")


def test_installs_a_verified_release_with_tools_already_here(tmp_path: Path) -> None:
    mac = Mac(tmp_path)
    whl = mac.release()
    r = mac.run(INSTALL, stdin="n\n")  # don't touch ~/.zprofile
    assert r.returncode == 0, r.stdout + r.stderr
    kept = mac.root / "_installer" / "releases" / "v3.0.4" / whl
    assert kept.read_bytes() == b"wheel"
    assert f"uv tool install --python 3.12 {kept}" in mac.calls()
    assert "Verified:" in r.stdout and "can't show who published them" in r.stdout
    rec = json.loads((mac.root / "_installer" / "install-record.json").read_text())
    assert rec["ecf_tag"] == "v3.0.4" and rec["python"] == "3.12"
    assert (rec["uv"], rec["gh"], rec["claude_code"]) == ("already", "already", "already")
    assert rec["zprofile_path_line"] is False
    assert not (mac.home / ".zprofile").exists()
    assert "Run `gh auth login` before your first `ecf upgrade`." in r.stdout
    assert "run: ecf init" in r.stdout
    assert leftover_temp(tmp_path) == []


def test_installs_uv_gh_and_claude_from_their_vendors(tmp_path: Path) -> None:
    mac = Mac(tmp_path, tools=())
    mac.release()
    r = mac.run(INSTALL, "--version", "v3.0.4", stdin="y\n")
    assert r.returncode == 0, r.stdout + r.stderr
    for name in ("uv", "uvx", "gh", "claude"):
        assert (mac.bin / name).exists(), name
    assert any(c.startswith("codesign --verify --strict") for c in mac.calls())
    rec = json.loads((mac.root / "_installer" / "install-record.json").read_text())
    assert (rec["uv"], rec["gh"], rec["claude_code"]) == ("release", "release", "official")
    assert rec["zprofile_path_line"] is True
    assert 'export PATH="$HOME/.local/bin:$PATH"' in (mac.home / ".zprofile").read_text()


def test_a_tampered_uv_download_is_refused(tmp_path: Path) -> None:
    mac = Mac(tmp_path, tools=("gh", "claude"))
    mac.file("https://github.com/astral-sh/uv/releases/download/0.13.1/"
             "uv-aarch64-apple-darwin.tar.gz", b"not uv")  # fmt: skip
    mac.release()
    r = mac.run(INSTALL)
    assert r.returncode == 1
    assert "doesn't match its published sha256" in r.stderr
    assert not (mac.bin / "uv").exists()


def test_claude_not_signed_by_anthropic_is_refused(tmp_path: Path) -> None:
    mac = Mac(tmp_path, tools=("uv", "gh"))
    mac.env["CODESIGN_TEAM"] = "XXXXXXXXXX"
    mac.release()
    r = mac.run(INSTALL)
    assert r.returncode == 1
    assert "isn't signed by Anthropic" in r.stderr
    assert not any(c.startswith("uv tool install") for c in mac.calls())


def test_old_claude_is_updated(tmp_path: Path) -> None:
    mac = Mac(tmp_path)
    mac.env["CLAUDE_VERSION"] = "2.1.200"
    mac.release()
    r = mac.run(INSTALL)
    assert "claude update" in mac.calls()
    assert r.returncode == 1  # the fake stays old, so the floor check stops it
    assert "ecf needs 2.1.295 or later" in r.stderr


@pytest.mark.parametrize(
    ("kw", "why"),
    [
        ({"wheel": b"swapped", "sums_wheel": sha(b"wheel")}, "doesn't match its line in"),
        ({"manifest_tag": "v3.0.3"}, "release-manifest.json isn't for v3.0.4"),
        ({"drop_from_manifest": "install-ecf-macos.sh"}, "don't agree"),
    ],
)
def test_a_release_that_does_not_verify_installs_nothing(
    tmp_path: Path, kw: dict[str, object], why: str
) -> None:
    mac = Mac(tmp_path)
    mac.release(**kw)  # pyright: ignore[reportArgumentType]
    r = mac.run(INSTALL)
    assert r.returncode == 1
    assert why in r.stderr
    assert not any(c.startswith("uv tool install") for c in mac.calls())
    assert not (mac.root / "_installer").exists()
    assert leftover_temp(tmp_path) == []


def test_filevault_off_stops_unless_allowed(tmp_path: Path) -> None:
    mac = Mac(tmp_path)
    mac.env["FILEVAULT"] = "Off"
    mac.release()
    r = mac.run(INSTALL)
    assert r.returncode == 1 and "FileVault (disk encryption) is off" in r.stderr
    assert not any(c.startswith("curl") for c in mac.calls())
    r = mac.run(INSTALL, "--allow-no-filevault", stdin="n\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "continuing because of --allow-no-filevault" in r.stdout


def test_an_existing_install_is_left_alone(tmp_path: Path) -> None:
    mac = Mac(tmp_path)
    mac.release()
    (mac.home / ".fake-uv-tools").write_text("email-classify-filter v3.0.2\n")
    r = mac.run(INSTALL, stdin="n\n")
    assert r.returncode == 1 and "use `ecf upgrade`" in r.stderr
    assert not any(c.startswith("uv tool install") for c in mac.calls())


def test_dry_run_changes_nothing(tmp_path: Path) -> None:
    mac = Mac(tmp_path, tools=())
    mac.release()
    r = mac.run(INSTALL, "--dry-run", stdin="y\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert mac.files_under_home() == []
    assert not any(c.startswith(("uv ", "brew ", "codesign")) for c in mac.calls())
    downloads = [c for c in mac.calls() if c.startswith("curl") and not c.endswith("/latest")]
    assert downloads == []


def test_homebrew_route(tmp_path: Path) -> None:
    mac = Mac(tmp_path, tools=())
    (mac.fakes / "brew").unlink()
    r = mac.run(INSTALL, "--homebrew")
    assert r.returncode == 1 and "--homebrew needs Homebrew" in r.stderr

    mac = Mac(tmp_path / "again", tools=("claude",))
    mac.release()
    r = mac.run(INSTALL, "--homebrew", stdin="n\n")
    calls = mac.calls()
    assert "brew install uv" in calls and "brew install gh" in calls
    # the fake brew installs nothing, so uv is still missing when ecf is installed
    assert r.returncode != 0


@pytest.mark.parametrize("args", [["--python", "3.14"], ["--bogus"]])
def test_bad_options_are_refused(tmp_path: Path, args: list[str]) -> None:
    r = Mac(tmp_path).run(INSTALL, *args)
    assert r.returncode == 2


def test_python_313_is_named_as_linux_tested(tmp_path: Path) -> None:
    mac = Mac(tmp_path)
    mac.release()
    r = mac.run(INSTALL, "--python", "3.13", stdin="n\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "tests run on it on Linux, not on macOS" in r.stdout
    assert any(c.startswith("uv tool install --python 3.13 ") for c in mac.calls())
