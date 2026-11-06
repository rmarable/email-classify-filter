"""The sdist holds only what the allow-list in pyproject.toml names (v1.0.0 release work): before
it, untracked agent worktrees and test caches were packaged (V1.6 closing run, 2026-10-06)."""

from __future__ import annotations

import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALLOWED = {
    "src", "tests", "scripts", "README.md", "LICENSE", "CHANGELOG.md", "SPEC.md", "SECURITY.md",
    "CONTRIBUTING.md", "DISCLAIMER.md", "GENERATE-FAKE-TESTING-EMAILS.md", "pyproject.toml",
    "uv.lock",
    "build-constraints.txt",
    ".python-version", "THIRD_PARTY_NOTICES", "PKG-INFO", ".gitignore",  # hatch adds .gitignore
}  # fmt: skip


def test_the_sdist_holds_only_allowed_paths(tmp_path: Path) -> None:
    subprocess.run(["uv", "build", "--sdist", "--out-dir", str(tmp_path)], cwd=ROOT, check=True,
                   capture_output=True)  # fmt: skip
    [sdist] = tmp_path.glob("*.tar.gz")
    with tarfile.open(sdist) as tar:
        names = [n.split("/", 1)[1] for n in tar.getnames() if "/" in n]
    tops = {n.split("/", 1)[0] for n in names}
    assert tops <= ALLOWED, sorted(tops - ALLOWED)
    assert not [
        n
        for n in names
        if "/." in f"/{n}"
        and n not in (".python-version", ".gitignore")
        and "/.claude-plugin/" not in n
    ]  # the plugin manifest ships
    assert not [n for n in names if "__pycache__" in n]  # hidden paths (.build, caches): above
    # the private set never ships (EVAL-PRIVATE.md, "Releases and CI"): `eval/` is outside the
    # allow-list above, and no card or email anywhere in the sdist has a private card's name
    # (a copy into tests/eval would; round 3, L9: the old `eval/` check couldn't fail). Without
    # a private clone (CI) there are no names to match. The check that no card or email in the
    # sdist is an injection card (check_card_injection.check_files, nothing skipped) is turned
    # on by the move commit, once the public sets hold none.
    assert "eval" not in ALLOWED
    private = {p.stem for p in (ROOT / "eval" / "private").glob("*/cases/*.md")}
    leaked = [n for n in names if n.endswith((".md", ".eml"))
              and n.rsplit("/", 1)[-1].rsplit(".", 1)[0] in private]  # fmt: skip
    assert not leaked, leaked
