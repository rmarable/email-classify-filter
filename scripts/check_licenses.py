"""Check every runtime dependency's license, extras included, against the allow-list (SPEC §17.5).

The dependency list comes from `uv export --no-dev --all-extras`. Licenses come from installed
package metadata: License-Expression, then a short License field, then trove classifiers.
Unknown or disallowed licenses fail the check. Add a reviewed entry to OVERRIDES only after
checking the upstream license.

`--notices` writes THIRD_PARTY_NOTICES (SPEC §1.5 item 4, §17.4): the same dependency set, each
package's license and notice files read from its wheel for macOS and Linux (installed into a
temporary folder with `uv pip install --target --python-platform`, hashes from uv.lock), plus the
third-party data files ecf itself ships. A package whose wheels carry no license text fails unless
it has a reviewed NO_TEXT entry; the file then names it and its URL, and never invents a text.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOTICES = ROOT / "THIRD_PARTY_NOTICES"
# Wheels the notices are read from: the platforms ecf's markers install on (v1: macOS; Linux is M5).
NOTICE_PLATFORMS = (("macOS", "aarch64-apple-darwin"), ("Linux", "x86_64-unknown-linux-gnu"))
NOTICE_PYTHON = "3.12"
# Files at the top of a .dist-info folder that are license material (older metadata); everything
# under .dist-info/licenses/ counts (PEP 639).
LICENSE_FILE = re.compile(r"^(LICEN[CS]E|COPYING|NOTICE|AUTHORS)", re.IGNORECASE)
# Reviewed: packages whose wheels ship no license file, at the locked version. The notices file
# states the license from metadata and the URL below instead of a text.
NO_TEXT: dict[str, tuple[str, str]] = {  # name -> (locked version, note)
    # The macOS wheels (12.2.2, read 2026-10-06) have no license file; METADATA says License: MIT
    # and Home-page github.com/ronaldoussoren/pyobjc, the same as pyobjc-framework-cocoa and
    # -localauthentication, whose wheels carry the same LICENSE.txt (compared 2026-10-06).
    "pyobjc-core": ("12.2.2", _PYOBJC := (
        "https://github.com/ronaldoussoren/pyobjc (METADATA Home-page). The PyObjC project's\n"
        "license text, from the same repository, is in pyobjc-framework-cocoa's wheel, below."
    )),
    "pyobjc-framework-security": ("12.2.2", _PYOBJC),
}  # fmt: skip
# Third-party data files inside ecf's own wheel: (what, license file, provenance file).
BUNDLED = (
    (
        "Unicode UTS #39 confusables.txt (src/ecf_server/data/unicode/), Unicode License v3",
        "src/ecf_server/data/unicode/LICENSE-UNICODE.txt",
        "src/ecf_server/data/unicode/README.md",
    ),
    (
        "BIP 39 English wordlist (src/ecf_server/data/wordlist/), MIT",
        "src/ecf_server/data/wordlist/LICENSE-BIP39.txt",
        "src/ecf_server/data/wordlist/README.md",
    ),
)

ALLOWED = {
    "MIT",
    "MIT-0",
    "BSD",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "ISC",
    "Apache-2.0",
    "Zlib",
    "MIT-CMU",
    "HPND",
    "PSF-2.0",
}
# Named runtime exceptions (operator decision 2026-09-27, OD-128): used unmodified.
EXCEPTIONS = {"certifi": "MPL-2.0"}
# Reviewed entries for packages that aren't installed on the platform running the check
# (platform-specific dependencies), or whose installed metadata can't be parsed. Readable metadata
# always wins; an entry applies only at its reviewed version.
OVERRIDES: dict[str, tuple[str, str, str]] = {  # name -> (locked version, license, source)
    "colorama": ("0.4.6", "BSD", "PyPI classifiers, 2026-09-27 (Windows-only, via click)"),
    # Linux-only, via keyring (Secret Service); PyPI metadata at the locked versions, 2026-09-27
    "cffi": ("2.1.1", "MIT-0", "PyPI License-Expression"),
    "cryptography": ("50.0.1", "Apache-2.0 OR BSD-3-Clause", "PyPI License-Expression"),
    "jeepney": ("0.9.0", "MIT", "PyPI License-Expression"),
    "pycparser": ("3.0", "BSD-3-Clause", "PyPI License-Expression"),
    "secretstorage": ("3.5.0", "BSD-3-Clause", "PyPI License-Expression"),
    # Windows-only, via keyring
    "pywin32-ctypes": ("0.2.3", "BSD-3-Clause", "PyPI License field"),
    # macOS-only (Keychain interaction control); PyPI License field at 12.2.2, 2026-09-27
    "pyobjc-core": ("12.2.2", "MIT", "PyPI License field"),
    "pyobjc-framework-cocoa": ("12.2.2", "MIT", "PyPI License field"),
    "pyobjc-framework-security": ("12.2.2", "MIT", "PyPI License field"),
    # macOS-only (step-up through LocalAuthentication, V1.2); installed metadata's License
    # field on macOS, read 2026-09-29
    "pyobjc-framework-localauthentication": ("12.2.2", "MIT", "installed metadata License field"),
    # Linux-only (step-up through PAM, V1.2); PyPI metadata has no license; the wheel's own
    # license file is the MIT text (dist-info licenses/LICENSE, 2.1.0 wheel, read 2026-09-29)
    "python-pam": ("2.1.0", "MIT", "dist-info licenses/LICENSE, 2026-09-29"),
    # Metadata says "BSD-like" / "New BSD"; the license files shipped in the wheels (read
    # 2026-09-28) are the zlib text and the BSD-3-Clause text respectively.
    "dkimpy": ("1.1.8", "Zlib", "dist-info licenses/LICENSE, 2026-09-28"),
    "imapclient": ("4.1.0", "BSD-3-Clause", "dist-info licenses/COPYING, 2026-09-28"),
    # Metadata has no license field; the wheel's license file is the MIT text (V1.5, read
    # 2026-10-03; the repo's LICENSE is MIT too, checked 2026-10-02)
    "pyrage": ("1.4.0", "MIT", "dist-info licenses/LICENSE, 2026-10-03"),
    # Via mcp 2.2.0 (V1.4), never installed on macOS or Linux; PyPI metadata, 2026-10-02
    "httpx2-jsfetch": ("1.0", "BSD-3-Clause", "PyPI License-Expression (emscripten only)"),
    "pywin32": ("312", "PSF-2.0", "PyPI License 'PSF' and PSF classifier (Windows only)"),
}

CLASSIFIER_MAP = {
    "MIT License": "MIT",
    "BSD License": "BSD",
    "Apache Software License": "Apache-2.0",
    "ISC License (ISCL)": "ISC",
    "Python Software Foundation License": "PSF-2.0",
    "zlib/libpng License": "Zlib",
    "Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
}
TEXT_MAP = {
    "mit": "MIT",
    "mit license": "MIT",
    "bsd": "BSD",
    "bsd license": "BSD",
    "bsd-3-clause": "BSD-3-Clause",
    "bsd-2-clause": "BSD-2-Clause",
    "apache 2.0": "Apache-2.0",
    "apache-2.0": "Apache-2.0",
    "apache software license": "Apache-2.0",
    "isc": "ISC",
    "mpl-2.0": "MPL-2.0",
    "psf-2.0": "PSF-2.0",
    "zlib": "Zlib",
}


def runtime_packages(*, extras: bool = True) -> list[tuple[str, str, str]]:
    """(name, version, environment marker or "") for every runtime dependency."""
    cmd = [
        "uv",
        "export",
        "--color",
        "never",  # FORCE_COLOR in the environment would otherwise colour the list
        "--no-dev",
        "--no-hashes",
        "--no-emit-project",
        "--format",
        "requirements-txt",
    ]
    if extras:
        cmd.append("--all-extras")
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    pkgs: list[tuple[str, str, str]] = []
    for raw in out.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "-")):
            continue
        spec, _, marker = line.partition(";")
        name, _, version = spec.strip().partition("==")
        pkgs.append((name, version, marker.strip()))
    return sorted(pkgs, key=lambda p: p[0].lower())


def markdown_table() -> str:
    rows = ["| Package | Version | License | Installed on |", "|---|---|---|---|"]
    core = {n for n, _, _ in runtime_packages(extras=False)}
    for name, version, marker in runtime_packages():
        where = installed_on(name, marker, core).replace("[eval]", "`[eval]`")
        rows.append(f"| `{name}` | {version} | {license_of(name, version)} | {where} |")
    return "\n".join(rows)


def installed_on(name: str, marker: str, core: set[str]) -> str:
    where = "all platforms" if name in core else "[eval] extra only"
    for plat, label in (
        ("darwin", "macOS"),
        ("linux", "Linux"),
        ("win32", "Windows"),
        ("emscripten", "WebAssembly (emscripten)"),
    ):
        if f"sys_platform == '{plat}'" in marker:
            where = label
    return where


def license_of(name: str, version: str) -> str:
    """Installed metadata first; the reviewed override only for a package that isn't installed
    here (platform-specific) or whose metadata can't be read, and only at the reviewed version."""
    try:
        md = metadata.metadata(name)
    except metadata.PackageNotFoundError:
        md = None
    return license_from(name, version, md)


def license_from(name: str, version: str, md: metadata.PackageMetadata | None) -> str:
    override = OVERRIDES.get(name.lower())
    if md is None:
        return _override(override, version, "not installed here")
    found = _from_metadata(md)
    if found.startswith("UNKNOWN") and override is not None:
        return _override(override, version, "metadata unreadable")
    return found


def _override(override: tuple[str, str, str] | None, version: str, why: str) -> str:
    if override is None:
        return f"UNKNOWN ({why}; add a reviewed entry to OVERRIDES)"
    if override[0] != version:
        return f"UNKNOWN (reviewed at {override[0]}, locked at {version}: re-review)"
    return override[1]


def _from_metadata(md: metadata.PackageMetadata) -> str:
    if expr := md.get("License-Expression"):
        return expr.strip()
    text = (md.get("License") or "").strip()
    if text and len(text) < 60 and text.lower() in TEXT_MAP:
        return TEXT_MAP[text.lower()]
    found = [
        CLASSIFIER_MAP[c.split(" :: ")[-1]]
        for c in md.get_all("Classifier") or []
        if c.startswith("License ::") and c.split(" :: ")[-1] in CLASSIFIER_MAP
    ]
    return " OR ".join(sorted(set(found))) if found else f"UNKNOWN ({text[:40]!r})"


def allowed(name: str, lic: str) -> bool:
    if EXCEPTIONS.get(name.lower()) == lic:
        return True
    if " AND " in lic:
        return all(allowed(name, p.strip("() ")) for p in lic.split(" AND "))
    return any(p.strip("() ") in ALLOWED for p in lic.split(" OR "))


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def install_wheels(tmp: Path) -> dict[str, Path]:
    """Install every locked runtime package (extras included, no dependencies resolved, hashes
    required) into one --target folder per NOTICE_PLATFORMS entry; platform markers decide."""
    req = tmp / "requirements.txt"
    subprocess.run(
        ["uv", "export", "--no-dev", "--all-extras", "--no-emit-project", "--quiet",
         "--format", "requirements-txt", "-o", str(req)],
        check=True,
    )  # fmt: skip
    targets: dict[str, Path] = {}
    for label, triple in NOTICE_PLATFORMS:
        target = tmp / triple
        subprocess.run(
            ["uv", "pip", "install", "--quiet", "--no-deps", "--require-hashes", "--target",
             str(target), "--python-platform", triple, "--python-version", NOTICE_PYTHON,
             "-r", str(req)],
            check=True,
        )  # fmt: skip
        targets[label] = target
    return targets


def dist_infos(targets: dict[str, Path]) -> dict[str, list[tuple[str, Path]]]:
    """canonical name -> [(platform label, .dist-info folder)] across the target folders."""
    found: dict[str, list[tuple[str, Path]]] = {}
    for label, target in targets.items():
        for d in sorted(target.glob("*.dist-info")):
            name = metadata.PathDistribution(d).metadata["Name"]
            found.setdefault(canonical(name), []).append((label, d))
    return found


def license_files(dist_info: Path) -> list[tuple[str, str]]:
    """(path inside .dist-info, text) for each license or notice file, sorted by path. Text is
    verbatim but for line endings (LF) and trailing spaces; a file that isn't UTF-8 fails."""
    paths = [p for p in dist_info.iterdir() if p.is_file() and LICENSE_FILE.match(p.name)]
    lic = dist_info / "licenses"
    if lic.is_dir():
        paths += [p for p in lic.rglob("*") if p.is_file()]
    out: list[tuple[str, str]] = []
    for p in sorted(paths, key=lambda p: p.relative_to(dist_info).as_posix()):
        try:
            text = p.read_bytes().decode("utf-8-sig")
        except UnicodeDecodeError as e:
            raise SystemExit(f"{p}: not UTF-8 ({e}); review it by hand") from e
        out.append((p.relative_to(dist_info).as_posix(), _clean(text)))
    return out


def _clean(text: str) -> str:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(line.rstrip() for line in lines).strip("\n") + "\n"


def _urls(md: metadata.PackageMetadata) -> list[str]:
    urls = [md["Home-page"]] if md.get("Home-page") else []
    urls += [u.split(",", 1)[-1].strip() for u in md.get_all("Project-URL") or []]
    return sorted(set(urls))


RULE = "=" * 78


def render_notices(
    packages: list[tuple[str, str, str]],
    core: set[str],
    dists: dict[str, list[tuple[str, Path]]],
    bundled: list[tuple[str, str, str]],
) -> tuple[str, list[str]]:
    """The notices text and a list of problems (missing or unreviewed texts, version or license
    mismatches). Deterministic: sorted, no dates beyond those in the inputs."""
    problems: list[str] = []
    covered = [p for p in packages if canonical(p[0]) in dists]
    absent = [p for p in packages if canonical(p[0]) not in dists]
    plats = " and ".join(f"{label} ({triple})" for label, triple in NOTICE_PLATFORMS)
    head = [
        "THIRD-PARTY NOTICES for email-classify-filter (ecf)",
        "",
        "ecf is installed from PyPI; its dependencies are installed beside it, not bundled in",
        "its wheel. This file reproduces, for every runtime dependency at the version locked in",
        "uv.lock (including the optional [eval] extra; development tools are not included), the",
        "license expression from the package metadata and every license and notice file in the",
        "package's wheel (.dist-info), read from the wheels for",
        f"{plats}, Python {NOTICE_PYTHON}.",
        "Texts are verbatim except for line endings and trailing spaces. It also reproduces the",
        "licenses of the third-party data files ecf's own wheel ships.",
        "",
        "Generated by `uv run python scripts/check_licenses.py --notices`; do not edit by hand.",
        "",
        "Contents",
        "",
        "  Data files shipped inside ecf",
        *[f"    {what}" for what, _, _ in bundled],
        "",
        "  Python packages",
    ]
    sections: list[str] = []
    for what, text, provenance in bundled:
        sections += [RULE, what, RULE, "", provenance.rstrip("\n"), "", text.rstrip("\n"), ""]
    for name, version, marker in covered:
        sec, lic, where = _package_section(name, version, dists[canonical(name)], problems,
                                           installed_on(name, marker, core))  # fmt: skip
        head.append(f"    {name} {version} ({lic}; {where})")
        sections += [*sec, ""]
    if absent:
        head += ["", "  Not installed on macOS or Linux, so not covered"]
        for name, version, marker in absent:
            head.append(f"    {name} {version} ({license_of(name, version)}; "
                        f"{installed_on(name, marker, core)})")  # fmt: skip
    return "\n".join([*head, "", *sections]).rstrip("\n") + "\n", problems


def _package_section(
    name: str, version: str, entries: list[tuple[str, Path]], problems: list[str], where: str
) -> tuple[list[str], str, str]:
    """One package's lines; appends to problems. Returns (lines, license, installed on)."""
    md = metadata.PathDistribution(entries[0][1]).metadata
    lic = license_from(name, version, md)
    if lic.startswith("UNKNOWN"):
        problems.append(f"{name} {version}: license {lic}")
    for label, d in entries:
        got = metadata.PathDistribution(d).version
        if got != version:
            problems.append(f"{name}: {label} wheel is {got}, uv.lock has {version}")
    texts: dict[str, dict[str, list[str]]] = {}  # path -> text -> platform labels
    for label, d in entries:
        for path, text in license_files(d):
            texts.setdefault(path, {}).setdefault(text, []).append(label)
    sec = [RULE, f"{name} {version}", RULE, "", f"License: {lic}"]
    if _from_metadata(md).startswith("UNKNOWN"):
        sec.append("  (metadata unreadable; reviewed entry in scripts/check_licenses.py)")
    sec.append(f"Installed on: {where}")
    if "Apache-2.0" in lic and not any(
        p.rsplit("/", 1)[-1].upper().startswith("NOTICE") for p in texts
    ):
        sec.append("NOTICE file: none in the wheel")
    if not texts:
        note = NO_TEXT.get(canonical(name))
        if note is None or note[0] != version:
            problems.append(
                f"{name} {version}: no license file in its wheels; read the upstream license "
                "and add a reviewed NO_TEXT entry (never invent a text)"
            )
        sec += [
            "License text: none in the wheel; the license above is from the package metadata.",
            "Upstream:",
            note[1] if note else ", ".join(_urls(md)),
        ]
    for path in sorted(texts):
        variants = texts[path]
        for text, labels in sorted(variants.items(), key=lambda kv: kv[1]):
            tag = f" ({', '.join(labels)} wheel)" if len(variants) > 1 else ""
            sec += ["", f"--- {path}{tag} ---", "", text.rstrip("\n")]
    return sec, lic, where


def write_notices(path: Path = NOTICES) -> int:
    packages = runtime_packages()
    core = {n for n, _, _ in runtime_packages(extras=False)}
    bundled = [
        (what, _clean((ROOT / lic).read_text("utf-8")), _clean((ROOT / prov).read_text("utf-8")))
        for what, lic, prov in BUNDLED
    ]
    with tempfile.TemporaryDirectory(prefix="ecf-notices-") as tmp:
        dists = dist_infos(install_wheels(Path(tmp)))
        text, problems = render_notices(packages, core, dists, bundled)
    if problems:
        print("THIRD_PARTY_NOTICES not written:", *problems, sep="\n  ")
        return 1
    path.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {path.name}: {len(packages)} packages, {len(text.encode())} bytes")
    return 0


def main() -> int:
    if "--markdown" in sys.argv[1:]:
        print(markdown_table())
        return 0
    if "--notices" in sys.argv[1:]:
        return write_notices()
    bad = 0
    for name, version, _marker in runtime_packages():
        lic = license_of(name, version)
        ok = allowed(name, lic)
        bad += not ok
        print(f"{'ok  ' if ok else 'FAIL'}  {name:28} {lic}")
    print(f"\n{'license check passed' if not bad else f'{bad} package(s) not allowed'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
