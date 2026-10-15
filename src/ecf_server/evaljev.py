"""Jev in `ecf eval run --classifier-backend jev` (v3.0.0 step 2d; planning-docs/JEV-STEP2.md §6;
OD-503, OD-504). Eval only.

`evalrun` runs the cases; this module asks Jev for each one through `jev.Client`. It lives apart
because `evalrun` is imported by modules the classifier imports, and the classifier never imports
`jev`, indirectly included (an import-linter contract, P9): the eval route builds the asker here
(`factory`) and passes it to `evalrun.start`, and `/ecf-eval --preset D` reads the pin and prompt
digest it matches its source run on from here (`source_inputs`).

- **The card as Jev gets it (D1, OD-504):** what Claude's classifier gets: the sender, subject,
  `Date:` header, attachment metadata and the classifier excerpt, redacted unless the run is
  `--no-redact` (synthetic `--fraud-only` only, P2).
- **Stops (§5.4):** `evalrun.FATAL` (auth, pin, quota, long wait) fails the run with no result; a
  spent waiting budget or `ecf eval stop` keeps the cases scored so far.
- **Remapping (step 3, §3.4):** `remap` scores a Jev result again under another mapping from its
  recorded probabilities, in process and with no TypeSafe call, through the same plan and hold as
  a live run (`evalrun.planned`, `evalrun.held`); `scripts/jev_sweep.py` fits step 3's bands and
  bars with it.
- **Determinism (§6):** a repeat agrees when its classification is the same and every probability
  is within `evalrun.PROB_TOLERANCE`; each pass's concurrency is recorded (P23). A pair where
  either pass faulted is left out and counted (`faulted_pairs`, J3); each repeat is a new request
  with a fresh delimiter token.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from ecf.eval.results import CaseResult, ResultFile
from ecf.schema import CompiledSchema
from ecf_server import jev, jev_mapping, policy, rules, ruletest, triggers
from ecf_server.clock import Clock
from ecf_server.evalrun import (
    FATAL,
    PROB_TOLERANCE,
    RUN,
    Asked,
    Case,
    Options,
    case_inputs,
    field_flips,
    held,
    load,
    planned,
    same_answer,
    score,
    spread,
)

# TypeSafe's price (/models, read 2026-10-14): input tokens only, output free (§5.5, P21)
COST_PER_M_INPUT = 0.042
COST_BASIS = ("$0.042 per million input tokens (/models, read 2026-10-14) over"
              " input_tokens_billed: every 200's usage, repeats and probes included; replies"
              " other than 200 aren't counted (their billing is unknown), nor 200s without usage"
              " (usage_missing)")  # fmt: skip


def case_email(case: Case, text: str) -> jev.Email:
    """What Claude's classifier gets from a synthetic card (`claude_eval._prepare`'s fields), for
    Jev (D1, OD-504): the sender, subject, `Date:` header, attachment metadata and `text`, the
    stored classifier excerpt (redacted unless the run is `--no-redact`)."""
    msg = ruletest.read_case(case.path.read_bytes()).parsed
    atts = tuple(jev.Attachment(a.name, a.content_type, a.size) for a in msg.attachments
                 if not a.inline or a.name)  # fmt: skip
    return jev.Email(msg.from_name or "", msg.from_addr or "", msg.subject or "",
                     (msg.headers.get("date") or ("",))[0], text, atts)  # fmt: skip


@dataclass(frozen=True)
class Setup:
    """What a Jev run needs from the route (P12): the key's reader, re-read before each try and
    at each probe; TypeSafe's transport (None: the real one; a fake in tests); the wait (None: the
    run's stop event, so `ecf eval stop` ends a pause; a fake clock's in tests); the run's
    waiting budget (a placeholder, set in step 3)."""

    key: Callable[[], str | None]
    transport: httpx.BaseTransport | None = None
    wait: Callable[[float], bool] | None = None
    budget_s: float = jev.WAIT_BUDGET_S


def factory(setup: Setup) -> Callable[[Options, Clock], JevAsker]:
    """`evalrun.start`'s `asker` for a Jev run."""
    return lambda opts, clock: JevAsker(opts, clock, setup)


def source_inputs(schema: CompiledSchema) -> tuple[str, str]:
    """The pin and the default-options prompt digest a preset D source run must have (D5)."""
    pin = jev.load_pin()
    return pin, jev.prompt_digest(schema, pin=pin)


class JevAsker:
    """A Jev run's client and its figures (`evalrun.Asker`)."""

    def __init__(self, opts: Options, clock: Clock, setup: Setup) -> None:
        self.opts = opts
        self.client = jev.Client(setup.key, transport=setup.transport, clock=clock,
                                 stop=RUN.stop, wait=setup.wait, on_pause=self._paused,
                                 budget_s=setup.budget_s,
                                 concurrency=opts.jev_concurrency, pin=opts.jev_pin,
                                 variants=frozenset(opts.jev_questions))  # fmt: skip
        self.pin = self.client.pin
        # until a request gets a reply classed ok or request error (not a provider fault): a 404
        # or 422 then stops the run (§5.3, J11)
        self.first = True
        self.end_to_end_ms: list[float] = []  # cases answered
        self.end_to_end_faulted_ms: list[float] = []  # faulted cases, apart (J13)

    @staticmethod
    def _paused(line: str) -> None:
        RUN.set(detail=line)  # the medium `Retry-After` tier, on the progress line (§5.4)

    def close(self) -> None:
        self.client.close()

    def fault_limit(self, n: int) -> int:
        return jev.fault_limit(n)

    def stop_reason(self, exc: Exception) -> str | None:
        """`budget` or `stopped` for a Jev stop that keeps a partial result; None otherwise."""
        if isinstance(exc, jev.JevError) and exc.cls not in FATAL:
            return "budget" if exc.cls == "budget" else "stopped"
        return None

    def fatal(self, exc: Exception) -> str | None:
        """The class of a Jev stop that fails the run (FATAL); None otherwise."""
        return exc.cls if isinstance(exc, jev.JevError) and exc.cls in FATAL else None

    def ask(self, schema: CompiledSchema, case: Case, text: str,
            sent: tuple[str, ...]) -> Asked:  # fmt: skip
        email = case_email(case, text)  # the `.eml` parse is not Jev's time (J13)
        t0 = time.monotonic()
        res = self.client.classify(schema, email, sent, first=self.first,
                                   redact=self.opts.redact)  # fmt: skip
        took = 1000 * (time.monotonic() - t0)
        (self.end_to_end_ms if res.fault is None else self.end_to_end_faulted_ms).append(took)
        self.first = self.first and not res.replied
        recorded: dict[str, Any] = {
            "probabilities": res.probabilities or None, "classifier_ms": res.classifier_ms,
            "fault": res.fault, "status": res.status if res.fault is not None else None,
            "tries": res.tries, "wait_ms": res.wait_ms, "queue_ms": res.queue_ms,
            "concurrency": res.concurrency, "confidence": res.confidence or None,
            "input_tokens": res.input_tokens, "output_tokens": res.output_tokens,
        }  # fmt: skip
        return Asked(res.classification, res.probabilities or None, res.fault, recorded,
                     res.concurrency)  # fmt: skip

    def repeats(self, schema: CompiledSchema, cases: list[Case], repeat: set[str],
                firsts: dict[str, Asked], scratch: ruletest.Scratch,
                redact: Callable[[str], str], opts: Options,
                ) -> tuple[int, dict[str, Any]]:  # fmt: skip
        """The determinism repeats (§16.5): the sampled cases asked again, each a new request with
        a fresh delimiter token; how many differ, and the flips per field (`probs` when only a
        probability moved) with each pass's concurrency (P23). A pair where either pass faulted
        isn't compared (a fault isn't an answer): it is listed in `faulted_pairs`, and a case
        whose first pass faulted isn't asked again (J3)."""
        diffs = 0
        flipped: dict[str, int] = {}
        conc: dict[str, list[int | None]] = {}
        faulted: list[str] = []
        for case in (c for c in cases if c.id in repeat and c.id in firsts):
            first = firsts[case.id]
            if first.fault is not None:
                faulted.append(case.id)
                continue
            facts, (text, _act) = case_inputs(case, scratch, redact)
            again = self.ask(schema, case, text, opts.sent(facts))
            conc[case.id] = [first.concurrency, again.concurrency]
            if again.fault is not None:
                faulted.append(case.id)
                continue
            if not same_answer(first, again):
                diffs += 1
                for f in field_flips(first.classification, again.classification) or ["probs"]:
                    flipped[f] = flipped.get(f, 0) + 1
        compared = sorted(c for c in conc if c not in faulted)
        return diffs, {"cases": compared, "faulted_pairs": sorted(faulted),
                       "field_flips": flipped, "prob_tolerance": PROB_TOLERANCE,
                       "concurrency": conc, "tokens": "fresh per repeat"}  # fmt: skip

    def inputs(self, schema: CompiledSchema, rules_now: rules.CompiledRules) -> dict[str, Any]:
        """What the run asked with (P13): top-level, so `ecf eval compare` prints differences."""
        pin = self.pin
        return {
            "prompt_digest": jev.prompt_digest(schema, pin=pin, variants=self.client.variants),
            "input_format": jev.state_format_id(),
            "excerpt_cap": jev.excerpt_cap(),
            "gate_key": jev.gate_key(schema, rules_now, pin=pin, variants=self.client.variants),
        }  # fmt: skip

    def figures(self, schema: CompiledSchema, faulted: dict[str, str],
                determinism: dict[str, Any] | None) -> dict[str, Any]:  # fmt: skip
        """`summary.jev` (§5.5): the client's figures, the end-to-end time per case (answered and
        faulted apart, J13), the faulted cases by type, the repeats' concurrency and the cost from
        `input_tokens_billed` (200s without `usage` counted in `usage_missing`, J6)."""
        billed = self.client.stats.input_tokens_billed
        by_type: dict[str, int] = {}
        for f in faulted.values():
            by_type[f] = by_type.get(f, 0) + 1
        return self.client.stats.summary() | {
            "pin": self.pin,
            "options": {"state": "s1", "preamble": "state", "fraud_risk": "choice",
                        "concurrency": self.opts.jev_concurrency,
                        "questions": list(self.opts.jev_questions)},
            "schema_digest": schema.digest,
            "end_to_end_ms": spread(self.end_to_end_ms),
            "end_to_end_faulted_ms": spread(self.end_to_end_faulted_ms),
            "faulted_cases": by_type,
            "determinism_concurrency": (determinism or {}).get("concurrency"),
            "cost": {"usd": round(billed * COST_PER_M_INPUT / 1e6, 6), "basis": COST_BASIS},
        }  # fmt: skip


@dataclass(frozen=True)
class RemapInputs:
    """The cards and their facts, computed once for many remaps (as a run computes them)."""

    cases: dict[str, tuple[Case, dict[str, Any]]]
    rules: rules.CompiledRules
    known: frozenset[str]


def remap_inputs(root: Path, clock: Clock, schema: CompiledSchema) -> RemapInputs:
    cases, _version = load(root, fraud_only=False)
    rules_now = rules.load_starter_rules(schema)
    scratch = ruletest.Scratch(clock)
    try:
        out = {c.id: (c, case_inputs(c, scratch, triggers.redact_injection)[0]) for c in cases}
    finally:
        scratch.close()
    return RemapInputs(out, rules_now, policy.labels(schema, rules_now))


def remap(result: ResultFile | list[CaseResult], inputs: RemapInputs,
          mapping: jev_mapping.JevMapping) -> list[CaseResult]:  # fmt: skip
    """A Jev result's cases scored again under `mapping`: each recorded classification planned
    and held as a live run would (no actor: a Jev run has none). A case with no answer stays a
    failure; a card no longer in the set is left out."""
    out: list[CaseResult] = []
    for r in result.cases if isinstance(result, ResultFile) else result:
        if (found := inputs.cases.get(r.id)) is None:
            continue
        case, facts = found
        cls = {k: v for k, v in r.got.items() if k != "rule"} or None
        if cls is None or r.fault is not None:
            out.append(score(case, None, None, None).model_copy(update={"fault": r.fault}))
            continue
        derived = jev_mapping.derive(cls, r.probabilities, mapping)
        ctx, plan, _blocked = planned(case, cls, facts, inputs.rules, inputs.known)
        plan, band = held(ctx, plan, derived, inputs.known, inputs.rules)
        out.append(score(case, cls, plan, None, derived).model_copy(
            update={"probabilities": r.probabilities} | band))  # fmt: skip
    return out
