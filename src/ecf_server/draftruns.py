"""The draft eval's run handling (M8 plan §4 "Incomplete runs" and "Run-handling invariants";
Step 5): the registration record, its slots and attempts, and each run's journal.

- **The record** is one 0600 JSON file per install (`<data>/evals/draft-runs.json`, operator
  decision 2026-10-28: no migration), replaced whole on each write (temporary file, fsync,
  rename). It holds each draft run's registration (arm, kind, slot, attempt, heartbeat, how it
  ended), the checker digest frozen at the pilot and the operator's confirmation of the set.
- **Slots** (AM3, AN5, AO3): before the set is confirmed, only the baseline arm's two pilot
  slots, counted across every version of the set; after it, `run-1`, `run-2` (forced) and
  `natural` for each arm on the confirmed digest, in that order. A slot is filled by a complete
  attempt, or ended by two incomplete ones; after one incomplete attempt it waits for the
  operator's `--new-attempt`.
- **An attempt** is a registered run plus the runs that resumed it after a restart (AT6). It
  ends 48 hours after its first registration, or at its second restart while claims were out
  (AR6, AS5).
- **The journal** (AK3) is one 0600 JSONL file per run, appended and fsynced per event: claims
  before they are handed out, first reads, claim ends, classifications, each scored case with
  its draft record, repeats. A truncated last line is ignored. It holds draft text and is read
  only by the service.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ecf.errors import ConflictError, InvalidInputError

REGISTRY = "draft-runs.json"
JOURNAL_SUFFIX = ".journal.jsonl"
FORMAT = 1
ATTEMPT_LIMIT = timedelta(hours=48)  # from the attempt's first registration (AR6)
RESTART_LIMIT = 2  # counted restarts (claims out) end the attempt (AS5)
HEARTBEAT_MARGIN = timedelta(minutes=2)  # the minute timer's gap, with room for load (AQ6)
ATTEMPTS_MAX = 2  # the registered attempt and one the operator allows (AL4)
PILOT_SLOTS = {"forced": ("pilot-forced",), "natural": ("pilot-natural",)}
SLOTS = {"forced": ("run-1", "run-2"), "natural": ("natural",)}
Kind = Literal["forced", "natural"]
End = Literal["done", "stopped", "expired", "lost", "failed"]
_STRICT = ConfigDict(extra="forbid")


class Registration(BaseModel):
    """One draft run as registered, and how it ended."""

    model_config = _STRICT
    run_id: str
    kind: Kind
    slot: str
    attempt: int
    arm: str  # `arm_key` of its models and prompt digests
    baseline: bool  # on the pins in force at registration (today's actor)
    models: dict[str, str]
    actor_prompt_digest: str
    set_digest: str
    root: str
    options: dict[str, Any]  # `claude_eval.Options` as JSON, to resume it
    pinned: bool
    key: str
    schema_digest: str
    checker_digest: str
    registered_at: datetime
    expires_at: datetime
    attempt_started_at: datetime  # its attempt's first registration
    heartbeat_at: datetime
    resumed_from: str | None = None  # the run lost to a restart that this one carries on
    resumed_by: str | None = None
    restarts: int = 0  # restarts with claims out, counted over the attempt so far
    new_attempt_reason: str | None = None
    supersedes: str | None = None  # an expired run `register` ended and saved first (AM3)
    card_digests: dict[str, str] = Field(default_factory=dict[str, str])  # pilot runs (AP5)
    claude_versions: list[str] = Field(default_factory=list[str])
    ended_at: datetime | None = None
    end: End | None = None
    end_detail: str = ""
    stop_reason: str | None = None
    complete: bool | None = None  # every case of the slot scored
    attempt_over: bool = False  # lost, and not resumed: the attempt ended incomplete

    @property
    def open(self) -> bool:
        return self.end is None


class Confirmation(BaseModel):
    model_config = _STRICT
    set_digest: str
    at: datetime
    changed: list[str]  # cards changed since the pilot, each a pilot classifier-stage miss


class Registry(BaseModel):
    model_config = _STRICT
    format: int = FORMAT
    frozen_checker: str | None = None  # recorded on the first pilot run (AP5, AQ7)
    confirmation: Confirmation | None = None
    runs: list[Registration] = Field(default_factory=list[Registration])

    def get(self, run_id: str) -> Registration | None:
        return next((r for r in self.runs if r.run_id == run_id), None)

    def put(self, reg: Registration) -> None:
        self.runs = [r for r in self.runs if r.run_id != reg.run_id] + [reg]


def folder(data_dir: Path) -> Path:
    return data_dir / "evals"


def load(data_dir: Path) -> Registry:
    path = folder(data_dir) / REGISTRY
    if not path.is_file():
        return Registry()
    reg = Registry.model_validate_json(path.read_bytes())
    if reg.format != FORMAT:
        raise ConflictError(f"{path} is in another format ({reg.format}); this ecf reads {FORMAT}")
    return reg


def save(data_dir: Path, reg: Registry) -> None:
    """Replace the record whole: a 0600 temporary file, fsynced, renamed over it, and the folder
    fsynced, so a crash leaves the old record or the new one."""
    d = folder(data_dir)
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = d / f"{REGISTRY}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(reg.model_dump_json(indent=1))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, d / REGISTRY)
    dfd = os.open(d, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def arm_key(models: dict[str, str], actor_prompt: str, options: dict[str, Any]) -> str:
    """An arm: the models of the roles it asks and its prompt digests, plus the options that change
    what the models see (preset, sensitivity, window)."""
    keyed = {k: options.get(k) for k in ("preset", "sensitivity", "classifier_window")}
    keyed |= {"models": models, "actor_prompt": actor_prompt}
    return hashlib.sha256(json.dumps(keyed, sort_keys=True).encode()).hexdigest()[:16]


def card_digest(eml: bytes, expected: dict[str, Any]) -> str:
    """One card as built: its message and its labels."""
    doc = {"eml": hashlib.sha256(eml).hexdigest(), "expected": expected}
    return hashlib.sha256(json.dumps(doc, sort_keys=True, default=str).encode()).hexdigest()


def shuffle_seed(set_digest: str, arm: str, slot: str) -> int:
    """Every attempt in a slot runs its cases in the same order (AQ9)."""
    return int(hashlib.sha256(f"{set_digest}|{arm}|{slot}".encode()).hexdigest()[:16], 16)


# Slots and attempts: which slot a new registration takes, and whether it may.


def _slot_runs(reg: Registry, arm: str, slot: str, digest: str | None) -> list[Registration]:
    """A slot's runs, oldest first; a pilot slot counts across every version of the set."""
    return [r for r in reg.runs if r.arm == arm and r.slot == slot
            and (digest is None or r.set_digest == digest)]  # fmt: skip


def attempt_state(runs: list[Registration]) -> str:
    """An attempt's state from its runs: open, complete, incomplete, or lost (resumable at the
    next start)."""
    last = runs[-1]
    if last.open:
        return "open"
    if last.complete:
        return "complete"
    if last.end == "lost" and not last.attempt_over:
        return "lost"
    return "incomplete"


def slot_state(runs: list[Registration]) -> tuple[str, int]:
    """empty, open, filled, waiting (one incomplete attempt: the operator decides) or ended (the
    attempts allowed all incomplete), and how many attempts it has had."""
    if not runs:
        return "empty", 0
    attempts = sorted({r.attempt for r in runs})
    states = [attempt_state([r for r in runs if r.attempt == a]) for a in attempts]
    if "complete" in states:
        return "filled", len(attempts)
    if states[-1] in ("open", "lost"):
        return "open", len(attempts)
    return ("ended" if len(attempts) >= ATTEMPTS_MAX else "waiting"), len(attempts)


def assign(reg: Registry, *, arm: str, baseline: bool, kind: Kind, digest: str,
           new_attempt: str | None) -> tuple[str, int, str | None]:  # fmt: skip
    """The slot and attempt a new draft run takes, and the set digest its slot is keyed by (None
    for a pilot slot); refused, naming why."""
    if reg.confirmation is None:
        if not baseline:
            raise InvalidInputError("before the set is confirmed only the pilot runs: the"
                                    " baseline arm (the pinned actor), one forced and one"
                                    " natural")  # fmt: skip
        if other := next((r for r in reg.runs if r.slot.startswith("pilot-") and r.arm != arm),
                         None):  # fmt: skip
            raise InvalidInputError(f"the pilots run on one arm: the baseline's models or actor"
                                    f" instructions changed since pilot run {other.run_id[:8]}"
                                    " (5B-3). Restore the pins and ecf version the pilot ran on;"
                                    " starting the pilot over on the new ones is the operator's"
                                    " decision")  # fmt: skip
        names, keyed = PILOT_SLOTS[kind], None
    else:
        if digest != reg.confirmation.set_digest:
            raise InvalidInputError(f"the set was confirmed at {reg.confirmation.set_digest[:16]};"
                                    f" this set is {digest[:16]}: a run on another version of the"
                                    " set is refused")  # fmt: skip
        names, keyed = SLOTS[kind], digest
    for name in names:
        state, n = slot_state(_slot_runs(reg, arm, name, keyed))
        if state in ("filled", "ended"):
            continue
        if state == "open":
            raise ConflictError(f"slot {name} of this arm has a run in progress or waiting to"
                                " resume")  # fmt: skip
        if state == "waiting":
            if new_attempt is None:
                raise InvalidInputError(f"slot {name}'s attempt ended incomplete: the operator may"
                                        " allow one new attempt (--new-attempt REASON); it runs"
                                        " only the cases still unscored")  # fmt: skip
            return name, n + 1, keyed
        if new_attempt is not None:
            raise InvalidInputError(f"--new-attempt is for a slot whose attempt ended incomplete;"
                                    f" slot {name} has none")  # fmt: skip
        return name, 1, keyed
    which = "pilot " if keyed is None else ""
    raise InvalidInputError(f"every {which}slot for {kind} runs of this arm is filled or ended"
                            + ("; the set's confirmation opens the others" if keyed is None
                               else ""))  # fmt: skip


def slot_of(reg: Registry, r: Registration) -> list[Registration]:
    """Every run of `r`'s slot, oldest first."""
    pilot = r.slot.startswith("pilot-")
    return _slot_runs(reg, r.arm, r.slot, None if pilot else r.set_digest)


# The journal: one event per line, appended and fsynced, read back without a truncated last line.


def journal_path(data_dir: Path, run_id: str) -> Path:
    return folder(data_dir) / f"{run_id}{JOURNAL_SUFFIX}"


def append(path: Path, event: dict[str, Any]) -> None:
    append_many(path, [event])


def append_many(path: Path, events: list[dict[str, Any]]) -> None:
    """Append events in one write and one fsync. A last line a crash cut short is cut off first,
    so the next event never joins it into a bad line mid-file (diff review 5B-1); a short write
    is finished. The fsync covers a crash of the service process; macOS doesn't flush the drive's
    cache on it, so a power cut can still lose the newest events (5B-12)."""
    if not events:
        return
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    data = b"".join((json.dumps(e, sort_keys=True, default=str) + "\n").encode() for e in events)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        _cut_partial(fd)
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view) :]
        os.fsync(fd)
    finally:
        os.close(fd)


def _cut_partial(fd: int) -> None:
    """Truncate the file to its last newline when it doesn't end in one."""
    size = os.fstat(fd).st_size
    if not size or os.pread(fd, 1, size - 1) == b"\n":
        return
    end = size
    while end > 0:
        start = max(0, end - 4096)
        chunk = os.pread(fd, end - start, start)
        nl = chunk.rfind(b"\n")
        if nl >= 0:
            os.ftruncate(fd, start + nl + 1)
            return
        end = start
    os.ftruncate(fd, 0)


def read(path: Path) -> list[dict[str, Any]]:
    """The journal's events; a last line with no newline after it was cut short by a crash and
    is left out, as the next append cuts it (delta 3); any other bad line is an error."""
    if not path.is_file():
        return []
    raw = path.read_bytes().split(b"\n")
    raw = raw[:-1]  # after the last newline: empty, or a line a crash cut short
    out: list[dict[str, Any]] = []
    for i, line in enumerate(raw):
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            raise ValueError(f"{path.name}: line {i + 1} isn't JSON") from None
    return out


def scored(events: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """case id -> its `scored` event."""
    return {e["case"]: e for e in events if e.get("e") == "scored"}


def classified(events: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any] | None]:
    """case id -> the classification its classify claim recorded (None: three invalid tries)."""
    return {e["case"]: e.get("classification") for e in events if e.get("e") == "classified"}


def recorded_unscored(events: Iterable[dict[str, Any]]) -> set[str]:
    """Cases whose act claim ended recorded but whose score never reached the journal: read, and
    so scored missing, never handed out again (diff review 5B-2; the score is now written
    first, so only an older journal or a cut leaves one)."""
    recorded: set[str] = set()
    got: set[str] = set()
    for e in events:
        if e.get("e") == "end" and e.get("stage") == "act" and e.get("outcome") == "recorded":
            recorded.add(str(e.get("case")))
        elif e.get("e") == "scored":
            got.add(str(e.get("case")))
    return recorded - got


def open_claims(events: Iterable[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """(case, stage) -> its last claim with no end: what was out when the run stopped, with
    `read` set when its message was read."""
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for e in events:
        k = (str(e.get("case")), str(e.get("stage")))
        if e.get("e") == "claim":
            out[k] = dict(e)
        elif e.get("e") == "read" and k in out:
            out[k]["read"] = e["at"]
        elif e.get("e") == "end":
            out.pop(k, None)
    return out
