"""Fake macOS commands and a fake GitHub for testing the install and uninstall scripts
(scripts/install-ecf-macos.sh, scripts/uninstall-ecf-macos.sh; SPEC §17.4).

Each script runs under /bin/sh with a temporary HOME and PATH = fakes, /usr/bin, /bin. The fakes
log every call to `$LOG`; the fake curl serves files from `$FIX` by URL, so nothing touches the
network or this Mac's real tools.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import stat
import subprocess
import tarfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALL = REPO_ROOT / "scripts" / "install-ecf-macos.sh"
UNINSTALL = REPO_ROOT / "scripts" / "uninstall-ecf-macos.sh"
TEAM = "Q6L2SF6YDW"
COMMIT = "0123456789abcdef0123456789abcdef01234567"

CURL = r"""#!/bin/sh
out="" fmt="" url=""
while [ $# -gt 0 ]; do
    case "$1" in
        -o|-*o) out=$2; shift 2 ;;
        -w) fmt=$2; shift 2 ;;
        -*) shift ;;
        *) url=$1; shift ;;
    esac
done
echo "curl $url" >>"$LOG"
case "$url" in
    https://github.com/*/releases/latest)
        repo=${url#https://github.com/}; repo=${repo%/releases/latest}
        f="$FIX/latest/$(echo "$repo" | tr / _)"
        [ -f "$f" ] || exit 22
        printf 'https://github.com/%s/releases/tag/%s' "$repo" "$(cat "$f")"
        exit 0 ;;
esac
f="$FIX/files/${url#https://}"
[ -f "$f" ] || exit 22
if [ -n "$out" ]; then cp "$f" "$out"; else cat "$f"; fi
"""

UV = r"""#!/bin/sh
echo "uv $*" >>"$LOG"
case "$1" in
    --version) echo "uv 0.13.1" ;;
    tool)
        case "$2" in
            list) [ -f "$HOME/.fake-uv-tools" ] && cat "$HOME/.fake-uv-tools"; exit 0 ;;
            install)
                mkdir -p "$HOME/.local/bin"
                {
                    echo '#!/bin/sh'
                    echo 'echo "ecf $*" >>"$LOG"'
                    echo '[ "$1" = --version ] && echo "ecf 3.0.4"'
                    echo 'exit 0'
                } >"$HOME/.local/bin/ecf"
                chmod +x "$HOME/.local/bin/ecf"
                echo "email-classify-filter v3.0.4" >"$HOME/.fake-uv-tools" ;;
            uninstall) rm -f "$HOME/.fake-uv-tools" "$HOME/.local/bin/ecf" ;;
        esac ;;
esac
exit 0
"""

GH = r"""#!/bin/sh
echo "gh $*" >>"$LOG"
case "$1" in
    --version) echo "gh version 2.102.0 (2026-09-29)" ;;
    auth) exit 1 ;;
esac
exit 0
"""

CLAUDE = r"""#!/bin/sh
echo "claude $*" >>"$LOG"
[ "$1" = --version ] && echo "${CLAUDE_VERSION:-2.1.301} (Claude Code)"
exit 0
"""

# ecf as the uninstaller drives it: `ecf --install NAME destroy [--config-token]` deletes the data
ECF = r"""#!/bin/sh
echo "ecf $*" >>"$LOG"
if [ "$1" = --install ] && [ "$3" = destroy ]; then
    rm -rf "$HOME/Library/Application Support/ecf/$2"
fi
[ "$1" = --version ] && echo "ecf 3.0.4"
exit 0
"""

MAC = {
    "uname": '#!/bin/sh\ncase "$1" in -m) echo "${ARCH:-arm64}" ;; *) echo Darwin ;; esac\n',
    "sw_vers": "#!/bin/sh\necho 27.0\n",
    "fdesetup": '#!/bin/sh\necho "FileVault is ${FILEVAULT:-On}."\n',
    "sysadminctl": '#!/bin/sh\necho "sysadminctl[1] screenLock delay is immediate" >&2\n',
    "codesign": (
        '#!/bin/sh\necho "codesign $*" >>"$LOG"\n'
        'case "$1" in --verify) exit "${CODESIGN_EXIT:-0}" ;; esac\n'
        'echo "TeamIdentifier=${CODESIGN_TEAM:-' + TEAM + '}" >&2\n'
    ),
    "brew": '#!/bin/sh\necho "brew $*" >>"$LOG"\nexit 0\n',
}


def _load_release_build() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "release_build", REPO_ROOT / "scripts" / "release_build.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rb = _load_release_build()


def _exe(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)


def _tar(members: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for name, text in members.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), 0o755
            t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _zip(members: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in members.items():
            info = zipfile.ZipInfo(name)
            info.external_attr = (stat.S_IFREG | 0o755) << 16
            z.writestr(info, text)
    return buf.getvalue()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class Mac:
    """A temporary HOME, fake commands and a fake GitHub; `run` runs a script against them."""

    tmp: Path
    tools: tuple[str, ...] = ("uv", "gh", "claude")  # already installed before the script runs
    env: dict[str, str] = field(default_factory=dict[str, str])

    def __post_init__(self) -> None:
        self.home = self.tmp / "home"
        self.fakes = self.tmp / "fakes"
        self.fix = self.tmp / "fix"
        self.log = self.tmp / "log"
        self.home.mkdir(parents=True)
        self.log.write_text("")
        for name, text in MAC.items():
            _exe(self.fakes / name, text)
        _exe(self.fakes / "curl", CURL)
        for name, text in (("uv", UV), ("gh", GH), ("claude", CLAUDE)):
            if name in self.tools:
                _exe(self.fakes / name, text)
        self.publish_tools()

    @property
    def root(self) -> Path:
        return self.home / "Library" / "Application Support" / "ecf"

    @property
    def bin(self) -> Path:
        return self.home / ".local" / "bin"

    def file(self, url: str, data: bytes) -> None:
        p = self.fix / "files" / url.removeprefix("https://")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def latest(self, repo: str, tag: str) -> None:
        p = self.fix / "latest" / repo.replace("/", "_")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(tag)

    def publish_tools(self) -> None:
        """uv's and gh's release files, and Claude Code's installer, as their vendors publish."""
        self.latest("astral-sh/uv", "0.13.1")
        uv = _tar({"uv-aarch64-apple-darwin/uv": UV, "uv-aarch64-apple-darwin/uvx": UV})
        base = "https://github.com/astral-sh/uv/releases/download/0.13.1"
        self.file(f"{base}/uv-aarch64-apple-darwin.tar.gz", uv)
        self.file(f"{base}/uv-aarch64-apple-darwin.tar.gz.sha256",
                  f"{sha(uv)}  uv-aarch64-apple-darwin.tar.gz\n".encode())  # fmt: skip
        self.latest("cli/cli", "v2.102.0")
        gh = _zip({"gh_2.102.0_macOS_arm64/bin/gh": GH, "gh_2.102.0_macOS_arm64/LICENSE": "MIT"})
        base = "https://github.com/cli/cli/releases/download/v2.102.0"
        self.file(f"{base}/gh_2.102.0_macOS_arm64.zip", gh)
        self.file(f"{base}/gh_2.102.0_checksums.txt",
                  f"{'0' * 64}  gh_2.102.0_macOS_amd64.zip\n"
                  f"{sha(gh)}  gh_2.102.0_macOS_arm64.zip\n".encode())  # fmt: skip
        installer = (
            '#!/bin/bash\nmkdir -p "$HOME/.local/share/claude/versions" "$HOME/.local/bin"\n'
            f"cat >\"$HOME/.local/share/claude/versions/2.1.301\" <<'EOF'\n{CLAUDE}EOF\n"
            'chmod +x "$HOME/.local/share/claude/versions/2.1.301"\n'
            'ln -sf "$HOME/.local/share/claude/versions/2.1.301" "$HOME/.local/bin/claude"\n'
        )
        self.file("https://claude.ai/install.sh", installer.encode())

    def release(
        self,
        tag: str = "v3.0.4",
        *,
        wheel: bytes = b"wheel",
        sums_wheel: str | None = None,
        manifest_tag: str | None = None,
        drop_from_manifest: str | None = None,
    ) -> str:
        """An ecf release as scripts/release_build.py writes it; returns the wheel's name."""
        version = rb.tag_to_version(tag)
        whl = f"email_classify_filter-{version}-py3-none-any.whl"
        files = {whl: wheel, f"email_classify_filter-{version}.tar.gz": b"sdist",
                 "install-ecf-macos.sh": INSTALL.read_bytes(),
                 "uninstall-ecf-macos.sh": UNINSTALL.read_bytes()}  # fmt: skip
        hashes = {n: sha(b) for n, b in files.items()}
        sums = dict(hashes)
        if sums_wheel is not None:
            sums[whl] = sums_wheel
        listed = {n: h for n, h in hashes.items() if n != drop_from_manifest}
        base = f"https://github.com/rmarable/email-classify-filter/releases/download/{tag}"
        for n, b in files.items():
            self.file(f"{base}/{n}", b)
        self.file(f"{base}/SHA256SUMS", rb.sums_text(sums).encode())
        manifest = rb.manifest_text(version, manifest_tag or tag, COMMIT, listed)
        self.file(f"{base}/release-manifest.json", manifest.encode())
        self.latest("rmarable/email-classify-filter", tag)
        return whl

    def install(self, name: str) -> None:
        """An ecf install's data folder, as `ecf init` leaves it."""
        d = self.root / name
        d.mkdir(parents=True)
        (d / "ecf.db").write_bytes(b"")

    def run(self, script: Path, *args: str, stdin: str = "") -> subprocess.CompletedProcess[str]:
        env = {"HOME": str(self.home), "PATH": f"{self.fakes}:/usr/bin:/bin", "LOG": str(self.log),
               "FIX": str(self.fix), "TMPDIR": str(self.tmp), "LANG": "C"} | self.env  # fmt: skip
        return subprocess.run(["/bin/sh", str(script), *args], input=stdin, env=env,
                              capture_output=True, text=True, timeout=60, check=False)  # fmt: skip

    def calls(self) -> list[str]:
        return self.log.read_text().splitlines()

    def files_under_home(self) -> list[str]:
        return sorted(str(p.relative_to(self.home)) for p in self.home.rglob("*") if p.is_file())


def leftover_temp(tmp: Path) -> list[str]:
    return [p.name for p in tmp.iterdir() if p.name.startswith("install-ecf.")]
