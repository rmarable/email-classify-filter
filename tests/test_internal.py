"""The internal set and Gmail address folding (V1.6 step 1b; SPEC §7.2; OD-431, OD-432)."""

from __future__ import annotations

import sqlite3

import pytest

from ecf_server import internal
from ecf_server.internal import OrgAddress


@pytest.mark.parametrize(
    ("addr", "want"),
    [
        ("Pat.Lee+ecf@gmail.com", "patlee@gmail.com"),
        ("p.a.t.l.e.e@googlemail.com", "patlee@gmail.com"),
        ("Pat.Lee+x@acme.example", "pat.lee+x@acme.example"),  # other providers: lower-case only
    ],
)
def test_gmail_folding(addr: str, want: str) -> None:
    assert internal.fold(addr) == want


def test_bare_local_ignores_dots_and_tags_everywhere() -> None:
    assert internal.bare_local("Pat.Lee+x@outlook.com") == "patlee"


def test_name_words_fold_case_accents_and_lookalike_letters() -> None:
    assert internal.name_words("José  LEE") == internal.name_words("jose lee")
    assert internal.name_words("P\u0430t Lee") == internal.name_words("Pat Lee")  # Cyrillic a
    assert internal.name_words("Lee, Pat (CEO)") == ("lee", "pat", "ceo")


def test_listed_is_exact_or_the_same_gmail_account() -> None:
    entries = (OrgAddress("patlee@gmail.com", "Pat Lee"), OrgAddress("dana@acme.example"))
    assert internal.listed("pat.lee+x@googlemail.com", entries) == entries[0]
    assert internal.listed("DANA@acme.example", entries) == entries[1]
    assert internal.listed("da.na@acme.example", entries) is None  # dots matter off Gmail
    assert internal.exactly_listed("patlee@gmail.com", entries)
    assert not internal.exactly_listed("pat.lee@gmail.com", entries)  # OD-432: not for origin


def test_org_addresses_reads_the_config(conn: sqlite3.Connection) -> None:
    assert internal.org_addresses(conn) == ()
    conn.execute("INSERT INTO settings (key, value, updated_at, updated_by) VALUES (?, ?, 'now',"
                 " 'test')", (internal.ORG_ADDRESSES_KEY,
                              '[{"address": "patlee@gmail.com", "name": "Pat Lee"}]'))  # fmt: skip
    assert internal.org_addresses(conn) == (OrgAddress("patlee@gmail.com", "Pat Lee"),)
