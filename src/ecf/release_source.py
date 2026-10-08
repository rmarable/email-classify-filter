"""Where `ecf upgrade` gets releases: GitHub Releases on ecf's repository (SPEC §11.10, §17.4;
operator decision D7, 2026-10-06: no PyPI). Client code; the service's weekly watch uses the
listing too (SPEC §7.6).

The repository is private, so everything goes through the GitHub CLI (`gh`) and the operator's
own `gh auth login`: ecf adds no dependency and never sees a token. A release is a tag `vX.Y.Z`
or `vX.Y.Z-rcN` with these assets (the contract with the release workflow):

- `email_classify_filter-<version>-py3-none-any.whl` and `email_classify_filter-<version>.tar.gz`
- `SHA256SUMS`: `<sha256 hex>  <filename>` lines, sorted
- `release-manifest.json`: `{"version", "tag", "commit", "files": {filename: sha256 hex}}`

`fetch` downloads the manifest, `SHA256SUMS` and the wheel, and accepts the wheel only when the
manifest names the tag and version asked for, the manifest and `SHA256SUMS` list the same files
with the same hashes, and the wheel hashes to that value. Both lists come from the same release,
so this catches a damaged or swapped asset, not a forged release: whoever can publish to the
repository can publish a consistent set (the repository's access control is the trust root).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from ecf.errors import InvalidInputError, ServiceUnavailableError
from ecf.upgrade_check import version_key

REPO = "rmarable/email-classify-filter"
MANIFEST, SUMS = "release-manifest.json", "SHA256SUMS"
DIST = "email_classify_filter"
LIST_LIMIT = 100
LIST_TIMEOUT_S = 30.0
DOWNLOAD_TIMEOUT_S = 600.0
_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)(?:-rc(\d+))?$")
_RC_VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:rc(\d+))?$")
_SUM_LINE = re.compile(r"^([0-9a-f]{64})  (\S+)$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
# launchd and systemd start the service with a short PATH; Homebrew's gh lives here
GH_FALLBACKS = ("/opt/homebrew/bin/gh", "/usr/local/bin/gh", "/usr/bin/gh")


class ReleaseError(ServiceUnavailableError):
    """gh missing, not signed in or failing, or a release or asset that isn't there."""


class VerifyError(InvalidInputError):
    """A release whose files don't agree: a wrong tag or version, or a hash that differs."""


def tag_version(tag: str) -> str:
    """`v1.2.3` -> `1.2.3`, `v1.2.3-rc1` -> `1.2.3rc1` (PEP 440, as the wheel names it)."""
    m = _TAG.match(tag)
    if not m:
        raise InvalidInputError(f"not a release tag: {tag!r} (vX.Y.Z or vX.Y.Z-rcN)")
    base = f"{m[1]}.{m[2]}.{m[3]}"
    return f"{base}rc{m[4]}" if m[4] else base


def version_tag(version: str) -> str | None:
    """The release tag for an ecf version, or None for one that's never released (`.devN`)."""
    m = _RC_VERSION.match(version)
    if not m:
        return None
    base = f"v{m[1]}.{m[2]}.{m[3]}"
    return f"{base}-rc{m[4]}" if m[4] else base


def is_tag(text: str) -> bool:
    return _TAG.match(text) is not None


def wheel_name(version: str) -> str:
    return f"{DIST}-{version}-py3-none-any.whl"


@dataclass(frozen=True)
class ReleaseInfo:
    tag: str
    prerelease: bool  # GitHub's flag, or an -rc tag

    @property
    def version(self) -> str:
        return tag_version(self.tag)


Run = Callable[[list[str], float], "subprocess.CompletedProcess[str]"]


def _run(args: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    env = os.environ | {"GH_PROMPT_DISABLED": "1", "GH_NO_UPDATE_NOTIFIER": "1", "NO_COLOR": "1"}
    return subprocess.run(  # noqa: S603 - gh, with arguments ecf builds
        args, capture_output=True, text=True, timeout=timeout, env=env, check=False
    )


def _which() -> str | None:
    found = shutil.which("gh")
    if found:
        return found
    return next((p for p in GH_FALLBACKS if os.access(p, os.X_OK)), None)


@dataclass
class Gh:
    """The GitHub CLI; fakes in tests."""

    repo: str = REPO
    run: Run = _run
    which: Callable[[], str | None] = field(default=_which)

    def call(self, args: list[str], timeout: float = LIST_TIMEOUT_S) -> str:
        gh = self.which()
        if gh is None:
            raise ReleaseError("the GitHub CLI (gh) isn't installed or isn't on PATH; ecf"
                               " releases are on a private GitHub repository: install gh"
                               " (brew install gh), then gh auth login")  # fmt: skip
        try:
            r = self.run([gh, *args, "--repo", self.repo], timeout)
        except subprocess.TimeoutExpired:
            raise ReleaseError(f"gh didn't finish in {timeout:.0f} s") from None
        except OSError as e:
            raise ReleaseError(f"couldn't run gh ({type(e).__name__})") from None
        if r.returncode == 4:
            raise ReleaseError("gh isn't signed in to GitHub; run gh auth login (an account"
                               f" that can read {self.repo})")  # fmt: skip
        if r.returncode != 0:
            why = (r.stderr or "").strip().splitlines()
            raise ReleaseError(f"gh failed (exit {r.returncode})"
                               + (f": {why[-1][:200]}" if why else ""))  # fmt: skip
        return r.stdout


def list_releases(gh: Gh) -> list[ReleaseInfo]:
    """Published releases with a release tag (drafts and other tags are left out)."""
    out = gh.call(["release", "list", "--exclude-drafts", "--limit", str(LIST_LIMIT),
                   "--json", "tagName,isPrerelease,isDraft"])  # fmt: skip
    try:
        rows: Any = json.loads(out or "[]")
    except ValueError:
        raise ReleaseError("gh release list gave an answer ecf can't read") from None
    if not isinstance(rows, list):
        raise ReleaseError("gh release list gave an answer ecf can't read")
    found: list[ReleaseInfo] = []
    for row in cast("list[Any]", rows):
        if not isinstance(row, dict):
            continue
        r = cast("dict[str, Any]", row)
        tag = r.get("tagName")
        if r.get("isDraft") or not isinstance(tag, str) or not is_tag(tag):
            continue
        found.append(ReleaseInfo(tag, bool(r.get("isPrerelease")) or "-rc" in tag))
    return sorted(found, key=lambda x: version_key(x.version))


def newest(releases: list[ReleaseInfo], current: str) -> ReleaseInfo | None:
    """The newest stable release newer than `current`; release candidates only by name."""
    newer = [r for r in releases if not r.prerelease and "-rc" not in r.tag
             and version_key(r.version) > version_key(current)]  # fmt: skip
    return max(newer, key=lambda r: version_key(r.version)) if newer else None


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_sums(text: str) -> dict[str, str]:
    """`SHA256SUMS` (`sha256sum` format) -> filename: hex; refuses anything else."""
    out: dict[str, str] = {}
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        m = _SUM_LINE.match(line)
        if not m:
            raise VerifyError(f"{SUMS} line {n} isn't `<sha256>  <file>`")
        if m[2] in out:
            raise VerifyError(f"{SUMS} lists {m[2]} twice")
        out[m[2]] = m[1]
    if not out:
        raise VerifyError(f"{SUMS} is empty")
    return out


def check_manifest(raw: str, tag: str) -> dict[str, str]:
    """The manifest's files, after checking it names `tag` and its version."""
    try:
        m: Any = json.loads(raw)
    except ValueError:
        raise VerifyError(f"{MANIFEST} isn't JSON") from None
    if not isinstance(m, dict):
        raise VerifyError(f"{MANIFEST} isn't a JSON object")
    man = cast("dict[str, Any]", m)
    if man.get("tag") != tag:
        raise VerifyError(f"{MANIFEST} is for {man.get('tag')!r}, not {tag}")
    if man.get("version") != tag_version(tag):
        raise VerifyError(f"{MANIFEST} names version {man.get('version')!r}, not"
                          f" {tag_version(tag)}")  # fmt: skip
    if not isinstance(man.get("commit"), str) or not _COMMIT.match(man["commit"]):
        raise VerifyError(f"{MANIFEST} has no 40-hex commit")
    files: Any = man.get("files")
    if not isinstance(files, dict) or not files:
        raise VerifyError(f"{MANIFEST} lists no files")
    out: dict[str, str] = {}
    for k, v in cast("dict[Any, Any]", files).items():
        if not isinstance(k, str) or not isinstance(v, str) or not _HEX64.match(v):
            raise VerifyError(f"{MANIFEST} has a file entry that isn't name: sha256")
        out[k] = v
    return out


def check_sums_file(wheel: Path, sums: Path) -> None:
    """`ecf upgrade --wheel <file> --sha256sums <file>`: the wheel is listed and matches."""
    listed = parse_sums(sums.read_text())
    want = listed.get(wheel.name)
    if want is None:
        raise VerifyError(f"{sums.name} doesn't list {wheel.name}")
    if sha256(wheel) != want:
        raise VerifyError(f"{wheel.name} doesn't match its hash in {sums.name}")


def fetch(gh: Gh, tag: str, dest: Path) -> Path:
    """Download and verify `tag`'s wheel; returns it in `dest` (made 0700), where it stays: `uv
    tool` records the wheel it installed from, and `ecf upgrade` reinstalls it to roll back."""
    version = tag_version(tag)
    whl = wheel_name(version)
    view = gh.call(["release", "view", tag, "--json", "tagName,isDraft,assets"])
    try:
        info = cast("dict[str, Any]", json.loads(view))
        assets = {str(cast("dict[str, Any]", a).get("name"))
                  for a in cast("list[Any]", info.get("assets") or [])}  # fmt: skip
    except (ValueError, AttributeError, TypeError):
        raise ReleaseError(f"gh release view {tag} gave an answer ecf can't read") from None
    if info.get("tagName") != tag or info.get("isDraft"):
        raise ReleaseError(f"there's no published release {tag}")
    missing = [n for n in (MANIFEST, SUMS, whl) if n not in assets]
    if missing:
        raise ReleaseError(f"release {tag} has no {', '.join(missing)}; it can't be verified,"
                           " so ecf won't install it")  # fmt: skip
    dest.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = Path(tempfile.mkdtemp(prefix=".download-", dir=dest.parent))
    try:
        gh.call(["release", "download", tag, "--dir", str(tmp), "--pattern", MANIFEST,
                 "--pattern", SUMS, "--pattern", whl], DOWNLOAD_TIMEOUT_S)  # fmt: skip
        for n in (MANIFEST, SUMS, whl):
            if not (tmp / n).is_file():
                raise ReleaseError(f"gh didn't download {n} from {tag}")
        files = check_manifest((tmp / MANIFEST).read_text(), tag)
        sums = parse_sums((tmp / SUMS).read_text())
        if files != sums:
            raise VerifyError(f"{MANIFEST} and {SUMS} of {tag} don't agree")
        if whl not in files:
            raise VerifyError(f"{MANIFEST} of {tag} doesn't list {whl}")
        if sha256(tmp / whl) != files[whl]:
            raise VerifyError(f"{whl} doesn't match its published sha256; it may be damaged or"
                              " replaced, so ecf won't install it")  # fmt: skip
        dest.chmod(0o700)
        for n in (MANIFEST, SUMS, whl):
            os.replace(tmp / n, dest / n)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return dest / whl
