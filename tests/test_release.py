"""`ecf_server/data/release.json` matches the code it describes (V1.5 step 11a; OD-375)."""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

from ecf import __version__
from ecf.schema import load_schema
from ecf_server import api, classifier, db, export_bundle


def test_release_json_matches_the_code() -> None:
    r = json.loads(resources.files("ecf_server.data").joinpath("release.json").read_text())
    assert r["product"] == "email-classify-filter"
    assert r["version"] == __version__
    assert r["schema_version"] == max(v for v, _n, _s in db._migration_files())  # pyright: ignore[reportPrivateUsage]
    assert r["data_format"] == export_bundle.DATA_FORMAT
    assert r["api_version"] == api.API_VERSION
    assert r["classifier_schema"] == load_schema().version
    assert r["prompt_digest"] == classifier.prompt_digest(load_schema())  # new wording: update it
    assert isinstance(r["min_client"], str)


def test_readme_status_names_this_release() -> None:
    # The bump commit updates README's Status (SPEC §1.4); an rc leaves it on the last release.
    if "rc" in __version__:
        return
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text()
    status = readme.split("## Status\n", 1)[1].split("\n## ", 1)[0]
    assert status.strip().startswith(f"`v{__version__}` ("), "README Status must name this release"
