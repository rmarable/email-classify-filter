"""A decrypted corpus held by the service for labelling, eval runs and rescoring (SPEC §16.7; R46,
R104, R154).

One session at a time. Opening decrypts the file once and checks it (`corpus.open_corpus`); the
passphrase is then dropped (Python can't wipe a string; a stated limit, R125). The session keeps
the compressed tar and reads one message at a time. It is released when the CLI closes it, when
an eval run that owns it ends, after `IDLE_S` without a request (the service tick checks), or when
the service restarts. A busy session (an eval run is using it) can't be closed except with
`stop`, which asks the run to stop after its current case. Nothing here is logged or audited
with content; routes are CLI only.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ecf.errors import ConflictError, InvalidInputError, NotFoundError
from ecf.ids import new_random_id
from ecf_server import corpus

IDLE_S = 15 * 60
SHOWN_FACTS = ("auth_result", "sender_origin", "from_org_address", "impersonates_internal",
               "reply_to_mismatch", "self_sent", "sender_seen_before", "payment_keyword",
               "gift_card_keyword", "bec_opener", "tax_form_request")  # fmt: skip


@dataclass
class Session:
    id: str
    path: Path
    corpus: corpus.Corpus
    used: float
    busy: bool = False
    stop: threading.Event = field(default_factory=threading.Event, repr=False)

    @property
    def rows(self) -> list[dict[str, Any]]:
        return self.corpus.rows


_LOCK = threading.Lock()
_current: Session | None = None


def open_session(path: str, secret: str, monotonic: Callable[[], float]) -> dict[str, Any]:
    """Decrypt and check a corpus and hold it; refused while another session is open."""
    global _current  # noqa: PLW0603 - one session per service, by design
    p = Path(path).expanduser()
    if p.suffix != corpus.SUFFIX or not p.is_file():
        raise InvalidInputError(f"no corpus file at {p}")
    with _LOCK:
        if _current is not None:
            raise ConflictError("another corpus is open; close it first (or wait 15 minutes)")
    opened = corpus.open_corpus(p, secret)
    del secret
    s = Session(new_random_id(), p.resolve(), opened, monotonic())
    with _LOCK:
        if _current is not None:
            raise ConflictError("another corpus is open; close it first (or wait 15 minutes)")
        _current = s
    return {"session_id": s.id, "corpus_id": opened.first["corpus_id"], "count": len(s.rows),
            "header": opened.header, "path": str(s.path)}  # fmt: skip


def get(session_id: str, monotonic: Callable[[], float]) -> Session:
    with _LOCK:
        s = _current
        if s is None or s.id != session_id:
            raise NotFoundError("no such corpus session (closed, expired or never opened)")
        s.used = monotonic()
        return s


def status(monotonic: Callable[[], float]) -> dict[str, Any] | None:
    with _LOCK:
        s = _current
        if s is None:
            return None
        return {"session_id": s.id, "corpus_id": s.corpus.first["corpus_id"],
                "age_s": int(monotonic() - s.used), "busy": s.busy}  # fmt: skip


def close(session_id: str, *, stop: bool = False) -> dict[str, Any]:
    """The CLI's `finally`: release the session. A busy one is refused unless `stop`, which asks
    its run to stop after the current case (it is released when the run ends)."""
    global _current  # noqa: PLW0603
    with _LOCK:
        s = _current
        if s is None or s.id != session_id:
            return {"closed": False}
        if s.busy:
            if not stop:
                raise ConflictError("an eval run is using this corpus; `ecf eval stop` first")
            s.stop.set()
            from ecf_server import evalrun  # noqa: PLC0415 - evalrun imports this module

            evalrun.stop()
            return {"closed": False, "stopping": True}
        _current = None
    return {"closed": True}


def set_busy(session_id: str, busy: bool) -> Session:
    """An eval run takes the session (and releases it when it ends: `release`)."""
    with _LOCK:
        s = _current
        if s is None or s.id != session_id:
            raise NotFoundError("no such corpus session (closed, expired or never opened)")
        if busy and s.busy:
            raise ConflictError("this corpus is already in use by an eval run")
        s.busy = busy
        if busy:
            s.stop.clear()
        return s


def release(session_id: str) -> None:
    """The run that owned the session ended."""
    global _current  # noqa: PLW0603
    with _LOCK:
        if _current is not None and _current.id == session_id:
            _current = None


def expire_idle(now: float) -> bool:
    """The service tick: drop a session idle for IDLE_S that no run is using."""
    global _current  # noqa: PLW0603
    with _LOCK:
        if _current is not None and not _current.busy and now - _current.used > IDLE_S:
            _current = None
            return True
    return False


def keys(s: Session) -> list[dict[str, Any]]:
    """Each message's index and label key, in manifest order."""
    return [{"index": r["index"], "key": r["key"]} for r in s.rows]


def item(s: Session, index: int) -> dict[str, Any]:
    """What the label screen shows for one message (R66): the stored display headers, attachment
    names, the authentication result and a few facts, the stored excerpts redacted and not. No
    model output (R6)."""
    row = next((r for r in s.rows if r["index"] == index), None)
    if row is None:
        raise NotFoundError(f"message {index} isn't in this corpus")
    facts: dict[str, Any] = row.get("facts") or {}
    triggers: dict[str, Any] = facts.get("triggers") or {}
    return {
        "index": index, "key": row["key"], "display": row["display"],
        "excerpt": row["excerpts"]["actor"], "unredacted": row["unredacted"]["actor"],
        "facts": {k: facts.get(k) for k in SHOWN_FACTS},
        "keywords": facts.get("keywords") or {},
        "triggers": {k: v for k, v in triggers.items() if v},
        "folder": row.get("folder"),
    }  # fmt: skip
