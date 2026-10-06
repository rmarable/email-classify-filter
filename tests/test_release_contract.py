"""The release assets `scripts/release_build.py` writes are the ones `ecf upgrade` accepts
(`ecf/release_source.py`): tag to version, wheel name, SHA256SUMS and release-manifest.json
(v1.0.0, OD-458)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from ecf import release_source as rs

ROOT = Path(__file__).resolve().parent.parent


def _release_build() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "release_build", ROOT / "scripts/release_build.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["release_build"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_what_the_build_writes_is_what_upgrade_accepts() -> None:
    rb = _release_build()
    for tag in ("v1.0.0", "v1.0.0-rc1", "v2.3.4-rc12"):
        assert rb.tag_to_version(tag) == rs.tag_version(tag)
    version = "1.0.0rc1"
    hashes = {rs.wheel_name(version): "a" * 64, f"email_classify_filter-{version}.tar.gz": "b" * 64}
    assert rs.parse_sums(rb.sums_text(hashes)) == hashes
    manifest = rb.manifest_text(version, "v1.0.0-rc1", "c" * 40, hashes)
    assert rs.check_manifest(manifest, "v1.0.0-rc1") == hashes
