import re
from pathlib import Path

import pytest

from ecf import errors as e

SPEC = Path(__file__).resolve().parents[1] / "SPEC.md"
EXIT_RULE = {
    e.ExitCode.USER_ERROR: {400, 404},
    e.ExitCode.REFUSED: {401, 403, 422},
    e.ExitCode.UNAVAILABLE: {429, 503},
    e.ExitCode.CONFLICT: {409},
    e.ExitCode.INTERNAL: {500},
}


def test_every_code_has_a_spec_and_a_class() -> None:
    assert set(e.ERROR_TABLE) == set(e.ErrorCode)
    classes = {c.code for c in e.EcfError.__subclasses__()}
    assert classes == set(e.ErrorCode)


def test_exit_codes_follow_the_rule() -> None:
    for s in e.ERROR_TABLE.values():
        assert s.http_status in EXIT_RULE[s.exit_code], s.code


def test_table_matches_spec() -> None:
    text = SPEC.read_text(encoding="utf-8")
    section = text[text.index("### 15.3 Error codes") : text.index("### 15.4")]
    rows = re.findall(r"^\| `([a-z_]+)` \| (\d{3}) \| (\d) \|", section, re.M)
    assert rows, "no rows parsed from SPEC §15.3"
    spec = {code: (int(http), int(ex)) for code, http, ex in rows}
    ours = {s.code.value: (s.http_status, int(s.exit_code)) for s in e.ERROR_TABLE.values()}
    assert ours == spec


@pytest.mark.parametrize("cls", e.EcfError.__subclasses__())
def test_problem_round_trip(cls: type[e.EcfError]) -> None:
    err = cls("short detail", nonce_id="ab" * 16)
    body = err.to_problem(instance="/v1/items/x")
    assert body["status"] == err.spec.http_status
    assert body["type"] == e.PROBLEM_TYPE_BASE + cls.code.value
    back = e.EcfError.from_problem(body)
    assert type(back) is cls
    assert back.detail == "short detail"
    assert back.extra == {"nonce_id": "ab" * 16}
    assert err.to_mcp()["isError"] is True


def test_unknown_code_becomes_internal() -> None:
    back = e.EcfError.from_problem({"code": "made_up", "detail": "x"})
    assert isinstance(back, e.InternalError)
    assert back.exit_code == e.ExitCode.INTERNAL


def test_default_detail_is_title() -> None:
    assert e.NotFoundError().detail == "Unknown id"
