"""`ecf replay` (V1.3 step 8d; SPEC §16.6, OD-238): fresh Message-IDs, cycling, and appending into
a real test server."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest

from ecf import replay
from ecf.errors import InvalidInputError
from tests.mail_contract import message

SYNTHETIC = Path(__file__).parent / "eval" / "synthetic"


def test_a_fresh_message_id_replaces_the_old_one() -> None:
    raw = message(1)
    out = replay.fresh_message_id(raw, "abc123")
    assert b"<contract-1@synthetic.acme.example>" not in out
    assert out.count(b"Message-ID:") == 1
    assert b"Message-ID: <replay-abc123@replay.acme.example>" in out
    assert out.split(b"\r\n\r\n", 1)[1] == raw.split(b"\r\n\r\n", 1)[1]  # the body is untouched


def test_a_message_without_one_gets_one() -> None:
    raw = b"From: a@b.example\r\nSubject: x\r\n\r\nbody\r\n"
    out = replay.fresh_message_id(raw, "t1")
    assert out.startswith(b"Message-ID: <replay-t1@replay.acme.example>\r\nFrom:")
    assert out.endswith(b"\r\n\r\nbody\r\n")


def test_an_empty_folder_is_refused(tmp_path: Path) -> None:
    with pytest.raises(InvalidInputError, match=r"no \.eml"):
        replay.files(tmp_path)


@pytest.mark.imap
def test_replay_appends_with_fresh_ids_into_dovecot(dovecot_server: Any) -> None:
    from tests import dovecot  # noqa: PLC0415

    dv: dovecot.Dovecot = dovecot_server
    user = f"ecf-t-{uuid.uuid4().hex[:12]}"
    n = replay.replay(SYNTHETIC / "eml", host=dv.host, port=dv.port, user=user,
                      password=dovecot.PASSWORD, count=5, cafile=dv.ca_file)  # fmt: skip
    assert n == 5
    admin = dv.admin(user)
    try:
        admin.select("INBOX", readonly=True)
        _typ, data = admin.search(None, "ALL")
        uids = data[0].split()
        assert len(uids) == 5
        ids: set[bytes] = set()
        for u in uids:
            _t, d = admin.fetch(u, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])")
            part = d[0]
            assert isinstance(part, tuple)
            ids.add(bytes(part[1]).strip())
        assert len(ids) == 5 and all(b"@replay.acme.example>" in i for i in ids)
    finally:
        admin.logout()
