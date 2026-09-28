"""Check every runtime dependency's license, extras included, against the allow-list (SPEC §17.5).

The dependency list comes from `uv export --no-dev --all-extras`. Licenses come from installed
package metadata: License-Expression, then a short License field, then trove classifiers.
Unknown or disallowed licenses fail the check. Add a reviewed entry to OVERRIDES only after
checking the upstream license.
"""

from __future__ import annotations

import subprocess
import sys
from importlib import metadata

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
# Reviewed entries for packages whose metadata can't be parsed, or that aren't installed on the
# platform running the check (platform-specific dependencies): name -> (license, source checked).
OVERRIDES: dict[str, tuple[str, str]] = {
    "colorama": ("BSD", "PyPI classifiers for 0.4.6, checked 2026-09-27 (Windows-only, via click)"),
    # Linux-only, via keyring (Secret Service); PyPI metadata at the locked versions, 2026-09-27
    "cffi": ("MIT-0", "PyPI License-Expression, 2.1.1"),
    "cryptography": ("Apache-2.0 OR BSD-3-Clause", "PyPI License-Expression, 50.0.1"),
    "jeepney": ("MIT", "PyPI License-Expression, 0.9.0"),
    "pycparser": ("BSD-3-Clause", "PyPI License-Expression, 3.0"),
    "secretstorage": ("BSD-3-Clause", "PyPI License-Expression, 3.5.0"),
    # Windows-only, via keyring
    "pywin32-ctypes": ("BSD-3-Clause", "PyPI License field, 0.2.3"),
    # macOS-only (Keychain interaction control); PyPI License field at 12.2.2, 2026-09-27
    "pyobjc-core": ("MIT", "PyPI License field, 12.2.2"),
    "pyobjc-framework-cocoa": ("MIT", "PyPI License field, 12.2.2"),
    "pyobjc-framework-security": ("MIT", "PyPI License field, 12.2.2"),
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
        where = "all platforms" if name in core else "`[eval]` extra only"
        for plat, label in (("darwin", "macOS"), ("linux", "Linux"), ("win32", "Windows")):
            if f"sys_platform == '{plat}'" in marker:
                where = label
        rows.append(f"| `{name}` | {version} | {license_of(name)} | {where} |")
    return "\n".join(rows)


def runtime_names() -> list[str]:
    return [name for name, _, _ in runtime_packages()]


def license_of(name: str) -> str:
    if name.lower() in OVERRIDES:
        return OVERRIDES[name.lower()][0]
    try:
        md = metadata.metadata(name)
    except metadata.PackageNotFoundError:
        return "UNKNOWN (not installed here; add a reviewed entry to OVERRIDES)"
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


def main() -> int:
    if "--markdown" in sys.argv[1:]:
        print(markdown_table())
        return 0
    bad = 0
    for name in runtime_names():
        lic = license_of(name)
        ok = allowed(name, lic)
        bad += not ok
        print(f"{'ok  ' if ok else 'FAIL'}  {name:28} {lic}")
    print(f"\n{'license check passed' if not bad else f'{bad} package(s) not allowed'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
