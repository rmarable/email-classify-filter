import re

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import TypeAdapter, ValidationError

from ecf import ids

addr: TypeAdapter[str] = TypeAdapter(ids.AddressIdField)
stable: TypeAdapter[str] = TypeAdapter(ids.StableIdField)
short: TypeAdapter[str] = TypeAdapter(ids.ShortIdField)


@pytest.mark.parametrize("value", ["billing", "accounts-payable", "a", "x1", "a" * 40])
def test_slug_accepts(value: str) -> None:
    assert addr.validate_python(value) == value


@pytest.mark.parametrize(
    "value", ["", "-billing", "billing-", "Billing", "bill_ing", "a" * 41, "a b"]
)
def test_slug_rejects(value: str) -> None:
    with pytest.raises(ValidationError):
        addr.validate_python(value)


def test_stable_and_short_ids() -> None:
    h = "ab" * 32
    assert stable.validate_python(h) == h
    assert short.validate_python(h[:8]) == h[:8]
    for bad in ("ab" * 31, h.upper(), h[:7]):
        with pytest.raises(ValidationError):
            (stable if len(bad) != 7 else short).validate_python(bad)


@given(st.integers(min_value=0, max_value=50))
def test_random_ids_are_32_hex_and_unique(_: int) -> None:
    a, b = ids.new_grant_id(), ids.new_job_id()
    assert re.fullmatch(ids.HEX32_PATTERN, a) and re.fullmatch(ids.HEX32_PATTERN, b)
    assert a != b
