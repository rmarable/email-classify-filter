"""`ecf eval settle`: the operator decides the fields the judges disagreed on, blind (eval generate
plan, Judges; OD-547, J14).

Only open disputes (`agree.open_disputes`) are asked, in a shuffled order, each email as a mail
client shows it (`grade.case_view`) under a counter, never its case id, title or the judges'
names; only its disputed fields, with the shipped definitions, the phishing definition and the
signs box. The judges' answers are shown only after the operator answers. Answers are applied
together: every card written, the set rebuilt once, and the settled cases confirmed in one
atomic write as the operator's. A phone page uses the same export (`export`) and import
(`apply`); the terminal (`ecf eval settle`) asks the same questions. A dispute `ecf eval expect`
raised (sender_type or priority deciding the expected rule; eval plan draft 21) carries a note
saying so. Settling records the answered fields in the row's `labelled_by`; then run `ecf eval
expect` again.
"""

from __future__ import annotations

import json
import secrets
from datetime import date
from pathlib import Path
from typing import Any, cast

from ecf.errors import InvalidInputError
from ecf.eval import agree as ag
from ecf.eval import expect as ex
from ecf.eval import generate as gen
from ecf.eval import grade as grading
from ecf.eval import labels as label_file
from ecf.eval import review
from ecf.eval.builder import build_all
from ecf.eval.cards import parse_card
from ecf.schema import FieldKind, load_schema

SETTLED = "settled"
NOTE = "your answer decides which rule applies"
# General red flags, never tied to an email (plan, Grading; OD-544)
SIGNS = ("A charge, invoice, order or account problem you didn't expect; pressure (a short"
         " deadline, a threat, \"cannot be reversed\"); it wants a phone call, a reply with"
         " details or a payment instead of you checking your own account; the sender isn't the"
         " company it speaks for (a \"help\", \"billing\" or \"support\" domain), or Reply-To goes"
         " somewhere else; vague about who it is or what you bought, a generic greeting; new"
         " bank details, gift cards, crypto or secrecy; an attachment or link pushed as the next"
         " step")  # fmt: skip


def questions(root: Path) -> list[dict[str, Any]]:
    """The open disputes as blind questions, shuffled: token, view, fields to answer (with
    their choices and definitions) and the judges' answers (for after), never the case id."""
    spec = load_schema().fields
    items: list[dict[str, Any]] = []
    for d in ag.open_disputes(root):
        view = grading.case_view(root, d["id"])
        fields: list[dict[str, Any]] = []
        for name in d["fields"]:
            f = spec[name]
            fields.append({"id": name, "kind": "boolean" if f.kind is FieldKind.BOOLEAN
                           else "choice", "values": list(f.values or []),
                           "help": f.description or ""})  # fmt: skip
        judges = cast(dict[str, dict[str, Any]], d.get("judges") or {})
        anon = {name: list(by.values()) for name, by in judges.items()}  # no judge names
        items.append({"case": d["id"], "view": view or "(this email is too large to show)",
                      "fields": fields, "judges": anon,
                      **({"note": NOTE} if d.get("reason") == ex.DECIDES else {})})  # fmt: skip
    secrets.SystemRandom().shuffle(items)
    for it in items:
        it["t"] = secrets.token_hex(4)
    return items


def export(root: Path, out: Path) -> tuple[Path, Path]:
    """The page data (no case ids) and its token map, for a phone page."""
    items = questions(root)
    out.mkdir(parents=True, exist_ok=True)
    page = {
        "items": [{k: v for k, v in it.items() if k != "case"} for it in items],
        "signs": SIGNS,
        "phishing": load_schema().fields["phishing"].description,
    }
    tokens = {it["t"]: it["case"] for it in items}
    data, key = out / "settle-page.json", out / "settle-tokens.json"
    data.write_text(json.dumps(page), encoding="utf-8")
    key.write_text(json.dumps(tokens, indent=1), encoding="utf-8")
    return data, key


def _judges(row: dict[str, Any]) -> list[Any]:
    lb: dict[str, Any] = row.get(label_file.LABELLED_BY) or {}
    return list(lb.get("judges") or [])


def apply(root: Path, answers: dict[str, dict[str, Any]], on: date) -> int:
    """Write the operator's answers (`{case id: {field: value}}`) for open disputes, every
    disputed field answered with an allowed value; rebuild once; confirm as the operator's.
    Returns how many emails were settled."""
    spec = load_schema().fields
    open_ = {d["id"]: d for d in ag.open_disputes(root)}
    rows = {r["id"]: r for r in label_file.read(root)}
    latest: list[str] = []  # for a row agree stopped before marking, before anything is written
    unmarked = [c for c in answers if c in rows and not _judges(rows[c])]
    if unmarked:
        latest = ex.judge_values(root)[0]
        if not latest:
            raise InvalidInputError("no agreement entry from `ecf eval agree`; run it again")
    writes: dict[Path, dict[str, Any]] = {}
    for cid, given in answers.items():
        d = open_.get(cid)
        if d is None:
            raise InvalidInputError(f"{cid}: no open dispute for it (settled already, or not"
                                    " in this set)")  # fmt: skip
        card = root / "cases" / f"{cid}.md"
        now = parse_card(card.read_text(encoding="utf-8"), source=card.name).expected.labels
        if label_file.edited(rows.get(cid) or {}, dict(now)):
            raise InvalidInputError(f"{cid} was edited by hand since its confirmation (or a"
                                    " settle was interrupted): run `ecf eval agree` again"
                                    " first")  # fmt: skip
        missing = [f for f in d["fields"] if f not in given]
        if missing:
            raise InvalidInputError(f"an email is missing answers for {', '.join(missing)}")
        for name, v in given.items():
            f = spec.get(name)
            if f is None or name not in d["fields"]:
                raise InvalidInputError(f"{name} isn't a disputed field of that email")
            ok = isinstance(v, bool) if f.kind is FieldKind.BOOLEAN else v in f.values
            if not ok:
                raise InvalidInputError(f"{v!r} isn't a {name} value")
        path = root / "cases" / f"{cid}.md"
        old = dict(parse_card(path.read_text(encoding="utf-8"), source=path.name).expected.labels)
        writes[path] = review.merged(old, given)
    for path, labels in writes.items():
        review.write_labels(path, labels)
    if writes and (rep := build_all(root)).findings:
        raise InvalidInputError(f"hygiene scan failed after writing labels: {rep.findings[0]}")
    gen.verify(root)
    rows = {r["id"]: r for r in label_file.read(root)}  # after the rebuild
    provenance: dict[str, dict[str, Any]] = {}
    for cid, a in answers.items():
        lb: dict[str, Any] = rows[cid].get(label_file.LABELLED_BY) or {}
        provenance[cid] = {"judges": list(lb.get("judges") or latest),
                           "settled": sorted({*label_file.settled(rows[cid]), *a})}  # fmt: skip
    done = label_file.confirm_many(root, provenance, on) if answers else 0
    gen.Work(root).append(SETTLED, [{"id": c, "fields": sorted(a), "on": on.isoformat()}
                                    for c, a in answers.items()])  # fmt: skip
    return done


def apply_page(root: Path, answers_file: Path, tokens_file: Path,
               on: date) -> tuple[int, int]:  # fmt: skip
    """A phone page's saved answers (`{token: {field: value}}`), mapped back to case ids; an
    email not fully answered yet is left for later. Returns (settled, left)."""
    try:
        given: Any = json.loads(answers_file.read_text(encoding="utf-8"))
        tokens: Any = json.loads(tokens_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise InvalidInputError(f"can't read the page's answers ({exc})") from None
    if not isinstance(given, dict) or not isinstance(tokens, dict):
        raise InvalidInputError("answers and tokens must be JSON objects")
    need = {d["id"]: set(d["fields"]) for d in ag.open_disputes(root)}
    mapped: dict[str, dict[str, Any]] = {}
    left = 0
    for t, a in cast(dict[str, Any], given).items():
        case = cast(dict[str, str], tokens).get(t)
        if case is None or not isinstance(a, dict):
            raise InvalidInputError(f"token {t!r} isn't on this page")
        answer = cast(dict[str, Any], a)
        if case not in need:
            continue  # settled already
        if not need[case] <= set(answer):
            left += 1
            continue
        mapped[case] = {k: v for k, v in answer.items() if k in need[case]}
    return apply(root, mapped, on), left
