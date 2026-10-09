"""`ecf eval label --review FIELDS`: the terminal side of the blind label review of the synthetic
set (SPEC §16.1; `ecf/eval/review.py` holds the rules).

Each card is shown as a mail client shows it (From, To, Cc, Reply-To, Subject, Date, attachment
names, text) under a counter, never its id, title, notes, expected values or a model's answer.
Every chosen field is answered from the schema's list first; only then are differences from the
card's labels shown, and the operator keeps either. Cards come in random order. A card whose
labels change is rewritten and the set rebuilt; a changed or pending card is confirmed once the
operator has checked the rest of its expected values, which are shown at that point.
"""

from __future__ import annotations

import json
import re
import secrets
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from ecf.cli_corpus_label import field_block, parse_answer
from ecf.errors import InvalidInputError
from ecf.eval import labels as label_file
from ecf.eval import review as rv
from ecf.eval.builder import build_all, build_message
from ecf.eval.cards import Card, parse_card
from ecf.schema import CompiledSchema, FieldSpec, load_schema
from ecf.text import plain

CLEAR = "\x1b[H\x1b[2J"
SHOWN_CHARS = 4000
QUIT = "q"
_TAG = re.compile(r"<[^>]+>")


class _Quit(Exception):
    pass


def screen(card: Card) -> list[str]:
    """The card as a mail client shows it; nothing from its header beyond the message."""
    msg = build_message(card)
    lines = [f"{h + ':':<10}{msg[h]}" for h in ("From", "To", "Cc", "Reply-To", "Subject", "Date")
             if msg[h]]  # fmt: skip
    names = [a.name for a in card.attachments]
    lines.append(f"Attachments: {', '.join(names) or '-'}")
    text = card.body if card.body.strip() else _TAG.sub(" ", card.html or "")
    if len(text) > SHOWN_CHARS:
        text = text[:SHOWN_CHARS] + "\n[cut at 4,000 characters]"
    return [plain(x) for x in ("", *lines, "", *text.rstrip().splitlines(), "")]


def _ask(spec: FieldSpec, number: int, of: int, *, unlabelled: bool,
         read: Callable[[str], str], write: Callable[[str], None]) -> Any:  # fmt: skip
    footer = ("(- leaves this field unlabelled. " if unlabelled else "(") + \
        "q quits; nothing is saved for this card)"  # fmt: skip
    write("\n".join(plain(x) for x in field_block(spec, number, of, footer)) + "\n")
    while True:
        a = read(f"{spec.name} > ").strip().lower()
        if a == QUIT:
            raise _Quit
        if a == "-" and unlabelled:
            return None
        v = parse_answer(spec, a)
        if v is not None:
            return v
        write(f"Not one of the choices above for {spec.name}: type a number or a name.\n")


def _answers(task: rv.Task, schema: CompiledSchema, read: Callable[[str], str],
             write: Callable[[str], None]) -> dict[str, Any]:  # fmt: skip
    labelled = task.card.expected.labels
    return {f: _ask(schema.fields[f], n, len(task.fields), unlabelled=f not in labelled,
                    read=read, write=write)
            for n, f in enumerate(task.fields, 1)}  # fmt: skip


def _settle(f: str, before: Any, answer: Any, read: Callable[[str], str],
            write: Callable[[str], None]) -> Any:  # fmt: skip
    if before is None or answer == before:
        return answer
    write(f"{f}: you said {_show(answer)}; the card says {_show(before)}.\n")
    while True:
        a = read("Keep [y]ours or the [c]ard's? > ").strip().lower()
        if a in ("y", "c"):
            return answer if a == "y" else before


def _show(v: Any) -> str:
    return ("yes" if v else "no") if isinstance(v, bool) else str(v)


def _confirm_rest(root: Path, case_id: str, read: Callable[[str], str],
                  write: Callable[[str], None], today: date) -> bool:  # fmt: skip
    row = next(r for r in label_file.read(root) if r["id"] == case_id)
    if label_file.is_confirmed(row):
        return True
    write(f"This was {case_id}. Its expected values now:\n"
          f"  {json.dumps(row['expected'], sort_keys=True)}\n")  # fmt: skip
    while True:
        a = read("All right (the rule and safety too)? y confirm / n leave it pending > ")
        a = a.strip().lower()
        if a == "y":
            label_file.confirm(root, case_id, today)
            return True
        if a == "n":
            write(f"left pending: fix {case_id}.md, run `ecf eval build`, then"
                  f" `ecf eval label {case_id}`\n")  # fmt: skip
            return False


def _rebuild(root: Path) -> None:
    if findings := build_all(root).findings:
        raise InvalidInputError(f"hygiene scan failed after a label change: {findings[0]}")


def _paths(root: Path) -> dict[str, Path]:
    return {parse_card(p.read_text(encoding="utf-8"), source=p.name).id: p
            for p in sorted((root / "cases").glob("*.md"))}  # fmt: skip


def review(  # noqa: PLR0913 - the set, what to ask, and the terminal
    root: Path,
    fields: str,
    *,
    read: Callable[[str], str],
    write: Callable[[str], None],
    today: date,
    add: bool = False,
    globs: tuple[str, ...] = (),
    redo: bool = False,
    recheck: int = 0,
    seed: int = 0,
    shuffle: Callable[[list[Any]], None] | None = None,
    rebuild: Callable[[Path], None] = _rebuild,
) -> list[str]:
    """Review the chosen fields card by card (or, with `recheck`, ask a sample again); returns
    the summary lines."""
    schema = load_schema()
    chosen = rv.fields_for(fields, schema)
    paths = _paths(root)
    cards = [parse_card(p.read_text(encoding="utf-8"), source=p.name) for p in paths.values()]
    rows = label_file.read(root)
    log = rv.read_log(root)
    since = len(log)
    if recheck:
        todo = rv.recheck_tasks(cards, rows, log, recheck, seed=seed, globs=globs)
        if chosen is not None:
            todo = [rv.Task(t.card, t.row, tuple(f for f in t.fields if f in chosen))
                    for t in todo]  # fmt: skip
    else:
        todo = rv.tasks(cards, rows, schema, chosen, add=add, globs=globs, redo=redo, log=log)
        (shuffle or secrets.SystemRandom().shuffle)(todo)
    todo = [t for t in todo if t.fields]
    write(f"{len(todo)} card(s) to {'recheck' if recheck else 'review'}\n")
    for n, task in enumerate(todo, 1):
        write(CLEAR + f"[{n}/{len(todo)}]\n" + "\n".join(screen(task.card)) + "\n")
        a = read("Enter to answer, s skip this card, q quit > ").strip().lower()
        if a == QUIT:
            break
        if a == "s":
            continue
        try:
            answers = _answers(task, schema, read, write)
        except _Quit:
            break
        if recheck:
            rv.append_log(root, [_recheck_entry(task, f, v, log, today)
                                 for f, v in answers.items()])  # fmt: skip
            continue
        _apply(root, paths[task.card.id], task, answers, read, write, today, rebuild)
    return rv.summary(rv.read_log(root), since)


def _recheck_entry(task: rv.Task, f: str, answer: Any, log: list[dict[str, Any]],
                   today: date) -> dict[str, Any]:  # fmt: skip
    before = rv.latest_final(log, task.card.id, f)
    return rv.entry(task.row, f, before=before, answer=answer, final=before,
                    outcome="same" if answer == before else "differs", on=today,
                    kind=rv.RECHECK)  # fmt: skip


def _apply(root: Path, path: Path, task: rv.Task, answers: dict[str, Any],
           read: Callable[[str], str], write: Callable[[str], None], today: date,
           rebuild: Callable[[Path], None]) -> None:  # fmt: skip
    """Settle each answer against the label, write a changed card and rebuild, log the review,
    then confirm the card if it's changed or pending."""
    old = task.card.expected.labels
    finals = {f: _settle(f, old.get(f), a, read, write) for f, a in answers.items()}
    new = rv.merged(old, finals)
    if new != old:
        rv.set_labels(path, new)
        rebuild(root)
    rv.append_log(root, [rv.entry(task.row, f, before=old.get(f), answer=answers[f],
                                  final=finals[f], outcome=rv.outcome(old.get(f), answers[f],
                                                                      finals[f]), on=today)
                         for f in answers])  # fmt: skip
    _confirm_rest(root, task.card.id, read, write, today)
