"""`ecf_server/data/release.json` matches the code it describes (V1.5 step 11a; OD-375)."""

from __future__ import annotations

import json
from importlib import resources

from ecf import __version__
from ecf.schema import load_schema
from ecf_server import api, db, export_bundle


def test_release_json_matches_the_code() -> None:
    r = json.loads(resources.files("ecf_server.data").joinpath("release.json").read_text())
    assert r["product"] == "email-classify-filter"
    assert r["version"] == __version__
    assert r["schema_version"] == max(v for v, _n, _s in db._migration_files())  # pyright: ignore[reportPrivateUsage]
    assert r["data_format"] == export_bundle.DATA_FORMAT
    assert r["api_version"] == api.API_VERSION
    assert r["classifier_schema"] == load_schema().version
    assert isinstance(r["min_client"], str)
