"""`ecf upgrade`'s release source (operator decision D7, 2026-10-06): GitHub Releases through a
fake `gh`, the manifest and SHA256SUMS checks, release selection, and the CLI's `--to` both ways."""

from __future__ import annotations

import hashlib
import io
import json
import re
import sqlite3
import subprocess
import zipfile
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import ecf.cli_upgrade
from ecf import __version__, release_source, upgrade_check
from ecf.cli import app
from ecf.errors import InvalidInputError
from ecf.paths import Paths
from ecf.release_source import Gh, ReleaseError, ReleaseInfo, VerifyError
from ecf_server import db
from ecf_server.db import write_tx
from tests.test_addresses import make_state
from tests.test_export_keys import ApiClient

SCHEMA = max(v for v, _n, _s in db._migration_files())  # pyright: ignore[reportPrivateUsage]
LOCK = {"main_session": "claude-haiku-4-5-20251001", "classifier": "claude-haiku-4-5-20251001",
        "classifier_high": "claude-sonnet-5-5", "actor": "claude-sonnet-5-5",
        "actor_high": "claude-opus-5-5"}  # fmt: skip
COMMIT = "a" * 40


def wheel_bytes(version: str) -> bytes:
    info = {"product": "email-classify-filter", "version": version, "api_version": 1,
            "data_format": 2, "schema_version": SCHEMA, "min_client": "0.1.0.dev0"}  # fmt: skip
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("ecf_server/data/release.json", json.dumps(info))
        z.writestr("ecf_server/data/models.lock", json.dumps(LOCK))
        z.writestr("ecf_server/data/ollama.lock", json.dumps({"digest": "d" * 64}))
    return buf.getvalue()


def assets(tag: str, *, wheel_version: str | None = None) -> dict[str, bytes]:
    """A release's assets as the release workflow publishes them (the R3 contract)."""
    version = release_source.tag_version(tag)
    whl = release_source.wheel_name(version)
    files = {whl: wheel_bytes(wheel_version or version),
             f"email_classify_filter-{version}.tar.gz": b"sdist"}  # fmt: skip
    sums = {n: hashlib.sha256(b).hexdigest() for n, b in files.items()}
    files["SHA256SUMS"] = "".join(f"{h}  {n}\n" for n, h in sorted(sums.items())).encode()
    files["release-manifest.json"] = json.dumps(
        {"version": version, "tag": tag, "commit": COMMIT, "files": sums}
    ).encode()
    return files


class FakeGh:
    """`gh release list|view|download` over an in-memory set of releases."""

    def __init__(self, releases: dict[str, dict[str, Any]]) -> None:
        self.releases = releases  # tag -> {"prerelease": bool, "draft": bool, "assets": {...}}
        self.calls: list[list[str]] = []
        self.exit: int | None = None

    def gh(self) -> Gh:
        return Gh(repo="test/repo", run=self.run, which=lambda: "/usr/bin/gh")

    def run(self, args: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
        del timeout
        self.calls.append(args)
        assert args[0] == "/usr/bin/gh" and args[-2:] == ["--repo", "test/repo"]
        if self.exit is not None:
            return subprocess.CompletedProcess(args, self.exit, "", "error: authentication")
        cmd = args[1:-2]
        if cmd[:2] == ["release", "list"]:
            rows = [{"tagName": t, "isPrerelease": r.get("prerelease", False),
                     "isDraft": r.get("draft", False)}
                    for t, r in self.releases.items()]  # fmt: skip
            return subprocess.CompletedProcess(args, 0, json.dumps(rows), "")
        tag = cmd[2]
        rel = self.releases.get(tag)
        if rel is None:
            return subprocess.CompletedProcess(args, 1, "", "release not found")
        if cmd[1] == "view":
            body = {"tagName": tag, "isDraft": rel.get("draft", False),
                    "assets": [{"name": n} for n in rel["assets"]]}  # fmt: skip
            return subprocess.CompletedProcess(args, 0, json.dumps(body), "")
        out = Path(cmd[cmd.index("--dir") + 1])
        pats = [cmd[i + 1] for i, a in enumerate(cmd) if a == "--pattern"]
        for p in pats:
            (out / p).write_bytes(rel["assets"][p])
        return subprocess.CompletedProcess(args, 0, "", "")


def _rel(tag: str, *, prerelease: bool = False, **kw: Any) -> dict[str, Any]:
    return {"prerelease": prerelease, "assets": assets(tag, **kw)}


# ---- tags, listing and selection --------------------------------------------------------------


def test_tags_and_versions() -> None:
    assert release_source.tag_version("v1.0.0") == "1.0.0"
    assert release_source.tag_version("v1.0.0-rc2") == "1.0.0rc2"
    assert release_source.version_tag("1.0.0rc2") == "v1.0.0-rc2"
    assert release_source.version_tag("0.1.0.dev0") is None
    for bad in ("1.0.0", "v1.0", "v1.0.0rc1", "v1.0.0-beta1", "v1.0.0; rm -rf /"):
        with pytest.raises(InvalidInputError):
            release_source.tag_version(bad)


def test_listing_and_prerelease_selection() -> None:
    fake = FakeGh({"v1.0.0": _rel("v1.0.0"), "v1.1.0-rc1": _rel("v1.1.0-rc1", prerelease=True),
                   "v1.0.1": {"prerelease": True, "assets": {}},  # flagged on GitHub
                   "v2.0.0": {"draft": True, "assets": {}}, "nightly": {"assets": {}}})  # fmt: skip
    found = release_source.list_releases(fake.gh())
    assert [r.tag for r in found] == ["v1.0.0", "v1.0.1", "v1.1.0-rc1"]
    assert "--exclude-drafts" in fake.calls[0]
    assert release_source.newest(found, "0.1.0.dev0") == ReleaseInfo("v1.0.0", False)
    assert release_source.newest(found, "1.0.0") is None  # rc and flagged prereleases: by name
    assert release_source.newest([ReleaseInfo("v1.1.0-rc1", False)], "1.0.0") is None


def test_gh_missing_or_signed_out() -> None:
    with pytest.raises(ReleaseError, match="isn't installed"):
        release_source.list_releases(Gh(which=lambda: None))
    fake = FakeGh({})
    fake.exit = 4
    with pytest.raises(ReleaseError, match="gh auth login"):
        release_source.list_releases(fake.gh())
    fake.exit = 1
    with pytest.raises(ReleaseError, match="exit 1"):
        release_source.list_releases(fake.gh())


# ---- fetch and verify -------------------------------------------------------------------------


def test_fetch_verifies_and_keeps_the_wheel(tmp_path: Path) -> None:
    fake = FakeGh({"v1.0.0": _rel("v1.0.0")})
    w = release_source.fetch(fake.gh(), "v1.0.0", tmp_path / "releases" / "v1.0.0")
    assert w == tmp_path / "releases" / "v1.0.0" / "email_classify_filter-1.0.0-py3-none-any.whl"
    assert upgrade_check.read_wheel(w).version == "1.0.0"
    assert oct(w.parent.stat().st_mode & 0o777) == "0o700"
    assert [p.name for p in (tmp_path / "releases").iterdir()] == ["v1.0.0"]  # no temp left


def _fails(tmp_path: Path, rel: dict[str, Any], tag: str, err: type[Exception], why: str) -> None:
    dest = tmp_path / "releases" / tag
    with pytest.raises(err, match=why):
        release_source.fetch(FakeGh({tag: rel}).gh(), tag, dest)
    assert not dest.exists() or not any(dest.iterdir()), "nothing unverified is kept"
    assert not dest.parent.exists() or [p.name for p in dest.parent.iterdir()] == [tag]


def test_a_tampered_wheel_is_refused(tmp_path: Path) -> None:
    rel = _rel("v1.0.0")
    rel["assets"]["email_classify_filter-1.0.0-py3-none-any.whl"] += b"x"
    _fails(tmp_path, rel, "v1.0.0", VerifyError, "doesn't match its published sha256")


def test_manifest_and_sums_must_agree(tmp_path: Path) -> None:
    rel = _rel("v1.0.0")
    a = rel["assets"]
    a["SHA256SUMS"] = re.sub(rb"^[0-9a-f]{64}", b"0" * 64, a["SHA256SUMS"], count=1)
    _fails(tmp_path, rel, "v1.0.0", VerifyError, "don't agree")
    rel = _rel("v1.0.0")
    rel["assets"]["SHA256SUMS"] = b"not a sums file\n"
    _fails(tmp_path, rel, "v1.0.0", VerifyError, "line 1")


def test_a_manifest_for_another_tag_or_version_is_refused(tmp_path: Path) -> None:
    rel = _rel("v1.0.0")
    other = _rel("v1.0.1")["assets"]["release-manifest.json"]
    rel["assets"]["release-manifest.json"] = other
    _fails(tmp_path, rel, "v1.0.0", VerifyError, "is for 'v1.0.1', not v1.0.0")
    rel = _rel("v1.0.0")
    m = json.loads(rel["assets"]["release-manifest.json"])
    rel["assets"]["release-manifest.json"] = json.dumps(m | {"version": "1.0.1"}).encode()
    _fails(tmp_path, rel, "v1.0.0", VerifyError, "names version '1.0.1'")
    rel["assets"]["release-manifest.json"] = json.dumps(m | {"commit": "abc"}).encode()
    _fails(tmp_path, rel, "v1.0.0", VerifyError, "40-hex commit")


def test_a_missing_asset_or_release_is_refused(tmp_path: Path) -> None:
    rel = _rel("v1.0.0")
    del rel["assets"]["SHA256SUMS"]
    _fails(tmp_path, rel, "v1.0.0", ReleaseError, "has no SHA256SUMS")
    with pytest.raises(ReleaseError, match="release not found"):
        release_source.fetch(FakeGh({}).gh(), "v9.0.0", tmp_path / "x")


def test_a_local_sums_file(tmp_path: Path) -> None:
    rel = assets("v1.0.0")
    whl = tmp_path / "email_classify_filter-1.0.0-py3-none-any.whl"
    sums = tmp_path / "SHA256SUMS"
    whl.write_bytes(rel[whl.name])
    sums.write_bytes(rel["SHA256SUMS"])
    release_source.check_sums_file(whl, sums)
    whl.write_bytes(rel[whl.name] + b"x")
    with pytest.raises(VerifyError, match="doesn't match"):
        release_source.check_sums_file(whl, sums)
    other = tmp_path / "other.whl"
    other.write_bytes(b"")
    with pytest.raises(VerifyError, match=r"doesn't list other\.whl"):
        release_source.check_sums_file(other, sums)


# ---- the CLI ----------------------------------------------------------------------------------


@pytest.fixture
def cli(db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeGh:
    st = make_state(db_path, None)
    monkeypatch.setenv("ECF_HOME", str(tmp_path / "home"))

    def client(_paths: Paths) -> ApiClient:
        return ApiClient(st)

    monkeypatch.setattr(ecf.cli_upgrade, "LocalClient", client)
    env = tmp_path / "env"
    env.mkdir()
    installed = tmp_path / "installed.whl"
    installed.write_bytes(wheel_bytes(__version__))
    (env / "uv-receipt.toml").write_text(
        f'[tool]\nrequirements = [{{ name = "email-classify-filter", path = "{installed}" }}]\n'
    )
    real = upgrade_check.this_install

    def this_install(prefix: Path | None = None) -> upgrade_check.Install:
        del prefix
        return real(env)

    monkeypatch.setattr(upgrade_check, "this_install", this_install)
    fake = FakeGh({"v1.0.0": _rel("v1.0.0"), "v1.1.0-rc1": _rel("v1.1.0-rc1", prerelease=True)})
    monkeypatch.setattr(ecf.cli_upgrade, "_gh", fake.gh)
    return fake


def _upgrade(*args: str, input: str | None = None) -> Any:
    return CliRunner().invoke(app, ["--install", "t", "upgrade", *args], input=input)


def test_cli_newest_stable_release(cli: FakeGh, conn: sqlite3.Connection, tmp_path: Path) -> None:
    del conn
    stale = tmp_path / "home" / "t" / "releases" / "v0.9.0"
    stale.mkdir(parents=True)
    r = _upgrade("--check")
    assert r.exit_code == 0, r.output
    assert "downloading v1.0.0 from test/repo" in r.output and "sha256 matches" in r.output
    assert f"ecf {__version__} → 1.0.0" in r.output and "checks passed" in r.output
    assert not stale.exists()  # earlier downloads go; the installed one stays
    r = _upgrade()
    assert r.exit_code == 1 and "Upgrade to 1.0.0?" in r.output  # asks before stopping anything
    cli.releases.pop("v1.0.0")
    r = _upgrade("--check")
    assert r.exit_code == 0 and "is the newest release" in r.output  # an rc only by name


def test_cli_to_a_release_candidate_and_prod(cli: FakeGh, conn: sqlite3.Connection,
                                             db_path: Path, tmp_path: Path) -> None:  # fmt: skip
    del cli, conn
    r = _upgrade("--to", "v1.1.0-rc1", "--check")
    assert r.exit_code == 0, r.output
    assert "→ 1.1.0rc1" in r.output
    c = db.connect(db_path)
    with write_tx(c):
        c.execute("INSERT INTO settings (key, value, updated_at, updated_by) VALUES"
                  " ('install_role', '\"prod\"', 't', 't')")  # fmt: skip
    c.close()
    r = _upgrade("--to", "v1.0.0", "--check")
    assert r.exit_code == 0, r.output  # a prod install takes a verified release
    kept = Paths("t", tmp_path / "home").data_dir / "releases"
    assert sorted(p.name for p in kept.iterdir()) == ["v1.0.0"]
    whl = kept / "v1.0.0" / "email_classify_filter-1.0.0-py3-none-any.whl"
    r = _upgrade("--wheel", str(whl), "--check")
    assert r.exit_code == 1 and "--wheel is for test installs" in r.output  # even a verified one


def test_cli_refusals(cli: FakeGh, conn: sqlite3.Connection, tmp_path: Path) -> None:
    del conn
    rel = cli.releases["v1.0.0"]
    rel["assets"]["email_classify_filter-1.0.0-py3-none-any.whl"] += b"x"
    r = _upgrade("--to", "v1.0.0", "--check")
    assert isinstance(r.exception, VerifyError)
    cli.releases["v1.0.0"] = _rel("v1.0.0", wheel_version="1.0.1")  # consistent, wrong inside
    r = _upgrade("--to", "v1.0.0", "--check")
    assert r.exit_code == 1 and "release.json says 1.0.1, not 1.0.0" in r.output
    cli.exit = 4
    assert isinstance(_upgrade("--check").exception, ReleaseError)
    r = _upgrade("--to", "1.0.0")
    assert isinstance(r.exception, InvalidInputError) and "(v1.0.0)" in str(r.exception)
    r = _upgrade("--to", __version__)
    assert isinstance(r.exception, InvalidInputError) and "already installed" in str(r.exception)
    assert isinstance(_upgrade("--sha256sums", "x").exception, InvalidInputError)
    assert isinstance(_upgrade("--wheel", "x.whl", "--to", "v1.0.0").exception, InvalidInputError)


def test_cli_to_an_older_version_goes_back(cli: FakeGh, conn: sqlite3.Connection,
                                           tmp_path: Path) -> None:  # fmt: skip
    del conn
    for older in ("0.0.9", "v0.0.9"):
        r = _upgrade("--to", older)
        assert r.exit_code == 1 and "no copy from 0.0.9" in r.output, r.output
    assert cli.calls == []  # going back never downloads
    snap = Paths("t", tmp_path / "home").data_dir / "upgrades" / f"0.0.9-to-{__version__}"
    snap.mkdir(parents=True)
    (snap / "ecf.db").write_bytes(b"")
    (snap / "old.whl").write_bytes(b"")
    r = _upgrade("--to", "v0.0.9", input="n\n")
    assert r.exit_code == 1 and "Going back to 0.0.9" in r.output


def test_cli_wheel_with_sums(cli: FakeGh, conn: sqlite3.Connection, tmp_path: Path) -> None:
    del conn
    rel = assets("v1.0.0")
    whl = tmp_path / "email_classify_filter-1.0.0-py3-none-any.whl"
    sums = tmp_path / "SHA256SUMS"
    whl.write_bytes(rel[whl.name])
    sums.write_bytes(rel["SHA256SUMS"])
    r = _upgrade("--wheel", str(whl), "--sha256sums", str(sums), "--check")
    assert r.exit_code == 0 and "matches SHA256SUMS" in r.output, r.output
    whl.write_bytes(rel[whl.name] + b"x")
    r = _upgrade("--wheel", str(whl), "--sha256sums", str(sums), "--check")
    assert isinstance(r.exception, VerifyError)
    assert cli.calls == []
