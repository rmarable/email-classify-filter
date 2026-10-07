"""`ecf corpus fetch`: a real-mail test corpus (SPEC §16.7; OD-466 to OD-468; ADR 0022).

The service reads real messages from a mailbox the operator owns, read-only, and writes them to one
age-encrypted file. Each message is parsed, checked and excerpted in the OD-204 child, then
analysed as live fetch does against this install's real state; the manifest keeps those facts and
excerpts so evaluation never parses mail. The plaintext exists only in this process's memory: the
messages stream into a gzip'd tar in memory, which is encrypted whole and written with
`export_bundle.write_atomic` (0600, never over an existing file).

File layout: `MAGIC`, one line of clear JSON header (readable by `ecf corpus info` without the
passphrase, so unauthenticated until decrypt), then the age ciphertext of a tar.gz holding
`NNNNN.eml`, `manifest.jsonl` (its first line carries the clear header's sha256) and
`profile.json`. A stop, a renumbered folder or an error that retries can't clear writes what was
fetched, marked `complete: false` with the reason; nothing fetched writes nothing.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import secrets
import sqlite3
import tarfile
import threading
import uuid
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ecf import __version__
from ecf.errors import ConflictError, InvalidInputError, MailUnavailableError
from ecf_server import (
    _age,
    download_budget,
    export_bundle,
    export_keys,
    install_identity,
    internal,
    manual_export,
    passphrase,
    stepup,
)
from ecf_server.addresses import get_address, get_org_domains, secret_name
from ecf_server.analysis import MessageAnalyzer
from ecf_server.checks import SecretUnavailableError
from ecf_server.clock import Clock, to_ts
from ecf_server.db import write_tx
from ecf_server.dnscache import DnsCache
from ecf_server.facts import AddressInfo
from ecf_server.fetch import DEFAULT_SCAN, LARGE_BYTES, address_config
from ecf_server.isolate import Isolated, IsolationError, Isolator, subprocess_isolator
from ecf_server.mail.corpus_reader import (
    WINDOW,
    CorpusReader,
    LeanMeta,
    UidValidityChangedError,
    is_own,
    resolve_folder,
)
from ecf_server.message import identity_digest
from ecf_server.notify import Notifier

SUFFIX = ".ecfcorpus"
MAGIC = b"ECFCORPUS 1\n"
FORMAT = 1
ORDERS = ("most-recent", "oldest", "random")
MIB = 1024 * 1024
TOTAL, TOTAL_MAX = 500, 5000
MAX_BYTES, MAX_BYTES_MAX = 256 * MIB, 512 * MIB
CHUNK, CHUNK_RANGE = 10, (1, 100)
SLEEP_S, SLEEP_RANGE = 10, (0, 600)
DNS_BUDGET_S = 10.0  # per message, in the isolated child (OD-467)
DNS_CAP_S = 600
OVERSAMPLE = 1.2
# consecutive failures before the run stops (R131): about 4 minutes of back-off, so a network gap
# of a minute or two survives (real-service test 1 lost a run to 75 s of patience)
TRIES = 8
BACKOFF_S = (5, 10, 20, 40, 60, 60, 60)
UID_TRIES = 2  # a UID that fails this many times is skipped (R77)
ATTACHMENTS_SHOWN = 20
ONE_OFF = "corpus-oneoff"


@dataclass(frozen=True)
class Request:
    email: str
    host: str
    port: int
    folder: str  # as requested: a role, a name or "default" (R198, R203)
    out: str
    total: int = TOTAL
    chunk: int = CHUNK
    sleep_s: int = SLEEP_S
    max_bytes: int = MAX_BYTES
    order: str = "most-recent"
    include_own: bool = False
    allow_spam: bool = False
    address_id: str | None = None
    expect_folder: str | None = None  # what the preflight resolved; compared, never trusted (R123)

    def target(self, out: Path) -> dict[str, Any]:
        """The step-up target: never the password (R18), the folder as requested (R201)."""
        return {"email": self.email.lower(), "folder": self.folder or "default",
                "host": self.host.lower(), "port": self.port, "out": str(out),
                "total": self.total, "max_bytes": self.max_bytes, "order": self.order}  # fmt: skip


def check_request(req: Request, data_dir: Path) -> Path:
    """The output path when every limit is in range (OD-467); else InvalidInputError."""
    if req.order not in ORDERS:
        raise InvalidInputError(f"--order is one of {', '.join(ORDERS)}")
    for name, value, lo, hi in (
        ("--total", req.total, 1, TOTAL_MAX),
        ("--chunk", req.chunk, *CHUNK_RANGE),
        ("--sleep", req.sleep_s, *SLEEP_RANGE),
        ("--max-bytes", req.max_bytes, 1, MAX_BYTES_MAX),
    ):
        if not lo <= value <= hi:
            raise InvalidInputError(f"{name} must be between {lo} and {hi}")
    if "@" not in req.email or not req.host:
        raise InvalidInputError("give the mailbox's email and IMAP host")
    return check_out(req.out, data_dir)


def check_out(path: str, data_dir: Path) -> Path:
    """A new corpus file's path: outside the data folder, any git work tree and iCloud Drive."""
    where = manual_export.check_path(path, data_dir, suffix=SUFFIX, what="a corpus")
    if in_git_tree(where.parent):
        raise InvalidInputError("a corpus can't go inside a git work tree")
    if any(p.name == "Mobile Documents" and p.parent.name == "Library" for p in where.parents):
        raise InvalidInputError("a corpus can't go in iCloud Drive")
    return where


def in_git_tree(folder: Path) -> bool:
    """`.git` as a directory or a file (worktrees, submodules) in this folder or above (R32)."""
    return any((p / ".git").exists() for p in (folder, *folder.parents))


@stepup.purpose("corpus_fetch")
def _describe(conn: sqlite3.Connection, target: dict[str, Any]) -> stepup.Bound:
    del conn
    out = str(target.get("out", ""))
    shown = out if len(out) <= 80 else f"{out[:30]}…{out[-45:]}"
    where = _folder_words(str(target.get("folder")))
    text = (f"ecf: copy up to {target.get('total')} messages from {target.get('email')} ({where},"
            f" a mailbox you own) on {target.get('host')} into an encrypted corpus at"
            f" {shown}")  # fmt: skip
    return stepup.Bound(stepup.digest("corpus_fetch", target), text)


def _folder_words(folder: str) -> str:
    return "its default folder" if folder == "default" else f"folder {folder}"


# ------------------------------------------------------------------------------------- progress


@dataclass
class Progress:
    state: str = "idle"  # idle | running | done | stopped | failed
    fetched: int = 0
    bytes: int = 0
    total: int = 0
    skipped: dict[str, int] = field(default_factory=dict[str, int])
    reason: str = ""
    retrying: str = ""  # shown while the run waits to reconnect
    out: str = ""
    started_at: str | None = None
    ended_at: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def snapshot(self) -> dict[str, Any]:
        """Never the passphrase or any message content (R105)."""
        with self.lock:
            return {"state": self.state, "fetched": self.fetched, "bytes": self.bytes,
                    "total": self.total, "skipped": dict(self.skipped), "reason": self.reason,
                    "retrying": self.retrying, "out": self.out, "started_at": self.started_at,
                    "ended_at": self.ended_at}  # fmt: skip

    def set(self, **kw: Any) -> None:
        with self.lock:
            for k, v in kw.items():
                setattr(self, k, v)

    def skip(self, why: str) -> None:
        with self.lock:
            self.skipped[why] = self.skipped.get(why, 0) + 1


RUN = Progress()
STOP = threading.Event()
Spawn = Callable[[Callable[[], None]], None]


def _thread(work: Callable[[], None]) -> None:
    threading.Thread(target=work, name="ecf-corpus-fetch", daemon=True).start()


def stop() -> dict[str, Any]:
    STOP.set()
    return RUN.snapshot()


# ------------------------------------------------------------------------------------- preflight


def budget_share(
    conn: sqlite3.Connection, clock: Clock, req: Request, *, gmail: bool
) -> int | None:
    """S = floor(L0 / 2), fixed at the start (R64); None where there is no budget."""
    if req.address_id is not None:
        left = download_budget.left(conn, clock, req.address_id, gmail=gmail)
    else:
        left = download_budget.left_email(conn, clock, req.email, gmail=gmail)
    return None if left is None else left // 2


def preflight(
    conn: sqlite3.Connection, clock: Clock, req: Request, reader: CorpusReader
) -> dict[str, Any]:
    """Log in, resolve the folder, EXAMINE it and look at the first window (R92, R127): an
    estimate the CLI shows before step-up. The fetch call works all of this out again."""
    reader.open()
    name = resolve_folder(reader.folders(), req.folder, gmail=reader.gmail,
                          allow_spam=req.allow_spam)  # fmt: skip
    state = reader.examine(name)
    if state.exists == 0:
        raise InvalidInputError(f"{name} is empty")
    share = budget_share(conn, clock, req, gmail=reader.gmail)
    empty: list[LeanMeta] = []
    first = next(_windows(reader, req), empty)
    sizes = [m.size for m in first[: req.total] if m.size <= LARGE_BYTES]
    cap = req.max_bytes if share is None else min(req.max_bytes, share)
    fits, used = 0, 0
    for size in sizes:
        if used + size > cap:
            break
        fits, used = fits + 1, used + size
    batches = math.ceil(req.total / req.chunk)
    return {"folder": name, "exists": state.exists, "gmail": reader.gmail,
            "candidates": len(first), "sample_bytes": sum(sizes), "fit_in_cap": fits,
            "byte_cap": cap, "budget_share": share, "batches": batches,
            "estimate_s": int(req.total * (req.sleep_s / req.chunk + 1))}  # fmt: skip


# ------------------------------------------------------------------------------------- selection


def _windows(reader: CorpusReader, req: Request) -> Iterator[list[LeanMeta]]:
    """Candidates in the order the run takes them, one batch at a time (R12, R62, R149, R160)."""
    f = reader.folder
    if req.order == "random":
        uids = reader.all_uids()
        rng = secrets.SystemRandom()
        order = list(range(len(uids)))
        rng.shuffle(order)
        k = max(1, math.ceil(req.total * OVERSAMPLE))
        for i in range(0, len(order), k):
            picked = [uids[j] for j in order[i : i + k]]
            meta = reader.meta(picked)
            yield [meta[u] for u in picked if u in meta]
        return
    density = max(f.exists, 1) / max(f.uidnext - 1, 1)
    width = min(WINDOW, max(100, math.ceil(2 * req.total / density)))
    newest = req.order == "most-recent"
    lo_hi = (
        ((max(1, hi - width + 1), hi) for hi in range(f.uidnext - 1, 0, -width))
        if newest
        else ((lo, min(lo + width - 1, f.uidnext - 1)) for lo in range(1, f.uidnext, width))
    )
    for lo, hi in lo_hi:
        meta = reader.meta_range(lo, hi)
        yield sorted(meta.values(), key=lambda m: (m.internaldate, m.uid), reverse=newest)


# ------------------------------------------------------------------------------------- the run


@dataclass
class Output:
    """The tar.gz grows in memory as messages arrive; raw bytes are dropped once added."""

    buf: io.BytesIO = field(default_factory=io.BytesIO)
    rows: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    keys: set[tuple[str, str]] = field(default_factory=set[tuple[str, str]])
    none_reasons: Counter[str] = field(default_factory=Counter[str])
    tar: tarfile.TarFile = field(init=False)

    def __post_init__(self) -> None:
        self.tar = tarfile.open(fileobj=self.buf, mode="w|gz")  # noqa: SIM115 - closed in seal

    def add(self, name: str, data: bytes) -> None:
        info = tarfile.TarInfo(name)
        info.size, info.mode = len(data), 0o600
        self.tar.addfile(info, io.BytesIO(data))


def start(  # noqa: PLR0913 - the service's collaborators, then the request
    connect: Callable[[], sqlite3.Connection],
    clock: Clock,
    notifier: Notifier,
    data_dir: Path,
    req: Request,
    reader_factory: Callable[[], CorpusReader],
    secret: str,
    *,
    isolator: Isolator | None = None,
    sleep: Callable[[float], bool] | None = None,
    spawn: Spawn = _thread,
) -> dict[str, Any]:
    """Start the fetch in a thread (`spawn` runs it inline in tests); refuse a second one."""
    with RUN.lock:
        if RUN.state == "running":
            raise ConflictError("a corpus fetch is already running; see `ecf corpus status`")
        RUN.state, RUN.fetched, RUN.bytes, RUN.total = "running", 0, 0, req.total
        RUN.skipped, RUN.reason, RUN.out, RUN.retrying = {}, "", "", ""
        RUN.started_at, RUN.ended_at = to_ts(clock.now()), None
    STOP.clear()
    pause = sleep or STOP.wait

    def work() -> None:
        conn = connect()
        reader = reader_factory()
        try:
            run(conn, clock, notifier, data_dir, req, reader, secret, RUN,
                isolator=isolator, sleep=pause)  # fmt: skip
        except Exception as exc:  # reported by type only, never raised into the thread
            RUN.set(state="failed", reason=type(exc).__name__, ended_at=to_ts(clock.now()))
        finally:
            reader.close()
            conn.close()

    spawn(work)
    return RUN.snapshot()


def run(  # noqa: PLR0912, PLR0913, PLR0915 - one loop over the selection, as fetch's
    conn: sqlite3.Connection,
    clock: Clock,
    notifier: Notifier,
    data_dir: Path,
    req: Request,
    reader: CorpusReader,
    secret: str,
    progress: Progress,
    *,
    isolator: Isolator | None = None,
    sleep: Callable[[float], bool] = lambda _s: False,
) -> Path | None:
    where = check_request(req, data_dir)
    reader.open()
    name = resolve_folder(reader.folders(), req.folder, gmail=reader.gmail,
                          allow_spam=req.allow_spam)  # fmt: skip
    if req.expect_folder is not None and name != req.expect_folder:
        raise InvalidInputError(f"--folder now names {name}, not {req.expect_folder}; run again")
    folder = reader.examine(name)
    gmail = reader.gmail
    share = budget_share(conn, clock, req, gmail=gmail)
    cap = req.max_bytes if share is None else min(req.max_bytes, share)
    mine = (install_identity.install_id(conn), install_identity.generation(conn))
    analyzer, scan, profile = _analysis(conn, clock, req, gmail)
    iso = isolator or _isolator(conn)
    out, reason, in_chunk = Output(), "", 0
    tries = _Tries()
    try:
        for batch in _windows(reader, req):
            for meta in batch:
                if len(out.rows) >= req.total:
                    break
                if meta.size > LARGE_BYTES:
                    progress.skip("large")
                    continue
                if is_own(meta, source=req.email, mine=mine, include_own=req.include_own):
                    progress.skip("own")
                    continue
                if progress.bytes + meta.size > cap:
                    reason = "byte_cap"
                    break
                left = _left(conn, clock, req, gmail)
                if left is not None and meta.size > left:
                    reason = "download_budget"
                    break
                got_raw = _fetch(reader, meta.uid, tries, sleep,
                                 lambda text: progress.set(retrying=text))  # fmt: skip
                if isinstance(got_raw, str):
                    if got_raw == "timeout":
                        progress.skip("timeout")
                        continue
                    reason = got_raw
                    break
                raw = got_raw
                if raw is None:
                    progress.skip("gone")
                    continue
                _record(conn, clock, req, len(raw))
                progress.set(bytes=progress.bytes + len(raw))
                try:
                    got = iso(raw, scan)
                except IsolationError:
                    progress.skip("facts")
                    continue
                key = (got.parsed.content_hash, identity_digest(got.parsed))
                if key in out.keys:
                    progress.skip("duplicate")
                    continue
                facts = analyzer.analyze(got.parsed, raw, auth=got.auth,
                                         gmail_labels=meta.labels if gmail else None)  # fmt: skip
                index = len(out.rows) + 1
                out.add(f"{index:05d}.eml", raw)
                out.rows.append(_row(index, meta, folder.uidvalidity, name, raw, key, got, facts,
                                     clock))  # fmt: skip
                out.keys.add(key)
                if got.auth.result == "none":
                    out.none_reasons[got.auth.reason[:60]] += 1
                del raw
                progress.set(fetched=len(out.rows))
                in_chunk += 1
                if STOP.is_set():
                    reason = "stopped"
                    break
                if in_chunk >= req.chunk:
                    in_chunk = 0
                    if sleep(req.sleep_s):
                        reason = "stopped"
                        break
                    try:
                        reader.noop()
                    except UidValidityChangedError:
                        raise
                    except MailUnavailableError:
                        pass  # the next fetch reconnects, with its back-off (R136)
            if reason or len(out.rows) >= req.total:
                break
        else:
            reason = "folder_exhausted" if len(out.rows) < req.total else ""
    except UidValidityChangedError:
        reason = "uidvalidity_changed"
    except MailUnavailableError:
        reason = "mail_unavailable"
    complete = reason in ("", "folder_exhausted", "byte_cap", "download_budget")
    if not out.rows:
        out.tar.close()
        progress.set(state="stopped" if reason == "stopped" else "failed", retrying="",
                     reason=reason or "nothing_fetched", ended_at=to_ts(clock.now()))  # fmt: skip
        return None
    final = _seal(conn, clock, notifier, req, where, out, profile, secret, name=name,
                  complete=complete, reason=reason,
                  skipped=progress.snapshot()["skipped"])  # fmt: skip
    progress.set(state="done" if complete else "stopped", reason=reason, out=str(final),
                 retrying="", ended_at=to_ts(clock.now()))  # fmt: skip
    return final


@dataclass
class _Tries:
    """Consecutive failures across UIDs; reset by any success (R131)."""

    failures: int = 0


def _fetch(
    reader: CorpusReader,
    uid: int,
    tries: _Tries,
    sleep: Callable[[float], bool],
    note: Callable[[str], None] = lambda _s: None,
) -> bytes | str | None:
    """The message, None when it's gone, "timeout" when this UID failed UID_TRIES times (R77),
    or why the run stops: "mail_unavailable" after TRIES failures in a row, or "stopped".
    A changed UIDVALIDITY propagates (R25)."""
    for _ in range(UID_TRIES):
        try:
            raw = reader.fetch(uid)
        except UidValidityChangedError:
            raise
        except MailUnavailableError:
            tries.failures += 1
        else:
            tries.failures = 0
            note("")
            return raw
        while True:  # back off, then reconnect; a failed reconnect counts too (R130)
            if tries.failures >= TRIES:
                return "mail_unavailable"
            note(f"reconnecting (attempt {tries.failures} of {TRIES - 1})")
            if sleep(BACKOFF_S[min(tries.failures, len(BACKOFF_S)) - 1]):
                return "stopped"
            try:
                reader.reconnect()
                break
            except UidValidityChangedError:
                raise
            except MailUnavailableError:
                tries.failures += 1
    return "timeout"


def _analysis(
    conn: sqlite3.Connection, clock: Clock, req: Request, gmail: bool
) -> tuple[MessageAnalyzer, int, dict[str, Any]]:
    """The analyzer against this install's real state (R97), the scan limit (R152) and the
    source profile (R49). A one-off mailbox gets no history, vendors or reuse (R167)."""
    dns = DnsCache(conn, clock)  # unused: the child already checked authentication
    if req.address_id is not None:
        cfg = address_config(conn, req.address_id)
        analyzer = MessageAnalyzer.for_address(conn, clock, req.address_id, dns)
        scan, sensitivity = cfg.max_scan_bytes, cfg.sensitivity
    else:
        analyzer = MessageAnalyzer(conn, clock, AddressInfo(ONE_OFF, req.email, "standard"), dns)
        scan, sensitivity = DEFAULT_SCAN, "standard"
    profile = {"email": req.email.lower(), "provider": "gmail" if gmail else "imap",
               "sensitivity": sensitivity, "org_domains": get_org_domains(conn),
               "org_addresses": [{"address": a.address, "name": a.name}
                                 for a in internal.org_addresses(conn)],
               "source": "address" if req.address_id else "one_off"}  # fmt: skip
    return analyzer, scan, profile


def _isolator(conn: sqlite3.Connection) -> Isolator:
    row = conn.execute("PRAGMA database_list").fetchone()
    path = row["file"] if row else ""
    if not path:
        raise InvalidInputError("a corpus fetch needs the service's database file")
    return subprocess_isolator(Path(path), dns_cap_s=DNS_CAP_S, dns_budget=lambda: DNS_BUDGET_S)


def _left(conn: sqlite3.Connection, clock: Clock, req: Request, gmail: bool) -> int | None:
    if req.address_id is not None:
        return download_budget.left(conn, clock, req.address_id, gmail=gmail)
    return download_budget.left_email(conn, clock, req.email, gmail=gmail)


def _record(conn: sqlite3.Connection, clock: Clock, req: Request, nbytes: int) -> None:
    """At once, in one table only (R14, R182)."""
    if req.address_id is not None:
        download_budget.record(conn, clock, req.address_id, nbytes)
    else:
        download_budget.record_email(conn, clock, req.email, nbytes)


def _row(  # noqa: PLR0913, PLR0917 - one manifest row from what the loop holds
    index: int,
    meta: LeanMeta,
    uidvalidity: int,
    folder: str,
    raw: bytes,
    key: tuple[str, str],
    got: Isolated,
    facts: dict[str, Any],
    clock: Clock,
) -> dict[str, Any]:
    p = got.parsed
    return {
        "index": index, "uid": meta.uid, "uidvalidity": uidvalidity, "folder": folder,
        "internaldate": meta.internaldate.isoformat(), "size": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "key": {"content_hash": key[0], "identity_digest": key[1]},
        "message_id": p.message_id, "gmail_msgid": meta.gmail_msgid,
        "gmail_labels": sorted(meta.labels), "fetched_at": to_ts(clock.now()), "facts": facts,
        "excerpts": {"classifier": got.excerpts[0], "actor": got.excerpts[1]},
        "unredacted": {"classifier": got.plain[0], "actor": got.plain[1]},
        "display": {"from": p.from_name + (f" <{p.from_addr}>" if p.from_addr else ""),
                    "reply_to": list(p.reply_to), "to": list(p.to), "subject": p.subject,
                    "date": (p.headers.get("date") or ("",))[0],
                    "attachments": [a.name for a in p.attachments][:ATTACHMENTS_SHOWN]},
    }  # fmt: skip


def _seal(  # noqa: PLR0913
    conn: sqlite3.Connection,
    clock: Clock,
    notifier: Notifier,
    req: Request,
    where: Path,
    out: Output,
    profile: dict[str, Any],
    secret: str,
    *,
    name: str,
    complete: bool,
    reason: str,
    skipped: dict[str, int],
) -> Path:
    corpus_id = uuid.uuid4().hex
    preset = None
    if req.address_id is not None:
        row = conn.execute("SELECT preset FROM addresses WHERE address_id = ?",
                           (req.address_id,)).fetchone()  # fmt: skip
        preset = row["preset"] if row else None
    header = {"format": FORMAT, "corpus_id": corpus_id, "created_at": to_ts(clock.now()),
              "ecf_version": __version__, "source_domain": internal.split(req.email)[1],
              "folder": req.folder or "default", "order": req.order, "count": len(out.rows),
              "bytes": sum(r["size"] for r in out.rows), "skipped": skipped,
              "complete": complete, "reason": reason, "source_preset": preset,
              "none_reasons": dict(out.none_reasons)}  # fmt: skip
    first = {"corpus_id": corpus_id, "install_id": install_identity.install_id(conn),
             "folder": name}  # fmt: skip
    final, sha = write_sealed(where, out, header, first, profile, secret)
    mailbox = hashlib.sha256(internal.fold(req.email).encode()).hexdigest()[:12]
    source: dict[str, Any] = (
        {"address_id": req.address_id}
        if req.address_id
        else {"mailbox": mailbox, "domain": internal.split(req.email)[1]}
    )
    now = to_ts(clock.now())
    with write_tx(conn):
        conn.execute(
            "INSERT INTO audit (ts, event, actor, outcome, data) VALUES (?, 'corpus.fetched',"
            " 'os_user', 'ok', ?)",
            (now, json.dumps({"corpus_id": corpus_id, "sha256": sha, "count": len(out.rows),
                              "bytes": header["bytes"], "order": req.order,
                              "complete": complete, "reason": reason, "skipped": skipped,
                              **source})),
        )  # fmt: skip
    export_keys.notice(conn, clock, notifier,
                       f"A real-mail corpus of {len(out.rows)} messages from {req.email} was"
                       f" written to {final} (encrypted; SPEC §16.7)")  # fmt: skip
    return final


def write_sealed(
    where: Path,
    out: Output,
    header: dict[str, Any],
    first: dict[str, Any],
    profile: dict[str, Any],
    secret: str,
) -> tuple[Path, str]:
    """Close the tar with its manifest and profile, encrypt it whole and write the file (0600,
    never over an existing one); returns the path and the file's sha256. The manifest's first line
    carries the clear header's sha256 (R91)."""
    clear = json.dumps(header, sort_keys=True).encode()
    first = {"header_sha256": hashlib.sha256(clear).hexdigest(), **first}
    lines = [json.dumps(first, sort_keys=True), *(json.dumps(r, sort_keys=True) for r in out.rows)]
    out.add("manifest.jsonl", ("\n".join(lines) + "\n").encode())
    out.add("profile.json", json.dumps(profile, sort_keys=True).encode())
    out.tar.close()
    plaintext = out.buf.getvalue()
    out.buf.close()
    sealed = MAGIC + clear + b"\n" + _age.encrypt_passphrase(plaintext, secret)
    del plaintext
    final = export_bundle.write_atomic(where.parent, where.name, sealed)
    return final, hashlib.sha256(sealed).hexdigest()


def new_passphrase(own: str | None) -> str:
    """The passphrase: six generated words, or the operator's own under the stronger corpus rule
    (R17): at least 5 words of 3 or more characters and at least 24 characters."""
    if own is None:
        return passphrase.generate()
    passphrase.check(own)
    words = [w for w in own.split() if len(w) >= 3]
    if len(own) < 24 or len(set(words)) < 5:
        raise InvalidInputError(
            "too weak for a corpus: use at least 5 words of 3 or more letters and 24"
            " characters, or the passphrase ecf offers"
        )
    return own


# ------------------------------------------------------------------------------------- reading


@dataclass(frozen=True)
class Corpus:
    header: dict[str, Any]
    first: dict[str, Any]  # manifest line 1: header hash, corpus_id, install_id, folder
    rows: list[dict[str, Any]]
    profile: dict[str, Any]
    tar_gz: bytes  # members are read one at a time (R135)

    def message(self, index: int) -> bytes:
        with tarfile.open(fileobj=io.BytesIO(self.tar_gz), mode="r:gz") as tar:
            f = tar.extractfile(f"{index:05d}.eml")
            if f is None:
                raise InvalidInputError(f"message {index} isn't in this corpus")
            return f.read()


def read_header(path: Path) -> dict[str, Any]:
    """The clear header, without the passphrase: unverified until the file is decrypted (R119)."""
    with path.open("rb") as f:
        if f.read(len(MAGIC)) != MAGIC:
            raise InvalidInputError(f"{path} isn't an ecf corpus")
        line = f.readline(1 << 20)
    try:
        header: dict[str, Any] = json.loads(line)
    except ValueError:
        raise InvalidInputError(f"{path} has a damaged header") from None
    return header


def open_corpus(path: Path, secret: str) -> Corpus:
    """Decrypt and check the file: the manifest's header hash must match the clear header
    (R91), and every message's sha256 its manifest row."""
    data = path.read_bytes()
    if not data.startswith(MAGIC):
        raise InvalidInputError(f"{path} isn't an ecf corpus")
    clear, _, cipher = data[len(MAGIC) :].partition(b"\n")
    del data
    try:
        tar_gz = _age.decrypt_passphrase(cipher, secret)
    except _age.AgeError:
        raise InvalidInputError("wrong passphrase, or the file is damaged") from None
    sums: dict[str, str] = {}
    files: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(tar_gz), mode="r|gz") as tar:  # one pass
        for m in tar:
            f = tar.extractfile(m)
            if f is None:
                continue
            body = f.read()
            if m.name.endswith(".eml"):
                sums[m.name] = hashlib.sha256(body).hexdigest()
            else:
                files[m.name] = body
    if "manifest.jsonl" not in files or "profile.json" not in files:
        raise InvalidInputError("the corpus has no manifest")
    manifest = files["manifest.jsonl"].decode().splitlines()
    first: dict[str, Any] = json.loads(manifest[0])
    if first.get("header_sha256") != hashlib.sha256(clear).hexdigest():
        raise InvalidInputError("the corpus header doesn't match its manifest; refused")
    rows: list[dict[str, Any]] = [json.loads(line) for line in manifest[1:] if line]
    for row in rows:
        if sums.get(f"{row['index']:05d}.eml") != row["sha256"]:
            raise InvalidInputError(f"message {row['index']} doesn't match its manifest row")
    profile: dict[str, Any] = json.loads(files["profile.json"])
    return Corpus(json.loads(clear), first, rows, profile, tar_gz)


# ------------------------------------------------------------------------------------- the routes


def from_body(
    conn: sqlite3.Connection, body: dict[str, Any], secret: Callable[[str], str | None]
) -> tuple[Request, Callable[[], str]]:
    """The request and its password from a route body. With `address`, a watched address and its
    Keychain password; else a one-off email, host and `app_password`, never stored. Either way
    the operator types the mailbox's address to say it's theirs (R45, R103)."""

    def num(key: str, default: int) -> int:
        v = body.get(key, default)
        if not isinstance(v, int) or isinstance(v, bool):
            raise InvalidInputError(f"{key} must be a whole number")
        return v

    common: dict[str, Any] = {
        "folder": str(body.get("folder") or "default"), "out": str(body.get("out") or ""),
        "total": num("total", TOTAL), "chunk": num("chunk", CHUNK),
        "sleep_s": num("sleep_s", SLEEP_S), "max_bytes": num("max_bytes", MAX_BYTES),
        "order": str(body.get("order") or "most-recent"),
        "include_own": body.get("include_own") is True,
        "allow_spam": body.get("allow_spam") is True,
        "expect_folder": expect if isinstance(expect := body.get("expect_folder"), str) else None,
    }  # fmt: skip
    ref = body.get("address")
    if isinstance(ref, str) and ref:
        a = get_address(conn, ref)
        aid, email = str(a["address_id"]), str(a["email"])
        req = Request(email=email, host=str(a["imap_host"] or ""), port=993, address_id=aid,
                      **common)  # fmt: skip

        def keychain() -> str:
            pw = secret(secret_name(aid))
            if not pw:
                raise SecretUnavailableError(f"no app password stored for {aid}")
            return pw

        password: Callable[[], str] = keychain
    else:
        email, host = str(body.get("email") or ""), str(body.get("host") or "")
        pw = body.get("app_password")
        if not isinstance(pw, str) or not pw:
            raise InvalidInputError("give the app password, or --address for a watched one")
        watched = [str(r[0]) for r in conn.execute(
            "SELECT address_id, email FROM addresses WHERE removed_at IS NULL")
            if internal.fold(str(r[1])) == internal.fold(email)]  # fmt: skip
        if watched:
            raise InvalidInputError(f"{email} is watched here: use --address {watched[0]}")
        req = Request(email=email, host=host, port=num("port", 993), **common)

        def typed() -> str:
            return pw

        password = typed
    owner = body.get("owner_email")
    if not isinstance(owner, str) or internal.fold(owner) != internal.fold(req.email):
        raise InvalidInputError(
            "the confirmation didn't match: type the mailbox's email address exactly (such as"
            " pat@example.com) to confirm it's a mailbox you own"
        )
    return req, password


def begin(  # noqa: PLR0913 - the service's collaborators, then the request
    connect: Callable[[], sqlite3.Connection],
    clock: Clock,
    notifier: Notifier,
    data_dir: Path,
    req: Request,
    password: Callable[[], str],
    *,
    nonce: str | None,
    own_passphrase: str | None,
    reader_factory: Callable[[Request, Callable[[], str]], CorpusReader] | None = None,
    spawn: Spawn = _thread,
) -> dict[str, Any]:
    """`POST /v1/corpus/fetch`: step-up for this exact request before anything touches the mailbox,
    then the passphrase, then the job (R175, R198). The generated passphrase is in this response
    only, before any byte is fetched (R105)."""
    conn = connect()
    try:
        where = check_request(req, data_dir)
        stepup.consume(conn, clock, "corpus_fetch", req.target(where), nonce)
    finally:
        conn.close()
    secret = new_passphrase(own_passphrase)
    make = reader_factory or _reader
    got = start(connect, clock, notifier, data_dir, req, lambda: make(req, password), secret,
                spawn=spawn)  # fmt: skip
    return got | {"passphrase": secret if own_passphrase is None else None}


def _reader(req: Request, password: Callable[[], str]) -> CorpusReader:
    return CorpusReader(req.host, req.email, password, port=req.port)


def info(path: str) -> dict[str, Any]:
    """`ecf corpus info`: the clear header (unverified) and how many labels sit beside it."""
    p = Path(path).expanduser()
    if p.suffix != SUFFIX or not p.is_file():
        raise InvalidInputError(f"no corpus file at {p}")
    labels = p.with_name(p.name + ".labels.jsonl")
    count = sum(1 for line in labels.read_text().splitlines() if line) if labels.exists() else 0
    return {"header": read_header(p), "verified": False, "labels": count}
