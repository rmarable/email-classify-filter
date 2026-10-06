"""The license gate's decision logic (scripts/check_licenses.py)."""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_licenses.py"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_licenses", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cl = load()


@pytest.mark.parametrize(
    ("name", "lic", "ok"),
    [
        ("x", "MIT", True),
        ("x", "MIT OR Apache-2.0", True),
        ("x", "Apache-2.0 OR BSD-3-Clause", True),
        ("x", "GPL-3.0", False),
        ("x", "MIT AND GPL-3.0", False),
        ("x", "MPL-2.0", False),
        ("certifi", "MPL-2.0", True),  # the named exception
        ("x", "UNKNOWN ('?')", False),
    ],
)
def test_allowed(name: str, lic: str, ok: bool) -> None:
    assert cl.allowed(name, lic) is ok


def test_override_used_only_at_the_reviewed_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(cl.OVERRIDES, "no-such-package-ecf", ("1.0", "MIT", "test"))
    assert cl.license_of("no-such-package-ecf", "1.0") == "MIT"
    assert cl.license_of("no-such-package-ecf", "1.1").startswith("UNKNOWN (reviewed at 1.0")
    assert cl.license_of("another-missing-ecf", "1.0").startswith("UNKNOWN (not installed")


def test_installed_metadata_beats_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(cl.OVERRIDES, "pydantic", ("0.0", "GPL-3.0", "wrong on purpose"))
    assert cl.license_of("pydantic", "whatever") == "MIT"


def test_override_covers_unreadable_metadata_only_at_the_reviewed_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # dkimpy's metadata says "BSD-like", which the checker can't map; the reviewed entry applies.
    version = cl.metadata.version("dkimpy")
    monkeypatch.setitem(cl.OVERRIDES, "dkimpy", (version, "Zlib", "test"))
    assert cl.license_of("dkimpy", version) == "Zlib"
    monkeypatch.setitem(cl.OVERRIDES, "dkimpy", ("0.0", "Zlib", "test"))
    assert cl.license_of("dkimpy", version).startswith("UNKNOWN (reviewed at 0.0")
    monkeypatch.delitem(cl.OVERRIDES, "dkimpy")
    assert cl.license_of("dkimpy", version).startswith("UNKNOWN (")


def _dist(root: Path, name: str, version: str, lic: str, files: dict[str, str]) -> Path:
    d = root / f"{name.replace('-', '_')}-{version}.dist-info"
    d.mkdir(parents=True)
    (d / "METADATA").write_text(
        f"Metadata-Version: 2.4\nName: {name}\nVersion: {version}\nLicense-Expression: {lic}\n"
        "Home-page: https://example.invalid/pkg\n"
    )
    (d / "RECORD").write_text("")
    for rel, text in files.items():
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_bytes(text.encode())
    return d


def test_notices_carry_each_license_file_and_flag_platform_differences(tmp_path: Path) -> None:
    mac = _dist(
        tmp_path / "mac",
        "pkg-a",
        "1.0",
        "Apache-2.0",
        {"licenses/LICENSE": "Apache text\r\n", "licenses/AUTHORS": "Ann  \n"},
    )
    lin = _dist(tmp_path / "lin", "pkg-a", "1.0", "Apache-2.0",
                {"licenses/LICENSE": "Apache text\n", "licenses/AUTHORS": "Bob\n"})  # fmt: skip
    b = _dist(tmp_path / "mac", "Pkg.B", "2.0", "MIT", {"LICENSE.txt": "MIT text\n"})
    dists = {"pkg-a": [("macOS", mac), ("Linux", lin)], "pkg-b": [("macOS", b)]}
    pkgs = [("pkg-a", "1.0", ""), ("pkg-b", "2.0", "sys_platform == 'darwin'"),
            ("pkg-w", "3.0", "sys_platform == 'win32'")]  # fmt: skip
    data = [("Data X", "X lic\n", "X src\n")]
    text, problems = cl.render_notices(pkgs, {"pkg-a"}, dists, data)
    assert problems == []
    assert "    pkg-a 1.0 (Apache-2.0; all platforms)" in text
    assert "    pkg-b 2.0 (MIT; macOS)" in text
    assert "--- licenses/LICENSE ---\n\nApache text\n" in text  # CRLF folded: one variant
    assert "--- licenses/AUTHORS (macOS wheel) ---\n\nAnn\n" in text  # trailing spaces cut
    assert "--- licenses/AUTHORS (Linux wheel) ---\n\nBob\n" in text
    assert "NOTICE file: none in the wheel" in text
    assert "--- LICENSE.txt ---\n\nMIT text\n" in text
    assert "Not installed on macOS or Linux, so not covered\n    pkg-w 3.0" in text
    assert text.index("Data X\n") < text.index("pkg-a 1.0\n")
    assert text == cl.render_notices(pkgs, {"pkg-a"}, dists, data)[0]  # deterministic


def test_notices_never_invent_a_missing_license_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    d = _dist(tmp_path, "bare", "1.0", "MIT", {})
    pkgs = [("bare", "1.0", "")]
    text, problems = cl.render_notices(pkgs, {"bare"}, {"bare": [("macOS", d)]}, [])
    assert problems and "no license file" in problems[0]
    assert "https://example.invalid/pkg" in text
    monkeypatch.setitem(cl.NO_TEXT, "bare", ("1.0", "reviewed upstream note"))
    text, problems = cl.render_notices(pkgs, {"bare"}, {"bare": [("macOS", d)]}, [])
    assert problems == []
    assert "License text: none in the wheel" in text and "reviewed upstream note" in text
    monkeypatch.setitem(cl.NO_TEXT, "bare", ("0.9", "reviewed at another version"))
    assert cl.render_notices(pkgs, {"bare"}, {"bare": [("macOS", d)]}, [])[1]


def test_notices_flag_a_wheel_at_another_version(tmp_path: Path) -> None:
    d = _dist(tmp_path, "pkg", "1.1", "MIT", {"licenses/LICENSE": "x\n"})
    _, problems = cl.render_notices([("pkg", "1.0", "")], {"pkg"}, {"pkg": [("Linux", d)]}, [])
    assert problems == ["pkg: Linux wheel is 1.1, uv.lock has 1.0"]


def test_committed_notices_list_every_locked_package() -> None:
    # Regenerating needs the network (CI step "THIRD_PARTY_NOTICES is current"); this catches a
    # uv.lock change without it.
    contents = cl.NOTICES.read_text(encoding="utf-8").split("\n\n" + cl.RULE, 1)[0]
    _, _, packages = contents.partition("  Python packages\n")
    listed = {
        tuple(line.split(" (", 1)[0].split())
        for line in packages.splitlines()
        if line.startswith("    ")
    }
    assert listed == {(n, v) for n, v, _ in cl.runtime_packages()}
