"""`ecf eval label --corpus FILE`: blind labelling of a real-mail corpus (SPEC §16.7; R5, R6, R21,
R66, R68, R71, R122, R141, R155, R197).

The service opens the corpus (one decrypted session) and serves one message at a time: headers,
authentication, attachment names, a few facts and keyword hits, and the stored excerpt with text
addressed to an automated reader removed. No model output is ever shown or loaded. You author
every schema field from its closed list, or mark the message `s` (skip) or `u` (unsure); both are
left out of scoring. Labels are saved after each message, beside the corpus, so you can stop with
`q` and resume. The screen is the terminal's alternate screen, left on every exit path, so the mail
stays out of the scrollback (a terminal set to keep alternate-screen lines still keeps them; SPEC
§12.2).
"""

from __future__ import annotations

import atexit
import secrets
import signal
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from types import FrameType
from typing import Any, Protocol

from ecf.errors import InvalidInputError
from ecf.eval import corpus_labels as cl
from ecf.schema import FieldKind, FieldSpec, load_schema_v1
from ecf.text import plain

ALT_ON, ALT_OFF, CLEAR = "\x1b[?1049h", "\x1b[?1049l", "\x1b[H\x1b[2J"
SHOWN_CHARS = 1500
QUIT = "q"


class Client(Protocol):  # the part of LocalClient this uses
    def request(self, method: str, path: str, json: Any = None, *, auth: bool = True,
                timeout: float | None = None) -> Any: ...  # fmt: skip


@dataclass
class Tally:
    labelled: int = 0
    skipped: int = 0
    unsure: int = 0
    reveals: int = 0
    keyword_hits: int = 0
    keyword_hits_payment: int = 0
    seen: list[int] = field(default_factory=list[int])


def render(item: dict[str, Any], *, more: bool, reveal: bool) -> list[str]:
    """The screen for one message: everything through `plain`, nothing from a model."""
    d = item["display"]
    facts = {k: v for k, v in item["facts"].items() if v not in (None, False, [], "")}
    hits = {k: v for k, v in item["keywords"].items() if v}
    text = item["unredacted"] if reveal else item["excerpt"]
    if not more and len(text) > SHOWN_CHARS:
        text = text[:SHOWN_CHARS] + "\n[m: more]"
    lines = [
        f"Message {item['index']}" + (" (redacted text shown)" if reveal else ""),
        f"From:     {d['from']}",
        f"Reply-To: {', '.join(d['reply_to']) or '-'}",
        f"To:       {', '.join(d['to'])}",
        f"Subject:  {d['subject']}",
        f"Date:     {d['date']}",
        f"Attachments: {', '.join(d['attachments']) or '-'}",
        f"Facts:    {', '.join(f'{k}={v}' for k, v in facts.items()) or '-'}",
        f"Keywords: {', '.join(f'{k}: {v}' for k, v in hits.items()) or '-'}",
        f"Checks:   {', '.join(item['triggers']) or '-'}",
        "",
        *text.splitlines(),
        "",
    ]
    return [plain(x) for x in lines]


def parse_answer(spec: FieldSpec, answer: str) -> str | bool | None:
    """A field's value from what was typed: its number in the list, the value itself, or y/n for
    a yes/no field; None when it's none of those."""
    a = answer.strip().lower()
    if spec.kind is FieldKind.BOOLEAN:
        return {"y": True, "yes": True, "n": False, "no": False}.get(a)
    if a.isdigit() and 1 <= int(a) <= len(spec.values):
        return spec.values[int(a) - 1]
    return a if a in spec.values else None


def field_block(spec: FieldSpec, number: int, of: int) -> list[str]:
    """How a field is asked: its name and meaning, then each choice on its own line with its
    number and what it means (from the schema), then what to type."""
    lines = ["", f"Field {number} of {of}: {spec.name}", f"  {spec.description}"]
    if spec.kind is FieldKind.BOOLEAN:
        lines += ["  y  yes", "  n  no", "Type y or n."]
    else:
        width = max(len(v) for v in spec.values)
        descs = spec.value_descriptions or ("",) * len(spec.values)
        lines += [f"  {i:>2}  {v:<{width}}  {d}".rstrip()
                  for i, (v, d) in enumerate(zip(spec.values, descs, strict=False), 1)]  # fmt: skip
        lines.append("Type the number or the name.")
    lines.append(
        "(or: s skip this message, u unsure; both are saved and left unscored."
        " q quits; nothing is saved for this message)"
    )
    return lines


def _ask_field(spec: FieldSpec, number: int, of: int, read: Callable[[str], str],
               write: Callable[[str], None], current: str | bool | None = None
               ) -> str | bool:  # fmt: skip
    """Returns the value, or raises for a mark (s/u) or quit. When relabelling, the saved value
    is shown in brackets and Enter keeps it."""
    write("\n".join(plain(x) for x in field_block(spec, number, of)) + "\n")
    shown = "" if current is None else f" [{_answer(current)}]"
    while True:
        a = read(f"{spec.name}{shown} > ").strip().lower()
        if a == "" and current is not None:
            return current
        if a in (*cl.MARKS, QUIT):
            raise _Mark(a)
        v = parse_answer(spec, a)
        if v is not None:
            return v
        write(f"Not one of the choices above for {spec.name}: type a number or a name.\n")


def _answer(value: str | bool) -> str:
    if isinstance(value, bool):
        return "y" if value else "n"
    return value


class _Mark(Exception):
    def __init__(self, mark: str) -> None:
        super().__init__(mark)
        self.mark = mark


def _screen_guard(write: Callable[[str], None]) -> Callable[[], None]:
    """Enter the alternate screen; the returned function leaves it, and so does any exit."""
    write(ALT_ON)
    done = False

    def leave() -> None:
        nonlocal done
        if not done:
            done = True
            write(ALT_OFF)

    def on_signal(signum: int, _frame: FrameType | None) -> None:
        leave()
        raise SystemExit(128 + signum)

    atexit.register(leave)
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, on_signal)
    return leave


def _view(item: dict[str, Any], heading: str, read: Callable[[str], str],
          write: Callable[[str], None], tally: Tally) -> str:  # fmt: skip
    """Show the message until the operator labels it (""), marks it (s/u) or quits (q)."""
    more = reveal = False
    while True:
        write(CLEAR + heading + "\n" + "\n".join(render(item, more=more, reveal=reveal)) + "\n")
        a = read(f"{heading} Enter to label, m more, r show redacted text, s skip, u unsure,"
                 " q quit > ").strip().lower()  # fmt: skip
        if a == "m":
            more = True
        elif a == "r":
            reveal = more = True
            tally.reveals += 1
        elif a in ("", *cl.MARKS, QUIT):
            return a


def _author(read: Callable[[str], str], write: Callable[[str], None],
            before: dict[str, str | bool] | None = None
            ) -> tuple[dict[str, Any] | None, str | None]:  # fmt: skip
    """Every field's value, or (None, mark); raises _Mark(q) to quit. `before` is the saved
    label when relabelling."""
    values: dict[str, Any] = {}
    fields = list(load_schema_v1().fields.values())
    try:
        for n, spec in enumerate(fields, 1):
            current = (before or {}).get(spec.name)
            values[spec.name] = _ask_field(spec, n, len(fields), read, write, current)
    except _Mark as m:
        if m.mark == QUIT:
            raise
        return None, m.mark
    return values, None


def _count(tally: Tally, item: dict[str, Any], values: dict[str, Any] | None,
           mark: str | None) -> None:  # fmt: skip
    tally.seen.append(int(item["index"]))
    if mark == "s":
        tally.skipped += 1
    elif mark == "u":
        tally.unsure += 1
    else:
        tally.labelled += 1
        if any(item["keywords"].values()):
            tally.keyword_hits += 1
            tally.keyword_hits_payment += bool(values and values["payment_related"])


def label(  # noqa: PLR0913 - the client, the file, the terminal and what to offer
    c: Client,
    corpus: Path,
    secret: str,
    *,
    read: Callable[[str], str],
    write: Callable[[str], None],
    today: date,
    shuffle: Callable[[list[Any]], None] | None = None,
    again: frozenset[int] = frozenset(),
    marked: bool = False,
) -> Tally:
    """Label the corpus's unlabelled messages in random order (R6); returns the tally. `again`
    offers those message numbers once more, labelled or not; `marked` offers the ones skipped or
    marked unsure. A new answer replaces the old one."""
    opened = c.request("POST", "/v1/corpus/session", {"path": str(corpus), "passphrase": secret},
                       timeout=600)  # fmt: skip
    del secret
    sid, corpus_id = str(opened["session_id"]), str(opened["corpus_id"])
    path = cl.path_for(corpus)
    labels = cl.load(path)
    tally = Tally()
    leave = _screen_guard(write)
    try:
        keys = c.request("GET", f"/v1/corpus/session/{sid}/keys")["keys"]
        todo = _choose(keys, labels, again, marked)
        (shuffle or secrets.SystemRandom().shuffle)(todo)
        for n, k in enumerate(todo, 1):
            item = c.request("GET", f"/v1/corpus/session/{sid}/items/{k['index']}")
            a = _view(item, f"[{n}/{len(todo)}]{_was(labels.get(_key(k)))}", read, write, tally)
            if a == QUIT:
                break
            try:
                values, mark = (
                    (None, a) if a in cl.MARKS else _author(read, write, _saved(labels, k))
                )
            except _Mark:
                break
            cl.put(labels, cl.make(_key(k), corpus_id, values, mark, today))
            cl.save(path, labels)
            _count(tally, item, values, mark)
    finally:
        leave()
        c.request("DELETE", f"/v1/corpus/session/{sid}")
    return tally


def _saved(labels: dict[cl.Key, cl.Label], k: dict[str, Any]) -> dict[str, str | bool] | None:
    lab = labels.get(_key(k))
    return lab.labels if lab is not None else None


def _was(before: cl.Label | None) -> str:
    if before is None:
        return ""
    state = {"s": "skipped", "u": "unsure"}.get(before.mark or "", "labelled")
    return f" (relabelling; was {state})"


def _choose(keys: list[dict[str, Any]], labels: dict[cl.Key, cl.Label], again: frozenset[int],
            marked: bool) -> list[dict[str, Any]]:  # fmt: skip
    """Which messages to offer: the numbers asked for again, the skipped or unsure ones, or (by
    default) the unlabelled ones."""
    if again:
        known = {int(k["index"]) for k in keys}
        if missing := sorted(again - known):
            raise InvalidInputError(f"no message {', '.join(map(str, missing))} in this corpus")
        return [k for k in keys if int(k["index"]) in again]
    if marked:
        return [k for k in keys if (lab := labels.get(_key(k))) is not None and lab.mark]
    return [k for k in keys if _key(k) not in labels]


def _key(k: dict[str, Any]) -> cl.Key:
    return (str(k["key"]["content_hash"]), str(k["key"]["identity_digest"]))


def summary(t: Tally, labels_path: Path) -> list[str]:
    lines = [f"labelled {t.labelled}, skipped {t.skipped}, unsure {t.unsure}; labels in"
             f" {labels_path} ({len(cl.load(labels_path))} in all)"]  # fmt: skip
    if t.reveals:
        lines.append(f"redacted text shown {t.reveals} time(s)")
    if t.keyword_hits:
        lines.append(f"of {t.keyword_hits} labelled message(s) with keyword hits,"
                     f" {t.keyword_hits_payment} labelled payment-related"
                     " (a bias check)")  # fmt: skip
    return lines


def require_output_terminal() -> None:
    if not sys.stdout.isatty():
        raise InvalidInputError("labelling shows mail text and needs its output on a terminal")
