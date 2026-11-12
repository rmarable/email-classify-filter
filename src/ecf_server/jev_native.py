"""Jev-designed questions for schema v3, eval only (planning-docs/JEV-NATIVE-QUESTIONS.md §3.2,
§3.4). The file is `data/jev_questions_v3.yaml`, written by hand; `schema_v3.yaml` stays the
Claude-style set. Only `evaljev` imports this module (an import-linter contract), so the service's
classifier path never reads the native file.

- **`load(schema)`** reads and checks the file against the compiled schema, refusing it on any
  structural fault (§3.2's list), cached per schema digest and file text.
- **`questions(native, ...)`** builds the request's questions: the `kinds` directive expanded,
  `inspect` paths added for the JSON state (`s3`), every Choice's options reversed for the
  option-order check (`--jev-reverse-choices`).
- **`compose(native, body, schema)`** maps a reply to a validated classification and per-field
  probabilities, and keeps every question's raw answer. It fails closed: an answer missing, of the
  wrong type, a probability not finite or outside [0, 1], or a choice outside the file's options
  gives classification None.
- **Probabilities per field** (§3.2, Q7, R6): a value's p is its deciding p times (1 - p) of every
  earlier decider; a plain `else` value's p is 1 - the largest raw p of its `else_p_steps`.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from importlib import resources
from typing import Any, cast

from ecf.errors import InvalidInputError
from ecf.eval.setio import reason
from ecf.schema import CompiledSchema, FieldKind
from ecf.yamlio import load_yaml
from ecf_server import jev_mapping, systemone

FILE = "jev_questions_v3.yaml"
TOP_KEYS = frozenset({"version", "schema_version", "notes", "mapping", "questions", "fields"})
TYPES = frozenset({"noul", "choice", "score"})
INSTRUCTION_KEYS = frozenset({"question", "note", "kinds"})
NOUL_TRUE_KEYS = frozenset({"what", "examples", "note"})
NOUL_FALSE_KEYS = frozenset({"what", "examples"})  # notes go on the `true` side only (P2)
OPTION_KEYS = frozenset({"what", "not_for", "examples"})
LEVEL_KEYS = frozenset({"situation", "examples"})
COMPOSERS = frozenset({"noul", "choice", "score", "cascade"})
KINDS_FROM = "from "
# the `inspect` paths added to each instruction under the JSON state (`s3`, P14): sender questions
# look at the sender and the body, every other question at the whole email
SENDER_QUESTIONS = frozenset({"sender_role", "claims_internal", "private_individual",
                              "fd_sender_mismatch", "fd_unexpected"})  # fmt: skip


def _bad(msg: str) -> InvalidInputError:
    return InvalidInputError(f"{FILE}: {msg}")


@dataclass(frozen=True)
class Native:
    """The checked file: its questions (as written, directives unexpanded), field composers, the
    caution mapping, and the digest of the whole parsed file."""

    questions: dict[str, dict[str, Any]]
    fields: dict[str, dict[str, Any]]
    mapping: jev_mapping.JevMapping
    mapping_doc: dict[str, Any]
    digest: str


# Expressions: a question id, {any: [...]} (largest p) or {all: [...]} (smallest p), nested.


def _expr_ids(expr: Any, where: str) -> set[str]:
    if isinstance(expr, str):
        return {expr}
    if isinstance(expr, dict) and len(cast("dict[str, Any]", expr)) == 1:
        ((op, items),) = cast("dict[str, Any]", expr).items()
        if op in ("any", "all") and isinstance(items, list) and items:
            out: set[str] = set()
            for it in cast("list[Any]", items):
                out |= _expr_ids(it, where)
            return out
    raise _bad(f"{where}: an expression is a question id, {{any: [...]}} or {{all: [...]}}")


def _expr_p(expr: Any, raw: dict[str, Any]) -> float:
    if isinstance(expr, str):
        return cast("float", raw[expr])
    ((op, items),) = cast("dict[str, list[Any]]", expr).items()
    ps = [_expr_p(it, raw) for it in items]
    return max(ps) if op == "any" else min(ps)


# The file's checks (§3.2)


def _check_text(v: Any, where: str) -> None:
    if isinstance(v, str):
        if not v.strip():
            raise _bad(f"{where}: empty text")
        return
    if isinstance(v, list) and v and all(isinstance(x, str) and x.strip()
                                         for x in cast("list[Any]", v)):  # fmt: skip
        return
    raise _bad(f"{where}: text or a list of texts")


def _check_side(v: Any, keys: frozenset[str], where: str, *, need: str) -> None:
    if isinstance(v, str):
        _check_text(v, where)
        return
    if not isinstance(v, dict):
        raise _bad(f"{where}: text or an object")
    d = cast("dict[str, Any]", v)
    if "not_for" in d and "not_for" not in keys:
        raise _bad(f"{where}: `not_for` is for Choice options only (P2)")
    if unknown := sorted(set(d) - keys):
        raise _bad(f"{where}: unknown keys {unknown}")
    if need not in d:
        raise _bad(f"{where}: `{need}` is required")
    for k, x in d.items():
        _check_text(x, f"{where}.{k}")


def _check_question(qid: str, q: Any, all_q: dict[str, Any]) -> None:  # noqa: PLR0912 - one check per rule
    if not isinstance(q, dict):
        raise _bad(f"question {qid}: an object")
    d = cast("dict[str, Any]", q)
    if unknown := sorted(set(d) - {"type", "instructions", "criteria"}):
        raise _bad(f"question {qid}: unknown keys {unknown}")
    t = d.get("type")
    if t not in TYPES:
        raise _bad(f"question {qid}: type must be one of {sorted(TYPES)}")
    ins = d.get("instructions")
    if isinstance(ins, dict):
        di = cast("dict[str, Any]", ins)
        if unknown := sorted(set(di) - INSTRUCTION_KEYS):
            raise _bad(f"question {qid}: unknown instruction keys {unknown}")
        if "question" not in di:
            raise _bad(f"question {qid}: an instruction object needs `question`")
        _check_text(di["question"], f"question {qid}.question")
        if "note" in di:
            _check_text(di["note"], f"question {qid}.note")
        if "kinds" in di:
            src = di["kinds"]
            if not (isinstance(src, str) and src.startswith(KINDS_FROM)):
                raise _bad(f"question {qid}: `kinds` must be `from <choice question>`")
            target = all_q.get(src[len(KINDS_FROM) :])
            if not (isinstance(target, dict) and cast("dict[str, Any]", target).get("type")
                    == "choice"):  # fmt: skip
                raise _bad(f"question {qid}: `kinds` must name a choice question")
    else:
        _check_text(ins, f"question {qid}.instructions")
    crit = d.get("criteria")
    if t == "noul":
        if not isinstance(crit, dict) or set(cast("dict[str, Any]", crit)) != {"true", "false"}:
            raise _bad(f"question {qid}: a Noul's criteria are `true` and `false`")
        for side, v in cast("dict[str, Any]", crit).items():
            keys = NOUL_TRUE_KEYS if side == "true" else NOUL_FALSE_KEYS
            _check_side(v, keys, f"question {qid}.{side}", need="what")
    elif t == "choice":
        if not isinstance(crit, dict) or not 2 <= len(cast("dict[str, Any]", crit)) <= 255:
            raise _bad(f"question {qid}: a Choice has 2-255 described options")
        for opt, v in cast("dict[str, Any]", crit).items():
            if not isinstance(v, dict):
                raise _bad(f"question {qid}.{opt}: a Choice option is an object with `what`")
            _check_side(v, OPTION_KEYS, f"question {qid}.{opt}", need="what")
    else:
        if not isinstance(crit, list) or not 2 <= len(cast("list[Any]", crit)) <= 10:
            raise _bad(f"question {qid}: a Score has 2-10 levels")
        for i, v in enumerate(cast("list[Any]", crit)):
            _check_side(v, LEVEL_KEYS, f"question {qid}.level {i}", need="situation")


def _threshold(v: Any, where: str) -> float:
    if not isinstance(v, int | float) or isinstance(v, bool) or not 0 < float(v) < 1:
        raise _bad(f"{where}: a threshold is a number in (0, 1)")
    return float(v)


def _options(questions: dict[str, Any], qid: str, kind: str, where: str) -> list[str]:
    q = questions.get(qid)
    if not isinstance(q, dict) or cast("dict[str, Any]", q).get("type") != kind:
        raise _bad(f"{where}: `{qid}` must be a {kind} question")
    crit = cast("dict[str, Any]", q)["criteria"]
    return list(cast("dict[str, Any]", crit)) if kind == "choice" else []


def _noul_ids(questions: dict[str, Any], ids: set[str], where: str) -> None:
    for i in ids:
        q = questions.get(i)
        if not isinstance(q, dict) or cast("dict[str, Any]", q).get("type") != "noul":
            raise _bad(f"{where}: `{i}` must be a Noul question")


def _check_field(  # noqa: PLR0912, PLR0915 - one check per composer rule (§3.2)
    name: str, spec: Any, schema: CompiledSchema,
    questions: dict[str, Any]) -> set[str]:  # fmt: skip
    """The field's composer checked: the questions it uses, and every schema value producible."""
    f = schema.fields[name]
    where = f"field {name}"
    if not isinstance(spec, dict):
        raise _bad(f"{where}: an object")
    d = cast("dict[str, Any]", spec)
    comp = d.get("compose")
    if comp not in COMPOSERS:
        raise _bad(f"{where}: compose must be one of {sorted(COMPOSERS)}")
    allowed = {"noul": {"compose", "from"}, "choice": {"compose", "from"},
               "score": {"compose", "from"},
               "cascade": {"compose", "threshold", "steps", "else",
                           "else_p_steps"}}[comp]  # fmt: skip
    if unknown := sorted(set(d) - allowed):
        raise _bad(f"{where}: unknown keys {unknown}")
    values = set(f.values) if f.kind is not FieldKind.BOOLEAN else {True, False}
    if comp == "noul":
        if f.kind is not FieldKind.BOOLEAN:
            raise _bad(f"{where}: `noul` composes a yes/no field only")
        ids = _expr_ids(d.get("from"), where)
        _noul_ids(questions, ids, where)
        return ids
    if f.kind is FieldKind.BOOLEAN:
        raise _bad(f"{where}: a yes/no field is composed with `noul`")
    if comp == "choice":
        qid = d.get("from")
        opts = _options(questions, cast("str", qid), "choice", where)
        if set(opts) != values:
            raise _bad(f"{where}: the choice's options must be the field's values")
        return {cast("str", qid)}
    if comp == "score":
        qid = cast("str", d.get("from"))
        _options(questions, qid, "score", where)
        levels = cast("list[Any]", cast("dict[str, Any]", questions[qid])["criteria"])
        if f.kind is not FieldKind.ORDINAL or len(levels) != len(f.values):
            raise _bad(f"{where}: a score composes an ordinal field with one level per value")
        return {qid}
    _threshold(d.get("threshold"), where)
    steps = d.get("steps")
    if not isinstance(steps, list) or not steps:
        raise _bad(f"{where}: `steps` is a list of [value, expression]")
    used: set[str] = set()
    made: list[Any] = []
    for st in cast("list[Any]", steps):
        if not (isinstance(st, list) and len(cast("list[Any]", st)) == 2):
            raise _bad(f"{where}: a step is [value, expression]")
        value, expr = cast("list[Any]", st)
        ids = _expr_ids(expr, where)
        _noul_ids(questions, ids, where)
        used |= ids
        made.append(value)
    els = d.get("else")
    if isinstance(els, dict):
        e = cast("dict[str, Any]", els)
        if unknown := sorted(set(e) - {"choice", "companion", "companion_threshold", "fallback",
                                       "none_value"}):  # fmt: skip
            raise _bad(f"{where}: unknown `else` keys {unknown}")
        opts = _options(questions, cast("str", e.get("choice")), "choice", where)
        used.add(cast("str", e["choice"]))
        made += opts
        if "companion" in e:
            _noul_ids(questions, {cast("str", e["companion"])}, where)
            used.add(cast("str", e["companion"]))
            _threshold(e.get("companion_threshold"), f"{where}.companion_threshold")
            for fb in cast("list[Any]", e.get("fallback") or []):
                if not (isinstance(fb, list) and len(cast("list[Any]", fb)) == 2):
                    raise _bad(f"{where}: a fallback is [value, expression]")
                value, expr = cast("list[Any]", fb)
                ids = _expr_ids(expr, where)
                _noul_ids(questions, ids, where)
                used |= ids
                made.append(value)
            if "none_value" not in e:
                raise _bad(f"{where}: a companion needs `none_value`")
            made.append(e["none_value"])
        elif "fallback" in e or "none_value" in e or "companion_threshold" in e:
            raise _bad(f"{where}: fallbacks and `none_value` need a companion")
    elif isinstance(els, str):
        made.append(els)
    else:
        raise _bad(f"{where}: `else` is a value or a choice")
    if any(not isinstance(v, str) for v in made):
        raise _bad(f"{where}: every value is a string")
    eps = d.get("else_p_steps")
    if eps is not None:
        step_values = [cast("list[Any]", st)[0] for st in cast("list[Any]", steps)]
        if (not isinstance(els, str) or not isinstance(eps, list) or not eps
                or not all(isinstance(x, str) for x in cast("list[Any]", eps))
                or not set(cast("list[str]", eps)) <= set(step_values)):  # fmt: skip
            raise _bad(f"{where}: `else_p_steps` names steps of a cascade with a plain `else`")
    if bad := sorted(str(v) for v in made if v not in values):
        raise _bad(f"{where}: values not in the schema {bad}")
    if missing := sorted(str(v) for v in values - set(made)):
        raise _bad(f"{where}: values no step, option, fallback or `else` gives {missing}")
    return used


def _canonical(doc: dict[str, Any]) -> str:
    return json.dumps({k: doc[k] for k in ("version", "schema_version", "mapping", "questions",
                                           "fields")}, sort_keys=False,
                      separators=(",", ":"), ensure_ascii=False)  # fmt: skip


def parse(text: str, schema: CompiledSchema) -> Native:  # noqa: PLR0912 - one check per rule
    """The file's text checked against `schema` (§3.2)."""
    doc = load_yaml(text, source=FILE)
    if not isinstance(doc, dict):
        raise _bad("a mapping at the top")
    d = cast("dict[str, Any]", doc)
    if unknown := sorted(set(d) - TOP_KEYS):
        raise _bad(f"unknown keys {unknown}")
    if d.get("version") != 1:
        raise _bad("version must be 1")
    if d.get("schema_version") != schema.version or schema.extension_fields:
        raise _bad(f"answers schema v{d.get('schema_version')} without extension fields only;"
                   f" this schema is v{schema.version}"
                   f"{' with extension fields' if schema.extension_fields else ''}")  # fmt: skip
    mapping_doc = d.get("mapping")
    try:
        mapping = jev_mapping.parse(mapping_doc)
    except Exception as exc:  # jev_mapping raises InternalError for the shipped file's faults
        raise _bad(f"mapping: {reason(exc)}") from exc
    questions = d.get("questions")
    if not isinstance(questions, dict) or not questions:
        raise _bad("`questions` is a mapping of question ids")
    qs = cast("dict[str, Any]", questions)
    for qid, q in qs.items():
        _check_question(qid, q, qs)
    fields = d.get("fields")
    if not isinstance(fields, dict):
        raise _bad("`fields` is a mapping of schema fields")
    fs = cast("dict[str, Any]", fields)
    if missing := sorted(set(schema.fields) - set(fs)):
        raise _bad(f"fields with no composer {missing}")
    if extra := sorted(set(fs) - set(schema.fields)):
        raise _bad(f"composers for fields not in the schema {extra}")
    used: set[str] = set()
    for name, spec in fs.items():
        used |= _check_field(name, spec, schema, qs)
    for name in mapping.bands:  # band crossings count by question id (diff review F10)
        spec = cast("dict[str, Any]", fs.get(name) or {})
        if spec.get("compose") != "noul" or spec.get("from") != name:
            raise _bad(f"field {name}: a banded field is composed from its same-named Noul")
    for q in qs.values():
        ins = cast("dict[str, Any]", q).get("instructions")
        if isinstance(ins, dict) and "kinds" in cast("dict[str, Any]", ins):
            used.add(cast("str", cast("dict[str, Any]", ins)["kinds"])[len(KINDS_FROM) :])
    if unknown_q := sorted(used - set(qs)):
        raise _bad(f"unknown questions {unknown_q}")
    if unused := sorted(set(qs) - used):
        raise _bad(f"questions no field uses {unused}")
    digest = hashlib.sha256(_canonical(d).encode("utf-8")).hexdigest()
    return Native(cast("dict[str, dict[str, Any]]", qs), cast("dict[str, dict[str, Any]]", fs),
                  mapping, cast("dict[str, Any]", mapping_doc), digest)  # fmt: skip


def _text() -> str:
    return resources.files("ecf_server.data").joinpath(FILE).read_text("utf-8")


_CACHE: dict[tuple[str, str], Native] = {}


def load(schema: CompiledSchema) -> Native:
    """The shipped file checked against `schema`, cached per schema digest and file text (P30)."""
    text = _text()
    key = (schema.digest, hashlib.sha256(text.encode("utf-8")).hexdigest())
    if key not in _CACHE:
        _CACHE[key] = parse(text, schema)
    return _CACHE[key]


# The request's questions


def _instruction(q: dict[str, Any], qid: str, native: Native, *, inspect: bool,
                 reverse: bool) -> Any:  # fmt: skip
    ins = q["instructions"]
    obj: dict[str, Any] = dict(cast("dict[str, Any]", ins)) if isinstance(ins, dict) else {
        "question": ins}  # fmt: skip
    if "kinds" in obj:
        src = native.questions[cast("str", obj["kinds"])[len(KINDS_FROM) :]]["criteria"]
        kinds = {k: cast("dict[str, Any]", v)["what"] if isinstance(v, dict) else v
                 for k, v in cast("dict[str, Any]", src).items()}  # fmt: skip
        obj["kinds"] = dict(reversed(kinds.items())) if reverse else kinds
    if inspect:
        obj["inspect"] = (["`email.from`", "`email.body`"] if qid in SENDER_QUESTIONS
                          else "`email`")  # fmt: skip
    if set(obj) == {"question"}:
        return obj["question"]
    return obj


def questions(native: Native, *, inspect: bool = False, reverse: bool = False) -> dict[str, Any]:
    """The request's questions, in file order. `inspect` adds `inspect` paths for the JSON state
    (`s3`); `reverse` reverses every Choice's options and the `kinds` lists (R7)."""
    out: dict[str, Any] = {}
    for qid, q in native.questions.items():
        crit = q["criteria"]
        if q["type"] == "choice" and reverse:
            crit = dict(reversed(list(cast("dict[str, Any]", crit).items())))
        out[qid] = {"type": q["type"],
                    "instructions": _instruction(q, qid, native, inspect=inspect,
                                                 reverse=reverse),
                    "criteria": crit}  # fmt: skip
    return out


# Composing a reply (§3.2, §3.4)


def _num(v: Any) -> float | None:
    if isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(float(v)):
        x = float(v)
        return x if 0.0 <= x <= 1.0 else None
    return None


def raw(native: Native, body: dict[str, Any]) -> dict[str, Any] | None:  # noqa: PLR0911 - one return per fault
    """Every question's raw answer (a Noul's p; a Choice's option probabilities and choice; a
    Score's level probabilities), or None when any is missing or malformed (fail closed, P16)."""
    answers = body.get("answers")
    if not isinstance(answers, dict):
        return None
    got = cast("dict[str, Any]", answers)
    out: dict[str, Any] = {}
    for qid, q in native.questions.items():
        a = got.get(qid)
        if not isinstance(a, dict):
            return None
        ans = cast("dict[str, Any]", a)
        if q["type"] == "noul":
            p = _num(ans.get("noul"))
            if p is None:
                return None
            out[qid] = p
            continue
        probs = ans.get("probabilities")
        if not isinstance(probs, dict):
            return None
        pm = cast("dict[str, Any]", probs)
        if q["type"] == "choice":
            opts = list(cast("dict[str, Any]", q["criteria"]))
            ps = {o: _num(pm.get(o)) for o in opts}
            choice = ans.get("choice")
            if choice not in opts or any(v is None for v in ps.values()):
                return None
            out[qid] = {"choice": choice, "probabilities": cast("dict[str, float]", ps)}
        else:
            n = len(cast("list[Any]", q["criteria"]))
            levels = [_num(pm.get(str(i))) for i in range(n)]
            if any(v is None for v in levels):
                return None
            out[qid] = {"probabilities": cast("list[float]", levels)}
    return out


def _prod_not(ps: list[float]) -> float:
    out = 1.0
    for p in ps:
        out *= 1.0 - p
    return out


def _cascade(  # noqa: PLR0911 - one return per path (§3.2)
    spec: dict[str, Any],
    rawp: dict[str, Any],
) -> tuple[Any, dict[str, float]]:
    """A cascade's value and its reported probabilities (§3.2's one rule)."""
    thr = float(spec["threshold"])
    earlier: list[float] = []
    for value, expr in cast("list[list[Any]]", spec["steps"]):
        p = _expr_p(expr, rawp)
        if p >= thr:
            return value, {str(value): p * _prod_not(earlier)}
        earlier.append(p)
    els = spec["else"]
    if isinstance(els, str):
        eps = cast("list[str] | None", spec.get("else_p_steps"))
        if eps:
            step_p = {str(st[0]): _expr_p(st[1], rawp)
                      for st in cast("list[list[Any]]", spec["steps"])}  # fmt: skip
            return els, {els: 1.0 - max(step_p[s] for s in eps)}
        return els, {els: _prod_not(earlier)}
    e = cast("dict[str, Any]", els)
    choice = cast("dict[str, Any]", rawp[e["choice"]])
    if "companion" in e:
        c = cast("float", rawp[e["companion"]])
        if c >= float(e["companion_threshold"]):
            v = choice["choice"]
            return v, {str(v): choice["probabilities"][v] * c * _prod_not(earlier)}
        earlier.append(c)
        before = _prod_not(earlier)
        fallen: list[float] = []
        for value, expr in cast("list[list[Any]]", e.get("fallback") or []):
            p = _expr_p(expr, rawp)
            if p >= thr:
                return value, {str(value): p * before * _prod_not(fallen)}
            fallen.append(p)
        nv = e["none_value"]
        return nv, {str(nv): before * _prod_not(fallen)}
    v = choice["choice"]
    return v, {str(v): choice["probabilities"][v] * _prod_not(earlier)}


def compose(native: Native, body: dict[str, Any],
            schema: CompiledSchema) -> tuple[systemone.Answer, dict[str, Any] | None]:  # fmt: skip
    """A reply mapped to `systemone.Answer` (classification validated as strictly as the
    Claude-style path) and every question's raw answer; classification None on any fault."""
    usage = body.get("usage")
    tokens = cast("dict[str, Any]", usage).get("input_tokens") if isinstance(usage, dict) else None
    n_in = tokens if isinstance(tokens, int) and not isinstance(tokens, bool) else None
    rawa = raw(native, body)
    if rawa is None:
        return systemone.Answer(None, {}, n_in), None
    rawp = {k: v for k, v in rawa.items() if isinstance(v, float)}
    out: dict[str, Any] = {}
    probs: dict[str, dict[str, float]] = {}
    for name, spec in native.fields.items():
        f = schema.fields[name]
        comp = spec["compose"]
        if comp == "noul":
            p = _expr_p(spec["from"], rawp)
            out[name] = p >= systemone.NOUL_TRUE
            probs[name] = {"true": p}
        elif comp == "choice":
            a = cast("dict[str, Any]", rawa[spec["from"]])
            out[name] = a["choice"]
            probs[name] = dict(cast("dict[str, float]", a["probabilities"]))
        elif comp == "score":
            ps = cast("list[float]", cast("dict[str, Any]", rawa[spec["from"]])["probabilities"])
            out[name] = f.values[max(range(len(ps)), key=lambda i: (ps[i], i))]  # ties higher
            probs[name] = dict(zip(f.values, ps, strict=True))
        else:
            out[name], probs[name] = _cascade(spec, rawa | rawp)
    try:
        cls = schema.validate(out).model_dump(mode="json")
    except ValueError:
        cls = None
    return systemone.Answer(cls, probs, n_in), rawa


def thresholds(native: Native) -> dict[str, set[float]]:
    """Each Noul's composing thresholds: 0.5 under `noul` and the yes/no expressions, the
    cascade's threshold in its steps and fallbacks, the companion's own (R19)."""
    out: dict[str, set[float]] = {}

    def add(expr: Any, t: float) -> None:
        for i in _expr_ids(expr, "thresholds"):
            out.setdefault(i, set()).add(t)

    for spec in native.fields.values():
        if spec["compose"] == "noul":
            add(spec["from"], systemone.NOUL_TRUE)
        elif spec["compose"] == "cascade":
            thr = float(spec["threshold"])
            for _v, expr in cast("list[list[Any]]", spec["steps"]):
                add(expr, thr)
            els = spec["else"]
            if isinstance(els, dict):
                e = cast("dict[str, Any]", els)
                if "companion" in e:
                    add(e["companion"], float(e["companion_threshold"]))
                for _v, expr in cast("list[list[Any]]", e.get("fallback") or []):
                    add(expr, thr)
    return out


def _top(ps: list[float]) -> int:
    return max(range(len(ps)), key=lambda i: (ps[i], i))


def raw_flips(native: Native, a: dict[str, Any], b: dict[str, Any],
              bands: dict[str, float]) -> tuple[list[str], int]:  # fmt: skip
    """Between two raw answers to one case (R19): the questions that flipped (a Noul crossing
    one of its composing thresholds; a Choice or Score changing its top option or level), and
    the band crossings (a banded field's question crossing its band's lower bound)."""
    flips: list[str] = []
    crossings = 0
    thr = thresholds(native)
    # the native none bar (diff review F8): p(none) = 1 - the largest raw p of `else_p_steps`;
    # a crossing of its bar is a crossing of 1 - bar by that largest p
    bar = native.mapping.none_bars.get("fraud_risk")
    fr = native.fields.get("fraud_risk") or {}
    if bar is not None and fr.get("else_p_steps"):
        steps = {str(st[0]): st[1] for st in cast("list[list[Any]]", fr["steps"])}
        pa = {k: v for k, v in a.items() if isinstance(v, float)}
        pb = {k: v for k, v in b.items() if isinstance(v, float)}
        exprs = [steps[s] for s in cast("list[str]", fr["else_p_steps"])]
        try:
            ma, mb = (max(_expr_p(e, p) for e in exprs) for p in (pa, pb))
        except KeyError:
            ma = mb = 0.0
        thr_fr = float(fr["threshold"])
        none_a, none_b = (all(_expr_p(st[1], p) < thr_fr
                              for st in cast("list[list[Any]]", fr["steps"]))
                          for p in (pa, pb))  # fmt: skip
        if (none_a or none_b) and (ma > 1 - bar) != (mb > 1 - bar):  # the bar applied (L2)
            crossings += 1
    for qid, va in a.items():
        vb = b.get(qid)
        if isinstance(va, float) and isinstance(vb, float):
            if any((va >= t) != (vb >= t) for t in thr.get(qid, ())):
                flips.append(qid)
            if qid in bands and (va >= bands[qid]) != (vb >= bands[qid]):
                crossings += 1
        elif isinstance(va, dict) and isinstance(vb, dict):
            da, db = cast("dict[str, Any]", va), cast("dict[str, Any]", vb)
            if "choice" in da:
                if da.get("choice") != db.get("choice"):
                    flips.append(qid)
            else:
                pa = cast("list[float]", da["probabilities"])
                pb = cast("list[float]", db["probabilities"])
                if _top(pa) != _top(pb):
                    flips.append(qid)
    return flips, crossings


def options_record(native: Native, *, state: str, reverse: bool) -> dict[str, Any]:
    """What a native run records in `summary.jev.options` (Q1, R7)."""
    return {"questions": "native", "questions_digest": native.digest, "state": state,
            "reverse_choices": reverse, "mapping": native.mapping_doc,
            "mapping_digest": jev_mapping.digest(native.mapping)}  # fmt: skip
