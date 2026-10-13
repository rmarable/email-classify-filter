import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ecf import ids


@pytest.mark.parametrize("value", ["billing", "accounts-payable", "a", "x1", "a" * 40])
def test_slug_accepts(value: str) -> None:
    assert re.fullmatch(ids.SLUG_PATTERN, value)


@pytest.mark.parametrize(
    "value", ["", "-billing", "billing-", "Billing", "bill_ing", "a" * 41, "a b"]
)
def test_slug_rejects(value: str) -> None:
    assert not re.fullmatch(ids.SLUG_PATTERN, value)


def test_stable_and_short_ids() -> None:
    h = "ab" * 32
    assert re.fullmatch(ids.HEX64_PATTERN, h) and re.fullmatch(ids.SHORT_ID_PATTERN, h[:8])
    for bad in ("ab" * 31, h.upper()):
        assert not re.fullmatch(ids.HEX64_PATTERN, bad)
    assert not re.fullmatch(ids.SHORT_ID_PATTERN, h[:7])


@given(st.integers(min_value=0, max_value=50))
def test_random_ids_are_32_hex_and_unique(_: int) -> None:
    a, b = ids.new_grant_id(), ids.new_job_id()
    assert re.fullmatch(r"[0-9a-f]{32}", a) and re.fullmatch(r"[0-9a-f]{32}", b)
    assert a != b
