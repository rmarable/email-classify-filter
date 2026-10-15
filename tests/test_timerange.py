"""The one time-range parser (OD-507, plan §3.1; D3, P13)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from ecf.errors import InvalidInputError
from ecf.timerange import FORMAT, parse, resolve, stamp

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 9, 29, 14, 0, tzinfo=UTC)  # 10:00 in New York (EDT, -04:00)


def p(text: str, now: datetime = NOW) -> datetime:
    return parse(text, now=now, zone=NY)


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("2026-09-01", datetime(2026, 9, 1, 4, 0, tzinfo=UTC)),  # start of the day, local
        ("2026-09-29T08:00", datetime(2026, 9, 29, 12, 0, tzinfo=UTC)),
        ("2026-09-29 08:00:30", datetime(2026, 9, 29, 12, 0, 30, tzinfo=UTC)),
        ("2026-09-29T08:00Z", datetime(2026, 9, 29, 8, 0, tzinfo=UTC)),
        ("2026-09-29T08:00-07:00", datetime(2026, 9, 29, 15, 0, tzinfo=UTC)),
        ("08:00", datetime(2026, 9, 29, 12, 0, tzinfo=UTC)),  # today, local
        ("09:59:59", datetime(2026, 9, 29, 13, 59, 59, tzinfo=UTC)),
        ("90s", NOW - timedelta(seconds=90)),
        ("30m", NOW - timedelta(minutes=30)),
        ("2h", NOW - timedelta(hours=2)),
        ("3d", NOW - timedelta(days=3)),
        ("2w", NOW - timedelta(weeks=2)),
        ("6mo", NOW - timedelta(days=180)),
        ("1y", NOW - timedelta(days=365)),
        ("1h30m", NOW - timedelta(minutes=90)),
        ("2d12h", NOW - timedelta(hours=60)),
    ],
)
def test_the_forms(text: str, want: datetime) -> None:
    assert p(text) == want


@pytest.mark.parametrize(
    "text", ["3 days", "3M", "30min", "1hr", "2 weeks", "yesterday", "1h1h", "2026-9-1",
             "2026-13-01", "25:00", "2026-09-29T08:00+25:00", "-3d", "1.5d", ""],
)  # fmt: skip
def test_anything_else_is_refused_with_the_short_forms(text: str) -> None:
    with pytest.raises(InvalidInputError) as e:
        p(text)
    assert "m h d w mo y" in e.value.detail or "unit once" in e.value.detail


def test_huge_ages_and_fractional_seconds() -> None:
    with pytest.raises(InvalidInputError, match="too far back"):
        p("9000y")
    assert p("08:00:00.5") == datetime(2026, 9, 29, 12, 0, 0, 500000, tzinfo=UTC)


def test_a_bare_time_later_today_is_refused_never_rolled_back() -> None:
    with pytest.raises(InvalidInputError, match="later today"):
        p("10:00:01")


def test_a_local_time_in_the_spring_gap_is_refused() -> None:
    with pytest.raises(InvalidInputError, match="doesn't exist"):
        p("2026-03-08T02:30", now=datetime(2026, 4, 1, tzinfo=UTC))


def test_a_local_time_in_the_fall_fold_shows_both_utc_readings() -> None:
    with pytest.raises(InvalidInputError) as e:
        p("2026-11-01T01:30", now=datetime(2026, 11, 2, tzinfo=UTC))
    assert "05:30Z" in e.value.detail and "06:30Z" in e.value.detail
    # with the zone it is one moment
    assert p("2026-11-01T01:30-05:00", now=datetime(2026, 11, 2, tzinfo=UTC)) == datetime(
        2026, 11, 1, 6, 30, tzinfo=UTC)  # fmt: skip


def test_output_matches_the_audit_logs_timestamps() -> None:
    assert stamp(p("2026-09-29T08:00")) == "2026-09-29T12:00:00.000000Z"
    assert p(stamp(NOW)) == NOW  # the service reads the client's form back
    assert datetime.strptime(stamp(NOW), FORMAT).replace(tzinfo=UTC) == NOW


def test_resolve_checks_the_pair_and_caps_until_at_now() -> None:
    since, until = resolve("3d", "2099-01-01", now=NOW, zone=NY)
    assert since == NOW - timedelta(days=3) and until == NOW
    assert resolve("3d", None, now=NOW, zone=NY) == (NOW - timedelta(days=3), None)
    with pytest.raises(InvalidInputError, match="isn't after"):
        resolve("1d", "2d", now=NOW, zone=NY)
    with pytest.raises(InvalidInputError, match="future"):
        resolve("2099-01-01", None, now=NOW, zone=NY)
