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
