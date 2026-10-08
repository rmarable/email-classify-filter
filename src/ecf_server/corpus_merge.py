"""`ecf corpus merge`: several corpora into one, for a top-up (SPEC §16.7; R52, R107, R109, R115,
R163, R173).

Each source is decrypted and copied one at a time, so only one source's plaintext is held beside
the growing output. Messages are de-duplicated across sources by their label key and re-indexed;
each row keeps the corpus and folder it came from. Sources from different mailboxes are refused
unless `--allow-mixed`, which then keeps the profile per row. Labels files beside the sources are
merged by key; two different labels for one key are refused and listed. The result is a new corpus
with a new ID and passphrase, written after a step-up bound to the sources' file hashes and the
output path, so nothing is decrypted before it.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from ecf import __version__
from ecf.errors import InvalidInputError
from ecf_server import corpus, export_keys, install_identity, stepup
from ecf_server.clock import Clock, to_ts
from ecf_server.db import write_tx
from ecf_server.notify import Notifier

LABELS_SUFFIX = ".labels.jsonl"
VOLATILE = ("corpus_id", "date")  # label fields that may differ between copies of one label


@dataclass(frozen=True)
class Source:
    path: Path
    secret: str


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def target(paths: Sequence[Path], out: Path) -> dict[str, Any]:
    """What the step-up binds (R173): each source file's sha256, which covers header and payload,
    and the output path. The clear header's corpus ID is shown, marked unverified."""
    sources = sorted(
        ({"sha256": file_sha256(p), "corpus_id": str(corpus.read_header(p).get("corpus_id"))}
         for p in paths),
        key=lambda s: s["sha256"],
    )  # fmt: skip
    return {"sources": sources, "out": str(out)}


@stepup.purpose("corpus_merge")
def _describe(conn: sqlite3.Connection, t: dict[str, Any]) -> stepup.Bound:
    del conn
    ids = ", ".join(str(s.get("corpus_id", ""))[:8] for s in t.get("sources", []))
    text = (f"ecf: merge {len(t.get('sources', []))} corpora ({ids}, unverified) into a new"
            f" encrypted corpus at {t.get('out')}")  # fmt: skip
    hashes = [s.get("sha256") for s in t.get("sources", [])]
    return stepup.Bound(stepup.digest("corpus_merge", hashes, t.get("out")), text)


def from_body(
    conn: sqlite3.Connection, clock: Clock, notifier: Notifier, data_dir: Path, body: dict[str, Any]
) -> dict[str, Any]:
    """`POST /v1/corpus/merge`: `sources` [{path, passphrase}], `out`, `allow_mixed`,
    `passphrase?`, `nonce_id?`."""
    raw = body.get("sources")
    if not isinstance(raw, list):
        raise InvalidInputError("sources: a list of {path, passphrase}")
    sources: list[Source] = []
    for item in cast(list[Any], raw):
        if not isinstance(item, dict):
            raise InvalidInputError("sources: a list of {path, passphrase}")
        d = cast(dict[str, Any], item)
        path, secret = d.get("path"), d.get("passphrase")
        if not isinstance(path, str) or not isinstance(secret, str):
            raise InvalidInputError("each source needs a path and its passphrase")
        sources.append(Source(Path(path), secret))
    out, own, nonce = body.get("out"), body.get("passphrase"), body.get("nonce_id")
    if not isinstance(out, str):
        raise InvalidInputError("out: the new corpus file")
    return merge(conn, clock, notifier, data_dir, sources, out,
                 allow_mixed=body.get("allow_mixed") is True,
                 own_passphrase=own if isinstance(own, str) else None,
                 nonce=nonce if isinstance(nonce, str) else None)  # fmt: skip


def merge(  # noqa: PLR0913 - the service's collaborators, then the request
    conn: sqlite3.Connection,
    clock: Clock,
    notifier: Notifier,
    data_dir: Path,
    sources: Sequence[Source],
    out: str,
    *,
    allow_mixed: bool,
    own_passphrase: str | None,
    nonce: str | None,
) -> dict[str, Any]:
    if len(sources) < 2:
        raise InvalidInputError("merge needs at least two corpora")
    where = corpus.check_out(out, data_dir)
    paths = [Path(s.path).expanduser() for s in sources]
    for p in paths:
        if p.suffix != corpus.SUFFIX or not p.is_file():
            raise InvalidInputError(f"no corpus file at {p}")
    stepup.consume(conn, clock, "corpus_merge", target(paths, where), nonce)
    labels_out = _merge_labels(paths)  # before any decrypt: a conflict stops the merge cheaply
    secret = corpus.new_passphrase(own_passphrase)
    result = corpus.Output()
    seen: set[tuple[str, str]] = set()
    emails: set[str] = set()
    complete = True
    ids: list[str] = []
    profiles: dict[str, dict[str, Any]] = {}
    for p, src in zip(paths, sources, strict=True):
        c = corpus.open_corpus(p, src.secret)
        ids.append(str(c.first["corpus_id"]))
        emails.add(str(c.profile.get("email", "")).lower())
        if len(emails) > 1 and not allow_mixed:
            raise InvalidInputError("these corpora come from different mailboxes; add"
                                    " --allow-mixed to merge them anyway")  # fmt: skip
        profiles[ids[-1]] = c.profile
        complete &= bool(c.header.get("complete"))
        for row in c.rows:
            key = (str(row["key"]["content_hash"]), str(row["key"]["identity_digest"]))
            if key in seen:
                continue
            raw = c.message(int(row["index"]))
            if sum(r["size"] for r in result.rows) + len(raw) > corpus.MAX_BYTES_MAX:
                raise InvalidInputError("the merged corpus would pass 512 MiB of mail")
            index = len(result.rows) + 1
            result.add(f"{index:05d}.eml", raw)
            new = row | {"index": index, "source_corpus_id": ids[-1]}
            if allow_mixed:
                new["profile"] = c.profile
            result.rows.append(new)
            seen.add(key)
        del c
    corpus_id = uuid.uuid4().hex
    domain = "mixed" if len(emails) > 1 else min(emails, default="").rpartition("@")[2]
    header: dict[str, Any] = {
        "format": corpus.FORMAT, "corpus_id": corpus_id, "created_at": to_ts(clock.now()),
        "ecf_version": __version__, "source_domain": domain, "folder": "merged",
        "order": "merged", "count": len(result.rows),
        "bytes": sum(r["size"] for r in result.rows), "skipped": {}, "complete": complete,
        "reason": "merged", "source_preset": None, "none_reasons": {}, "sources": ids,
    }  # fmt: skip
    first = {"corpus_id": corpus_id, "install_id": install_identity.install_id(conn),
             "folder": "merged"}  # fmt: skip
    profile = next(iter(profiles.values())) if len(emails) == 1 else {"mixed": True}
    final, sha = corpus.write_sealed(where, result, header, first, profile, secret)
    labels = _write_labels(final, labels_out, corpus_id)
    with write_tx(conn):
        conn.execute(
            "INSERT INTO audit (ts, event, actor, outcome, data) VALUES (?, 'corpus.merged',"
            " 'os_user', 'ok', ?)",
            (to_ts(clock.now()), json.dumps({"corpus_id": corpus_id, "sha256": sha,
                                              "count": len(result.rows), "sources": ids,
                                              "labels": labels})),
        )  # fmt: skip
    export_keys.notice(conn, clock, notifier,
                       f"{len(ids)} real-mail corpora were merged into {final} ({len(result.rows)}"
                       " messages, encrypted; SPEC §16.7)")  # fmt: skip
    return {"out": str(final), "count": len(result.rows), "labels": labels,
            "passphrase": secret if own_passphrase is None else None}  # fmt: skip


def _merge_labels(paths: Sequence[Path]) -> dict[tuple[str, str], dict[str, Any]]:
    """Labels from every source's labels file, by key; two different labels for one key are
    refused and listed (R115)."""
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    clashes: list[str] = []
    for p in paths:
        f = p.with_name(p.name + LABELS_SUFFIX)
        if not f.exists():
            continue
        for line in f.read_text().splitlines():
            if not line.strip():
                continue
            row: dict[str, Any] = json.loads(line)
            key = (str(row["key"]["content_hash"]), str(row["key"]["identity_digest"]))
            have = merged.get(key)
            if have is not None and _comparable(have) != _comparable(row):
                clashes.append(key[0][:12])
                continue
            merged.setdefault(key, row)
    if clashes:
        raise InvalidInputError("different labels for the same message in two corpora: "
                                + ", ".join(sorted(set(clashes))))  # fmt: skip
    return merged


def _comparable(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k not in VOLATILE}


def _write_labels(
    corpus_path: Path, labels: dict[tuple[str, str], dict[str, Any]], corpus_id: str
) -> int:
    """Beside the merged corpus: 0600, O_EXCL then rename, rows sorted by key (R86, R90)."""
    if not labels:
        return 0
    final = corpus_path.with_name(corpus_path.name + LABELS_SUFFIX)
    tmp = final.with_name(f".{final.name}.partial")
    body = "".join(json.dumps(labels[k] | {"corpus_id": corpus_id}, sort_keys=True) + "\n"
                   for k in sorted(labels))  # fmt: skip
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(body)
        if final.exists():
            raise InvalidInputError(f"{final} already exists")
        tmp.rename(final)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return len(labels)
