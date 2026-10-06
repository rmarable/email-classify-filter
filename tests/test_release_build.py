"""The release build's file contract (scripts/release_build.py; SPEC §17.4): SHA256SUMS and
release-manifest.json formats, and the tag-versus-version check. `ecf upgrade` reads both files."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import zipfile
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "release_build.py"
COMMIT = "0123456789abcdef0123456789abcdef01234567"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("release_build", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rb = load()


def fake_build(out: Path, version: str, release_version: str | None = None) -> None:
    """A wheel with METADATA and release.json, and an sdist, named as hatchling names them."""
    out.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out / f"email_classify_filter-{version}-py3-none-any.whl", "w") as z:
        z.writestr(
            f"email_classify_filter-{version}.dist-info/METADATA",
            f"Metadata-Version: 2.4\nName: email-classify-filter\nVersion: {version}\n",
        )
        z.writestr(
            "ecf_server/data/release.json", json.dumps({"version": release_version or version})
        )
    (out / f"email_classify_filter-{version}.tar.gz").write_bytes(b"sdist " + version.encode())
    (out / ".gitignore").write_text("*\n")  # uv writes one; it is not a release file


@pytest.mark.parametrize(
    ("tag", "version"),
    [("v1.0.0", "1.0.0"), ("v1.0.0-rc1", "1.0.0rc1"), ("v12.3.45-rc10", "12.3.45rc10")],
)
def test_tag_to_version(tag: str, version: str) -> None:
    assert rb.tag_to_version(tag) == version


@pytest.mark.parametrize(
    "tag",
    ["1.0.0", "v1.0", "v1.0.0rc1", "v1.0.0-rc0", "v1.0.0-beta1", "v01.0.0", "v1.0.0-rc1 ",
     "ms-v1.6-gmail", "v1.0.0.post1"],
)  # fmt: skip
def test_malformed_tags_are_refused(tag: str) -> None:
    with pytest.raises(rb.ReleaseError):
        rb.tag_to_version(tag)


def test_sha256sums_format_is_sorted_two_space_lines() -> None:
    hashes = {"b.tar.gz": "b" * 64, "a.whl": "a" * 64}
    text = rb.sums_text(hashes)
    assert text == f"{'a' * 64}  a.whl\n{'b' * 64}  b.tar.gz\n"
    assert all(re.fullmatch(r"[0-9a-f]{64}  \S+", line) for line in text.splitlines())


def test_manifest_has_sorted_keys_and_the_same_hashes() -> None:
    hashes = {"z.tar.gz": "1" * 64, "a.whl": "2" * 64}
    text = rb.manifest_text("1.0.0rc1", "v1.0.0-rc1", COMMIT, hashes)
    doc = json.loads(text)
    assert doc == {"version": "1.0.0rc1", "tag": "v1.0.0-rc1", "commit": COMMIT, "files": hashes}
    assert list(doc) == sorted(doc)
    assert list(doc["files"]) == sorted(doc["files"])
    assert text.endswith("}\n")


@pytest.mark.parametrize("commit", ["abc", COMMIT.upper(), COMMIT + "0"])
def test_manifest_refuses_a_bad_commit(commit: str) -> None:
    with pytest.raises(rb.ReleaseError):
        rb.manifest_text("1.0.0", "v1.0.0", commit, {})


def test_check_built_matches_the_tag(tmp_path: Path) -> None:
    fake_build(tmp_path, "1.0.0rc1")
    version, hashes = rb.check_built(tmp_path, "v1.0.0-rc1")
    assert version == "1.0.0rc1"
    assert sorted(hashes) == [
        "email_classify_filter-1.0.0rc1-py3-none-any.whl",
        "email_classify_filter-1.0.0rc1.tar.gz",
    ]
    for name, digest in hashes.items():
        assert digest == hashlib.sha256((tmp_path / name).read_bytes()).hexdigest()
    # SHA256SUMS and the manifest carry the same hashes
    pairs = [line.split("  ") for line in rb.sums_text(hashes).splitlines()]
    sums = {name: digest for digest, name in pairs}
    assert sums == json.loads(rb.manifest_text(version, "v1.0.0-rc1", COMMIT, hashes))["files"]


def test_check_built_without_a_tag(tmp_path: Path) -> None:
    fake_build(tmp_path, "0.1.0.dev0")
    assert rb.check_built(tmp_path, None)[0] == "0.1.0.dev0"


@pytest.mark.parametrize("tag", ["v1.0.0", "v1.0.0-rc2", "v1.0.1-rc1"])
def test_check_built_refuses_a_tag_for_another_version(tmp_path: Path, tag: str) -> None:
    fake_build(tmp_path, "1.0.0rc1")
    with pytest.raises(rb.ReleaseError, match=r"the package is 1\.0\.0rc1"):
        rb.check_built(tmp_path, tag)


def test_check_built_refuses_release_json_out_of_step(tmp_path: Path) -> None:
    fake_build(tmp_path, "1.0.0", release_version="0.9.0")
    with pytest.raises(rb.ReleaseError, match=r"release\.json"):
        rb.check_built(tmp_path, "v1.0.0")


def test_check_built_refuses_extra_files(tmp_path: Path) -> None:
    fake_build(tmp_path, "1.0.0")
    (tmp_path / "email_classify_filter-0.9.0.tar.gz").write_bytes(b"old")
    with pytest.raises(rb.ReleaseError, match="expected"):
        rb.check_built(tmp_path, "v1.0.0")


def test_main_refuses_a_malformed_tag_before_building(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def no_build(out: Path, epoch: str) -> None:
        raise AssertionError("built")

    monkeypatch.setattr(rb, "build", no_build)
    assert rb.main(["--out", str(tmp_path / "out"), "--tag", "1.0.0"]) == 1
    assert "is not vX.Y.Z" in capsys.readouterr().err


def test_main_refuses_a_non_empty_out_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def no_build(out: Path, epoch: str) -> None:
        raise AssertionError("built")

    monkeypatch.setattr(rb, "build", no_build)
    (tmp_path / "stale.whl").write_bytes(b"x")
    assert rb.main(["--out", str(tmp_path)]) == 1
    assert "is not empty" in capsys.readouterr().err


def test_main_writes_both_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "out"
    epochs: list[str] = []

    def fake(o: Path, epoch: str) -> None:
        epochs.append(epoch)
        fake_build(o, "0.1.0.dev0")

    monkeypatch.setattr(rb, "build", fake)
    assert rb.main(["--out", str(out)]) == 0
    assert epochs and epochs[0].isdigit()  # SOURCE_DATE_EPOCH from the commit time
    doc = json.loads((out / "release-manifest.json").read_text())
    assert re.fullmatch(r"[0-9a-f]{40}", doc["commit"])
    assert doc["tag"] is None
    assert rb.sums_text(doc["files"]) == (out / "SHA256SUMS").read_text()
