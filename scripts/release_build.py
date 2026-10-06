"""Build the release files reproducibly and write their hashes (SPEC §17.4, §1.5 item 6).

    uv run python scripts/release_build.py --out DIR [--tag vX.Y.Z[-rcN]]

Builds the sdist and the wheel (from the sdist) with `uv build`, SOURCE_DATE_EPOCH set to the
commit time, into DIR (which must be empty or absent), then writes:

- `SHA256SUMS`: `<sha256 hex>  <filename>`, one line per built file, sorted by filename;
- `release-manifest.json`: {"commit", "files": {filename: sha256}, "tag", "version"}, keys sorted.

With --tag, the tag must be `vX.Y.Z` or `vX.Y.Z-rcN`, its version (`X.Y.Z`, `X.Y.ZrcN`) must equal
the built package's, and the working tree must be clean. Without --tag (CI), the manifest's "tag"
is null; such a build is not a release. Building twice from one commit gives identical files;
CI checks that.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = "email_classify_filter"
SUMS = "SHA256SUMS"
MANIFEST = "release-manifest.json"
TAG_RE = re.compile(r"^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-rc([1-9]\d*))?$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


class ReleaseError(Exception):
    pass


def tag_to_version(tag: str) -> str:
    """`v1.0.0` -> `1.0.0`; `v1.0.0-rc1` -> `1.0.0rc1` (PEP 440). Anything else is refused."""
    m = TAG_RE.match(tag)
    if m is None:
        raise ReleaseError(f"tag {tag!r} is not vX.Y.Z or vX.Y.Z-rcN")
    major, minor, patch, rc = m.groups()
    return f"{major}.{minor}.{patch}" + (f"rc{rc}" if rc else "")


def expected_files(version: str) -> list[str]:
    return sorted([f"{DIST}-{version}-py3-none-any.whl", f"{DIST}-{version}.tar.gz"])


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sums_text(hashes: dict[str, str]) -> str:
    return "".join(f"{hashes[name]}  {name}\n" for name in sorted(hashes))


def manifest_text(version: str, tag: str | None, commit: str, hashes: dict[str, str]) -> str:
    if not COMMIT_RE.match(commit):
        raise ReleaseError(f"commit {commit!r} is not 40 lowercase hex")
    doc = {"commit": commit, "files": dict(sorted(hashes.items())), "tag": tag, "version": version}
    return json.dumps(doc, indent=2, sort_keys=True) + "\n"


def wheel_version(wheel: Path) -> str:
    """The wheel's METADATA version; its `release.json` must say the same (ADR 0008 lockstep)."""
    with zipfile.ZipFile(wheel) as z:
        meta_name = next(n for n in z.namelist() if n.endswith(".dist-info/METADATA"))
        meta = z.read(meta_name).decode()
        release = json.loads(z.read("ecf_server/data/release.json"))
    found = re.search(r"^Version: (\S+)$", meta, re.MULTILINE)
    if found is None:
        raise ReleaseError(f"{wheel.name}: no Version in METADATA")
    version = found.group(1)
    if release.get("version") != version:
        raise ReleaseError(
            f"{wheel.name}: release.json version {release.get('version')!r} != {version!r}"
        )
    return version


def check_built(out: Path, tag: str | None) -> tuple[str, dict[str, str]]:
    """The built version (equal to the tag's, if given) and each built file's hash."""
    wheels = sorted(out.glob("*.whl"))
    if len(wheels) != 1:
        raise ReleaseError(f"expected one wheel in {out}, found {[w.name for w in wheels]}")
    version = wheel_version(wheels[0])
    if tag is not None and tag_to_version(tag) != version:
        raise ReleaseError(f"tag {tag} is version {tag_to_version(tag)}; the package is {version}")
    # uv also writes a .gitignore into the folder; it is not a release file
    names = sorted(p.name for p in out.iterdir() if not p.name.startswith("."))
    if names != expected_files(version):
        raise ReleaseError(f"built files {names} != expected {expected_files(version)}")
    return version, {name: sha256_file(out / name) for name in names}


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True,
                          text=True).stdout.strip()  # fmt: skip


def build(out: Path, epoch: str) -> None:
    env = {**os.environ, "SOURCE_DATE_EPOCH": epoch}
    subprocess.run(["uv", "build", "--out-dir", str(out)], cwd=ROOT, env=env, check=True)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    p.add_argument("--out", type=Path, required=True, help="output folder (empty or absent)")
    p.add_argument("--tag", help="release tag vX.Y.Z or vX.Y.Z-rcN; checked against the version")
    a = p.parse_args(argv)
    out: Path = a.out.resolve()
    tag: str | None = a.tag
    try:
        if tag is not None:
            tag_to_version(tag)  # refuse a malformed tag before building
            if git("status", "--porcelain"):
                raise ReleaseError("the working tree is not clean")
        if out.exists() and any(out.iterdir()):
            raise ReleaseError(f"{out} is not empty")
        commit = git("rev-parse", "HEAD")
        epoch = git("log", "-1", "--format=%ct")
        build(out, epoch)
        version, hashes = check_built(out, tag)
        (out / SUMS).write_text(sums_text(hashes))
        (out / MANIFEST).write_text(manifest_text(version, tag, commit, hashes))
    except ReleaseError as e:
        print(f"release_build: {e}", file=sys.stderr)
        return 1
    print(f"version {version}, commit {commit}, SOURCE_DATE_EPOCH {epoch}")
    print(sums_text(hashes), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
