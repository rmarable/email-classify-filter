"""Decision models through Ollama's `/v1/systemone` (SPEC §7.8; OD-470, OD-471; eval only).

A decision model answers typed questions about a `state` with a probability per option. ecf asks it
the classifier's eight questions about one email and maps the answers to a classification that goes
through the same strict `parse` as Gemma's, so rules, policy and the actor see no difference.

- **Questions** come from the schema: an enum field is a `choice` whose criteria are the value
  descriptions, an ordinal field a `score` whose criteria are the level names only (Gemma's prompt
  has no per-level text either), a boolean a `noul`. Each question's instructions are the field's
  description, the text `schema.prompt_block` gives Gemma.
- **Answers:** a `choice` is the chosen key; a `score` is the most probable level, ties going to the
  higher one (fails toward more risk; the endpoint's own `score` is an expected value); a `noul` is
  true at p >= 0.5.
- **State:** the endpoint has no instruction field, so the classifier's untrusted-data text (OD-255)
  is prepended to the delimited excerpt (measured 2026-10-07: it stopped the one category hijack and
  cut fraud-risk under-rating from 3 cards to 1, §21.2).
- **Failures:** an answer that doesn't map, or a request over the model's context (HTTP 400; the
  endpoint never truncates), is a failed attempt like a Gemma schema failure. Probabilities are
  returned for calibration only, never for routing (OD-054 wording).
- **Never production:** nothing in the service's classifier path imports this module.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from importlib import resources
from typing import Any, cast

from ecf.errors import InvalidInputError
from ecf.schema import CompiledSchema, FieldKind
from ecf_server import classifier
from ecf_server.ollama import Client, OllamaError, Pin

NOUL_TRUE = 0.5
# the classifier's instructions up to its JSON-answer line: the untrusted-data text (OD-255)
PREAMBLE = classifier.INSTRUCTIONS.split("\n\nAnswer with")[0]


@dataclass(frozen=True)
class Answer:
    classification: dict[str, Any] | None  # None: a failed attempt
    probabilities: dict[str, dict[str, float]]  # field -> option -> p (noul: {"true": p})
    input_tokens: int | None


LOCK = "decision_models.lock"


def load_pins() -> dict[str, Pin]:
    """The eval-only decision models (`data/decision_models.lock`), by ecf's short name. Only
    entries in the classifier role and marked eval-only are read (OD-470)."""
    raw = json.loads(resources.files("ecf_server.data").joinpath(LOCK).read_text("utf-8"))
    out: dict[str, Pin] = {}
    for name, m in cast("dict[str, dict[str, Any]]", raw["models"]).items():
        if m.get("role") == "classifier" and m.get("eval_only") is True:
            out[name] = Pin(tag=m["tag"], digest=m["digest"], ecf_name=m["ecf_name"])
    return out


def pin(name: str) -> Pin:
    """The pin for `name`; an unknown name is refused (never an arbitrary Ollama model)."""
    pins = load_pins()
    if name not in pins:
        known = ", ".join(sorted(pins)) or "none"
        raise InvalidInputError(f"no decision model {name!r} in {LOCK} (known: {known})")
    return pins[name]


def verify(client: Client, p: Pin) -> str:
    """ecf's copy of the decision model carries the pinned digest; returns it. Raises
    OllamaError (`model_missing` or `digest_mismatch`) otherwise."""
    got = client.digests().get(p.ecf_tag)
    if got is None:
        raise OllamaError("model_missing", p.ecf_tag)
    if got != p.digest:
        raise OllamaError("digest_mismatch", f"{p.ecf_tag} is {got[:12]}, pinned {p.digest[:12]}")
    return got


def questions(schema: CompiledSchema) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for f in schema.fields.values():
        if f.kind is FieldKind.ENUM:
            crit = dict(zip(f.values, f.value_descriptions, strict=True))
            out[f.name] = {"type": "choice", "instructions": f.description, "criteria": crit}
        elif f.kind is FieldKind.ORDINAL:
            out[f.name] = {"type": "score", "instructions": f.description,
                           "criteria": list(f.values)}  # fmt: skip
        else:
            out[f.name] = {"type": "noul", "instructions": f.description}
    return out


def state(text: str, token: str) -> str:
    return (f"{PREAMBLE}\n\n<<<EMAIL {token}>>>\n{classifier.fit(text)}\n"
            f"<<<END EMAIL {token}>>>")  # fmt: skip


def _prob(v: Any) -> float | None:
    return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else None


def map_answers(raw: dict[str, Any], schema: CompiledSchema) -> Answer:
    """The classification (validated) and the probabilities; a classification of None when any
    answer is missing or doesn't map."""
    answers = raw.get("answers")
    usage = raw.get("usage")
    tokens = cast("dict[str, Any]", usage).get("input_tokens") if isinstance(usage, dict) else None
    n_in = tokens if isinstance(tokens, int) and not isinstance(tokens, bool) else None
    if not isinstance(answers, dict):
        return Answer(None, {}, n_in)
    got = cast("dict[str, Any]", answers)
    out: dict[str, Any] = {}
    probs: dict[str, dict[str, float]] = {}
    ok = True
    for f in schema.fields.values():
        a = got.get(f.name)
        if not isinstance(a, dict):
            ok = False
            continue
        ans = cast("dict[str, Any]", a)
        if f.kind is FieldKind.BOOLEAN:
            p = _prob(ans.get("noul"))
            if p is None:
                ok = False
                continue
            probs[f.name] = {"true": p}
            out[f.name] = p >= NOUL_TRUE
            continue
        raw_p = ans.get("probabilities")
        p_map = cast("dict[str, Any]", raw_p) if isinstance(raw_p, dict) else {}
        if f.kind is FieldKind.ENUM:
            probs[f.name] = {k: v for k, v in ((k, _prob(p_map.get(k))) for k in f.values)
                             if v is not None}  # fmt: skip
            out[f.name] = ans.get("choice")
        else:  # ordinal: keys are level indexes as strings
            levels = [_prob(p_map.get(str(i))) for i in range(len(f.values))]
            if any(p is None for p in levels):
                ok = False
                continue
            ps = [p for p in levels if p is not None]
            probs[f.name] = dict(zip(f.values, ps, strict=True))
            out[f.name] = f.values[max(range(len(ps)), key=lambda i: (ps[i], i))]
    if not ok:
        return Answer(None, probs, n_in)
    return Answer(classifier.parse(json.dumps(out), schema), probs, n_in)


def ask(client: Client, model: str, text: str, schema: CompiledSchema) -> Answer:
    """One decision-model classification of one excerpt. Raises OllamaError for faults; a request
    over the model's context is a failed attempt."""
    try:
        raw = client.systemone(model, state(text, secrets.token_hex(8)), questions(schema))
    except OllamaError as e:
        if e.cause == "http" and e.detail == "HTTP 400":
            return Answer(None, {}, None)
        raise
    return map_answers(raw, schema)
