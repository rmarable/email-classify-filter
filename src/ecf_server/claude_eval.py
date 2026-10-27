"""`/ecf-eval`: the synthetic set through Claude (SPEC §10.4, §16.2-§16.3; V1.4 step 7; operator
decisions 2026-10-02, OD-287 to OD-292).

- **A run is registered from the command line** (`ecf eval run --claude`, OD-288), never through
  MCP: preset C or D, the sensitivity it runs as, `--fraud-only`, other models for the
  classifier or actor roles, and how many items a `-high` spawn takes (`--batch`). One run at a
  time. The cases are prepared in a service thread (ecf's own analysis and the excerpts, as `ecf
  eval run` does: about 40 s for the 158-case set); each becomes claimable as soon as it is
  ready.
- **The same agents and tools** (OD-287): `eval_next` claims cases like `review_queue` claims
  items, under a random reference (never the case ID, whose name gives the answer away), and the
  subagents read and submit through `get_message`, `record_classification` and `propose_action`,
  as in `/ecf-review`. Claims, fencing, the 3 invalid tries and the telemetry model check are the
  same; only an agent server reads or submits, so the main session never sees case text (OD-307),
  and no gold label leaves the service.
- **Models** (OD-288): a run on the pins in force uses the production agents (`ecf-classifier`,
  ...) and is keyed by the preset's pin key, so it counts for the go-live gate (§9.3). A run with
  other models gets the `ecf-eval-*` agents (the same prompts, rendered by `ecf claude` on the
  run's models), is keyed `eval-...`, and is for comparison only.
- **Preset B** (OD-289), which took Gemma's classifications from an `ecf eval run`, was removed
  with Gemma in v3.0.0 (ADR 0031); its result files still load and rescore (R-D12).
- **Preset D** (v3.0.0 step 2d; D5, P15; step 4, P8): Jev's classifications from the latest
  `ecf eval run --classifier-backend jev` that is complete, redacted (`redact` true), not marked
  not for the gate nor an experiment, on the pinned Jev version, its prompt digest, the default
  schema and this version of the set, with every case of this run and its result file present;
  Claude is the actor. A faulted Jev case (no answer) is scored as a failure (P1). On the pinned
  actors, and with a source run whose gate key is the one the service stored for D
  (`jev_keys.stored`), it is keyed by D's pin key and counts for D's gate (`claude_pins.PAIR`,
  step 4); otherwise it is comparison only (an `eval-...` key). Its schema, prompt digest, input
  format and excerpt cap are the source run's, and the summary names the source run and pin
  (`jev_source`) for the D Check.
- **Frozen classifications** (v3.0.0 step 6, P2, P3; OD-518): a preset C run with
  `--source-run` takes each case's classification from that `/ecf-eval` run (preset C, any
  classifier model, not itself frozen, complete, not an experiment, on this version of the set,
  the effective schema and this sensitivity, its result file present with every case of this
  run) and asks only the actor.
  It is comparison only, in code: unpinned, keyed `eval-...` over the actor models, the schema
  and the source run's key, its summary marked without a classifier and not gate-eligible; no
  `--classifier-model`, no determinism repeats. Its schema, prompt digest, read bytes and input
  format are the source run's, and the summary names the source (`classifier_source`).
- **Scoring** is `ecf eval run`'s (`evalrun.score`, the starter rules, policy, the safety checks),
  with the actor's proposal recorded and judged on its own too (`proposal_safety`, step 6, P1);
  the result file and `eval_runs` row have its format, plus the run's Claude figures. A run that
  makes its own classifications (preset C without a source) classifies its first 10 cases twice
  (determinism, §16.5).
- The run lives in service memory: a claim ends with its session and the run carries on in the
  next `ecf claude` session; a service restart loses it (no result saved). It expires 24 hours
  after it was registered; `ecf eval stop` ends it and saves what was scored (OD-291).
"""

from __future__ import annotations

import fnmatch
import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast

from ecf.errors import (
    ConflictError,
    ForbiddenProfileError,
    InvalidInputError,
    NotFoundError,
)
from ecf.eval import genset
from ecf.eval.results import (
    CaseResult,
    ClaimOutcome,
    ClaimRecord,
    ClaimStage,
    ResultFile,
    load_result,
    spawn_figures,
)
from ecf.ids import new_random_id
from ecf.plugin_digest import actor_prompt_digest, plugin_digest
from ecf.schema import CompiledSchema, read_bytes
from ecf_server import (
    actor,
    alerts,
    claude_pins,
    claude_review,
    claude_usage,
    config,
    evalrun,
    excerpt,
    jev_facts,
    jev_keys,
    jev_mapping,
    policy,
    rules,
    ruletest,
    signals,
    slack_admin,
    telemetry,
    triggers,
)
from ecf_server.clock import Clock, to_ts
from ecf_server.log_bridge import log
from ecf_server.message import ACTOR_CHARS, CLASSIFIER_CHARS
from ecf_server.notify import Notifier
from ecf_server.telemetry import Hold, Seen, Telemetry

EXPIRY = timedelta(hours=24)
BATCH_MAX = 5
ROLES = ("classifier", "classifier_high", "actor", "actor_high")
# the Claude roles each preset's eval uses: its gate pins (D's classifier is Jev)
USED_ROLES = {"C": claude_pins.GATE_ROLES["C"], "D": claude_pins.GATE_ROLES["D"]}
D_PAIR = claude_pins.PAIR["D"]  # a D run's `eval_runs.pair`
CLAUDE_INPUT_FORMAT = "claude-v1"  # a C run's `input_format`, so Jev vs C prints the difference
# the inputs a D run takes from its source Jev run (P13)
SOURCE_KEYS = ("schema_digest", "prompt_digest", "input_format", "excerpt_cap")
# and those a C run on frozen classifications takes from its source C run (step 6, P2)
C_SOURCE_KEYS = ("schema_digest", "prompt_digest", "read_bytes", "input_format", "excerpt_cap")
# `--classifier-window` (window tuning step 2): the characters the classifier may read
# (8,000 is the widest the plan compares; a wider one waits for a plan that needs it)
WINDOW_MIN, WINDOW_MAX = 500, 8000
ACTOR_ROLES = ("actor", "actor_high")
NO_RUN = ("no Claude eval is waiting: register one with `ecf eval run --claude`, then open"
          " `ecf claude`")  # fmt: skip


@dataclass(frozen=True)
class Options:
    root: Path
    preset: str = "C"  # C, or D (Jev's classifications, Claude's actor; comparison only)
    sensitivity: str = "standard"  # the address sensitivity the cases run as
    fraud_only: bool = False
    classifier_model: str | None = None
    actor_model: str | None = None  # both actor roles, or only `actor` with `actor_high_model`
    actor_high_model: str | None = None  # `actor_high` alone (step 6, P17)
    batch: int = 1  # items per `-high` spawn
    copy_to: Path | None = None  # also write the result file here (plan 0.3)
    # preset C on frozen classifications (step 6, P2): the `/ecf-eval` run (ID or a prefix of
    # 8 or more characters) whose classification of each case it takes, asking only the actor
    source_run: str | None = None
    # False: an experiment (`--not-for-gate`), and always on a generated set (eval plan P1): run
    # on the eval agents, never pinned, so the gate never reads it, and the gate's set stays put
    for_gate: bool = True
    # window tuning (step 2): the classifier's excerpt cut at this many characters instead of
    # the default, and only the cards whose ids match these globs; either makes an experiment
    classifier_window: int = CLASSIFIER_CHARS
    cases: tuple[str, ...] = ()


Outcome = ClaimOutcome


@dataclass
class Claim:
    """One claim of a case, for the run's timing figures (v2.2.0 plan item 2): closed values,
    counters and service-side times only, all from the injected clock."""

    stage: ClaimStage
    spawn: str  # the label `eval_next` gave it; unique within its session and round
    round: int  # the run's rounds that handed out items, from 1, across sessions
    session: int  # the session's ordinal in `Run.sessions`, from 1
    limit: int  # the round's `limit`
    items: int  # how many items the round handed out
    preparing: bool  # the run was still preparing cases
    claimed: datetime
    read: datetime | None = None  # the first `get_message`
    answered: datetime | None = None  # the valid submission (`Hold.at`'s moment)
    settled: datetime | None = None  # the held submission applied or refused
    outcome: Outcome | None = None
    high_risk: tuple[policy.HighRiskReason, ...] = ()  # an act claim's reasons (§8.2)
    # an agent called under it, or named it with a mixed-up token: its spawn runs (OD-501)
    called: bool = False
    invalid: int = 0  # submissions refused before its outcome (S4, P19: M2's criterion 1)


@dataclass
class Work:
    """One case of the run and its claim."""

    case: evalrun.Case
    ref: str
    stage: str = "preparing"  # preparing | classify | act | repeat | done
    facts: dict[str, Any] = field(default_factory=dict[str, Any])
    email: dict[str, Any] = field(default_factory=dict[str, Any])
    cls_text: str = ""
    act_text: str = ""
    # where the case's text stands against the window (`evalrun.measured`, OD-555)
    measured: dict[str, Any] = field(default_factory=dict[str, Any])
    classification: dict[str, Any] | None = None
    derived: jev_mapping.Derived = jev_mapping.NO_BAND  # preset D: its source case's (step 3)
    plan: policy.Plan | None = None
    # the rule's own action names when its plan went to the actor (step 6, P1): what the
    # proposal-level checks read, since the proposal is appended to the plan
    before_actor: frozenset[str] | None = None
    proposal: dict[str, str] | None = None
    result: CaseResult | None = None
    # the claim
    session: str | None = None
    token_hash: str = ""
    hashes: set[str] = field(default_factory=set[str])  # its earlier claims' tokens (OD-501 P4)
    fence: int = 0
    expires: datetime | None = None
    claim: str = "free"  # free | claimed | held
    need: str = ""
    agent: str = ""
    invalid: int = 0
    claims: list[Claim] = field(default_factory=list[Claim])  # every claim, the current last
    # the reply to the last submission that ended a claim: (session, fence, token hash, agent,
    # reply); a resubmission with that token gets it again (a lost reply, not a new answer)
    last: tuple[str, int, str, str, str, bool, dict[str, Any]] | None = None  # + digest, ended
    held_digest: str = ""  # the held submission's payload digest


@dataclass
class Run:
    run_id: str
    opts: Options
    models: dict[str, str]  # role -> model ID for this run
    pinned: bool
    key: str  # eval_runs.digest: the pin key, or eval-... for a comparison run
    version: str
    created: datetime
    works: list[Work]
    rules: rules.CompiledRules
    known: frozenset[str]
    data_dir: Path
    schema: CompiledSchema  # the effective schema at registration (with any extension)
    # the run whose classifications it uses: preset D's Jev `ecf eval run`, or a frozen C run's
    # `/ecf-eval` run (step 6, P2)
    source_run: str | None = None
    source: dict[str, Any] = field(default_factory=dict[str, Any])  # D: its SOURCE_KEYS
    source_key: str | None = None  # the source run's `eval_runs.digest`
    state: str = "preparing"  # preparing | ready | done | stopped | expired | failed
    detail: str = ""
    sessions: list[str] = field(default_factory=list[str])
    # session -> its ended claims, each reported once by eval_next
    outcomes: dict[str, list[dict[str, str]]] = field(default_factory=dict)  # pyright: ignore[reportUnknownVariableType]
    repeats_started: bool = False
    diffs: int = 0
    rounds: int = 0  # `eval_next` replies that handed out items
    # session -> agent -> until when an expired claim's agent may still be running
    grace: dict[str, dict[str, datetime]] = field(default_factory=dict)  # pyright: ignore[reportUnknownVariableType]
    # session -> token hash -> its claim's expiry: tokens a mix-up freed, still mix-up tokens
    freed: dict[str, dict[str, datetime]] = field(default_factory=dict)  # pyright: ignore[reportUnknownVariableType]
    # session -> token hash -> the unread free that ended it (OD-501 P2)
    unread: dict[str, dict[str, claude_review.Unread]] = field(default_factory=dict)  # pyright: ignore[reportUnknownVariableType]
    late: int = 0  # freed tokens a late agent presented (review R1): each a false free
    # session -> rounds handed out since its last `eval_next`; round -> that next call (OD-501)
    awaiting: dict[str, list[int]] = field(default_factory=dict)  # pyright: ignore[reportUnknownVariableType]
    next_call: dict[int, datetime] = field(default_factory=dict)  # pyright: ignore[reportUnknownVariableType]
    summary: dict[str, Any] | None = None
    stop: threading.Event = field(default_factory=threading.Event)
    lock: threading.RLock = field(default_factory=threading.RLock)

    @property
    def by_ref(self) -> dict[str, Work]:
        return {w.ref: w for w in self.works}

    @property
    def open(self) -> bool:
        return self.state in ("preparing", "ready")


@dataclass
class _Slot:
    """The one run, in memory."""

    run: Run | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


_SLOT = _Slot()


def current() -> Run | None:
    with _SLOT.lock:
        return _SLOT.run


def reset() -> None:
    """Forget the run (tests; a service start has none)."""
    with _SLOT.lock:
        _SLOT.run = None


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8", "surrogateescape")).hexdigest()


# Agents and models: the agent name for each role (`ecf-eval-*` for a comparison run, else the
# pinned `ecf-*`), and the model each role runs, which `ecf claude` renders as agents.


def agent_name(role: str, pinned: bool) -> str:
    return f"ecf-{'' if pinned else 'eval-'}{role.replace('_', '-')}"


def role_of(agent: str) -> str:
    return agent.removeprefix("ecf-").removeprefix("eval-").replace("-", "_")


def models_for(conn: sqlite3.Connection, opts: Options,
               source_key: str | None = None) -> tuple[dict[str, str], bool, str]:  # fmt: skip
    """The run's model per role, whether they are the pins in force for the preset's roles, and
    the run's key (the gate's pin key, or `eval-...`). A D run is pinned only when its source Jev
    run's gate key is the one the service stored for D (step 4, P8); otherwise its key binds the
    actor models, its source run's gate key and the schema."""
    eff = claude_pins.effective(conn)
    models = {r: eff[r] for r in ROLES}
    # `--actor-model` sets both actor roles unless `--actor-high-model` sets `actor_high` (P17)
    actor_roles = ACTOR_ROLES if opts.actor_high_model is None else ("actor",)
    for model, roles in ((opts.classifier_model, ("classifier", "classifier_high")),
                         (opts.actor_model, actor_roles),
                         (opts.actor_high_model, ("actor_high",))):  # fmt: skip
        if model is None:
            continue
        if claude_pins.family(model) is None:
            raise InvalidInputError(f"{model!r} isn't a Claude model ID (claude-haiku-*,"
                                    " claude-sonnet-*, claude-opus-*)")  # fmt: skip
        models |= dict.fromkeys(roles, model)
    used = used_roles(opts)
    frozen = opts.preset == "C" and opts.source_run is not None  # never a gate run (P2)
    pinned = (opts.preset in claude_pins.PAIR and not frozen and opts.for_gate
              and all(models[r] == eff[r] for r in used))  # fmt: skip
    if opts.preset == jev_keys.PRESET:  # Jev's side must be D's current one too (P8)
        pinned = pinned and source_key is not None and source_key == jev_keys.stored(conn).get(
            "gate_key")  # fmt: skip
    if pinned:
        return models, True, claude_pins.key(claude_pins.pins(conn, opts.preset))
    keyed = {r: models[r] for r in used}
    keyed["schema"] = config.current_schema(conn).digest  # as in `claude_pins.pins` (OD-478)
    keyed["actor_prompt"] = actor_prompt_digest()[:16]  # likewise (OD-523)
    if source_key is not None:
        keyed["jev" if opts.preset == jev_keys.PRESET else "source"] = source_key
    if opts.classifier_window != CLASSIFIER_CHARS:  # experiments at other windows or on a few
        keyed["classifier_window"] = str(opts.classifier_window)  # cards keep apart (W6)
    if opts.cases:
        keyed["cases"] = ",".join(opts.cases)
    return models, False, "eval-" + claude_pins.key(keyed)


def used_roles(opts: Options) -> tuple[str, ...]:
    """The Claude roles the run asks: its preset's gate roles, or the actor roles alone when a
    C run takes its classifications from a source run (step 6, P2)."""
    if opts.preset == "C" and opts.source_run is not None:
        return ACTOR_ROLES
    return USED_ROLES[opts.preset]


def agents_for(run: Run) -> dict[str, str]:
    """The `ecf-eval-*` agents `ecf claude` renders for a comparison run: name -> model."""
    if run.pinned:
        return {}
    return {agent_name(r, False): run.models[r] for r in ROLES}


# Registering a run from the command line (`ecf eval run --claude`): its source (a Jev run for
# preset D) and its cases, prepared in a thread, ready for an `ecf claude` session to work on.


def _jev_run(conn: sqlite3.Connection, version: str, schema: CompiledSchema, ids: set[str]
             ) -> tuple[str, dict[str, dict[str, Any]], dict[str, Any],
                        dict[str, jev_mapping.Derived]]:  # fmt: skip
    """Preset D's source (D5, P15): the latest `ecf eval run --classifier-backend jev` on the
    pinned Jev version, its prompt digest and `schema` (v3, the default: D9), on this version of
    the set, complete, redacted and not an experiment, its result file present, with every case
    of this run (`ids`: a `--fraud-only` source can't serve a full run, J4): its run ID, each
    case's classification (a faulted case's is empty, so it is scored as a failure, P1), its
    inputs (SOURCE_KEYS) and each case's unsure and below-bar fields, derived from its recorded
    probabilities with `jev_mapping.derive`, as the service derives them (step 4 P5; the source's
    prompt digest names the mapping). Which other sources D may take is open (removal P19)."""
    from ecf_server import evaljev  # noqa: PLC0415 - eval only, not a service import

    pin, prompt = evaljev.source_inputs(schema)
    rows = conn.execute("SELECT run_id, metrics, path FROM eval_runs WHERE pair = ? AND"
                        " set_version = ? ORDER BY created_at DESC",
                        (evalrun.pair_for(evalrun.JEV), version)).fetchall()  # fmt: skip
    for row in rows:
        m: dict[str, Any] = json.loads(row["metrics"])
        o: dict[str, Any] = m.get("options") or {}
        figures: dict[str, Any] = m.get("jev") or {}
        if (m.get("backend") != evalrun.JEV or figures.get("pin") != pin
                or m.get("prompt_digest") != prompt or m.get("schema_digest") != schema.digest
                or m.get("complete") is not True or m.get("redact") is not True):  # fmt: skip
            continue
        if o.get("for_gate") is False or o.get("switches_off") or o.get("measuring"):
            continue  # an experiment: its classifications aren't production's
        if not row["path"] or not Path(row["path"]).is_file():
            continue  # an imported run: its result file stayed on the other computer
        result = load_result(Path(row["path"]))
        if not ids <= {c.id for c in result.cases}:
            continue  # a source without some of this run's cases (a `--fraud-only` run)
        got = {c.id: {k: v for k, v in c.got.items() if k != "rule"} for c in result.cases}
        # unsure and below bar derived again from the source's probabilities by the service's
        # own function (step 4 P5), under the shipped mapping its prompt digest names
        derived = {c.id: jev_mapping.derive(got[c.id], c.probabilities) for c in result.cases}
        source = {k: m[k] for k in SOURCE_KEYS if k in m}
        det: dict[str, Any] = m.get("determinism") or {}
        if det:  # shown in the D Check (P16): the source's field flips and faulted pairs
            pairs: list[Any] = det.get("faulted_pairs") or []
            source["jev_determinism"] = {"field_flips": det.get("field_flips") or {},
                                         "faulted_pairs": len(pairs)}  # fmt: skip
        return str(row["run_id"]), got, source, derived
    raise InvalidInputError("preset D uses Jev's classifications from a complete `ecf eval run"
                            " --classifier-backend jev` on the pinned Jev version, the default"
                            " schema and this version of the set, covering this run's cases, and"
                            " there is none: run it first")  # fmt: skip


SOURCE_MIN = 8  # characters of a `--source-run` ID prefix, as `ecf eval status` shows them


def _claude_run(conn: sqlite3.Connection, opts: Options, version: str, schema: CompiledSchema,
                ids: set[str]
                ) -> tuple[str, dict[str, dict[str, Any]], dict[str, Any]]:  # fmt: skip
    """A frozen-classification C run's source (step 6, P2; checks as `_jev_run`): the
    `/ecf-eval` run `opts.source_run` names, preset C on any classifier model (operator decision
    2026-10-15: a candidate classifier's run can serve), complete, not an experiment and not
    itself on frozen classifications, on this version of the set, `schema`
    and this run's sensitivity, its result file present with every case of this run (`ids`). Its
    full run ID, each case's classification (a failed one empty, so it is scored as a failure)
    and its inputs (C_SOURCE_KEYS). Refused, naming the first check it fails."""
    ref = opts.source_run or ""
    if len(ref) < SOURCE_MIN:
        raise InvalidInputError(f"--source-run: give the run ID, or at least its first"
                                f" {SOURCE_MIN} characters")  # fmt: skip
    rows = conn.execute("SELECT run_id, pair, set_version, metrics, path FROM eval_runs WHERE"
                        " substr(run_id, 1, ?) = ?", (len(ref), ref)).fetchall()  # fmt: skip
    if len(rows) != 1:
        why = "no such eval run" if not rows else "more than one run starts so; give more of it"
        raise InvalidInputError(f"--source-run {ref}: {why}")
    row = rows[0]
    m: dict[str, Any] = json.loads(row["metrics"])
    claude: dict[str, Any] = m.get("claude") or {}
    o: dict[str, Any] = m.get("options") or {}
    checks = (
        (row["pair"] == claude_pins.PAIR["C"] and claude.get("preset") == "C",
         "isn't a preset C `/ecf-eval` run"),
        ("source_run" not in claude, "took its classifications from another run"),
        (m.get("complete") is True and o.get("classifier") is not False
         and o.get("for_gate") is not False, "isn't complete, or is an experiment"),
        (row["set_version"] == version, "is on another version of the set"),
        (m.get("schema_digest") == schema.digest, "asked another schema"),
        (claude.get("sensitivity") == opts.sensitivity,
         f"didn't run at {opts.sensitivity} sensitivity"),
        (bool(row["path"]) and Path(row["path"]).is_file(),
         "has no result file on this computer (an imported run)"),
    )  # fmt: skip
    for ok, why in checks:
        if not ok:
            raise InvalidInputError(f"--source-run {ref}: that run {why}; a frozen-classification"
                                    " run takes a complete preset C run on this set,"
                                    " schema and sensitivity")  # fmt: skip
    result = load_result(Path(row["path"]))
    if not ids <= {c.id for c in result.cases}:
        raise InvalidInputError(f"--source-run {ref}: that run lacks some of this run's cases (a"
                                " `--fraud-only` run can't serve a full one)")  # fmt: skip
    got = {c.id: {k: v for k, v in c.got.items() if k != "rule"} for c in result.cases}
    return str(row["run_id"]), got, {k: m[k] for k in C_SOURCE_KEYS if k in m}


def start(connect: Callable[[], sqlite3.Connection],  # noqa: PLR0912, PLR0915 - the refusals
          clock: Clock, data_dir: Path, opts: Options, *,
          spawn: Callable[[Callable[[], None]], None] | None = None,
          ) -> dict[str, Any]:  # fmt: skip
    if opts.preset not in USED_ROLES:
        raise InvalidInputError("a Claude eval runs preset C or D (preset B was removed in"
                                " v3.0.0, ADR 0031)")  # fmt: skip
    if opts.preset == "D" and opts.classifier_model is not None:
        raise InvalidInputError("preset D classifies with Jev: no classifier model")
    if opts.source_run is not None and opts.preset != "C":
        raise InvalidInputError("--source-run is for preset C (preset D takes Jev's"
                                " classifications)")  # fmt: skip
    if opts.source_run is not None and opts.classifier_model is not None:
        raise InvalidInputError("a run on a source run's classifications asks no classifier:"
                                " no --classifier-model")  # fmt: skip
    if opts.sensitivity not in ("standard", "high"):
        raise InvalidInputError("sensitivity must be standard or high")
    if not 1 <= opts.batch <= BATCH_MAX:
        raise InvalidInputError(f"batch must be between 1 and {BATCH_MAX}")
    if not WINDOW_MIN <= opts.classifier_window <= WINDOW_MAX:
        raise InvalidInputError(f"the classifier window must be between {WINDOW_MIN} and"
                                f" {WINDOW_MAX} characters")  # fmt: skip
    window = opts.classifier_window != CLASSIFIER_CHARS
    if window and (opts.preset != "C" or opts.source_run is not None):
        raise InvalidInputError("--classifier-window is for preset C asking its own classifier"
                                " (preset D's classifications are Jev's, and a source run's"
                                " are already made)")  # fmt: skip
    # a generated set, another window or a few cards is never the gate's evidence (eval plan
    # P1; window tuning: a for-gate run counts whatever its size)
    if genset.is_generated(opts.root) or window or opts.cases:
        opts = replace(opts, for_gate=False)
    cases, version = evalrun.load(opts.root, fraud_only=opts.fraud_only)
    if opts.cases:  # the set's version stays the whole set's
        if unmatched := [g for g in opts.cases
                         if not any(fnmatch.fnmatchcase(c.id, g) for c in cases)]:  # fmt: skip
            among = " among the fraud cards (--fraud-only)" if opts.fraud_only else ""
            raise InvalidInputError(f"no case matches --cases {', '.join(unmatched)}{among}")
        cases = [c for c in cases if any(fnmatch.fnmatchcase(c.id, g) for g in opts.cases)]
    if not cases:
        raise InvalidInputError("no cases to run (build the set with `ecf eval build`)")
    conn = connect()
    source_run: str | None = None
    got: dict[str, dict[str, Any]] = {}
    source: dict[str, Any] = {}
    derived: dict[str, jev_mapping.Derived] = {}
    try:
        if opts.for_gate:
            _check_root(conn, opts.root)
        schema = config.current_schema(conn)
        if opts.preset == "D":
            source_run, got, source, derived = _jev_run(conn, version, schema,
                                                        {c.id for c in cases})  # fmt: skip
        elif opts.source_run is not None:
            source_run, got, source = _claude_run(conn, opts, version, schema,
                                                  {c.id for c in cases})  # fmt: skip
        source_key = _source_key(conn, source_run)
        models, pinned, key = models_for(conn, opts, source_key)
    finally:
        conn.close()
    starter = rules.load_starter_rules(schema)
    works = [Work(c, secrets.token_hex(16)) for c in cases]
    with _SLOT.lock:
        old = _SLOT.run
        if old is not None and old.open and not _expired(old, clock):
            raise ConflictError(f"Claude eval {old.run_id[:8]} is already registered; see"
                                " `ecf eval status`, or `ecf eval stop`")  # fmt: skip
        run = Run(new_random_id(), opts, models, pinned, key, version, clock.now(), works,
                  starter, policy.labels(schema, starter), data_dir, schema, source_run,
                  source, source_key)  # fmt: skip
        _SLOT.run = run
    if opts.for_gate:  # only here (P16); never an experiment's or a generated set's (eval P1)
        evalrun.remember_root(connect, clock, opts.root)
    log.info("claude_eval.registered", run_id=run.run_id[:8], cases=len(works),
             preset=opts.preset, pinned=pinned)  # fmt: skip

    def work() -> None:
        try:
            _prepare(connect, clock, run, got, derived)
        except Exception as exc:  # reported in status; never raised into the thread
            log.error("claude_eval.prepare_failed", error_type=type(exc).__name__)
            with run.lock:
                run.state = "failed"
                run.detail = f"preparing the cases failed ({type(exc).__name__})"

    (spawn or _thread)(work)
    return snapshot(run)


def _thread(work: Callable[[], None]) -> None:
    threading.Thread(target=work, name="ecf-claude-eval", daemon=True).start()


def _check_root(conn: sqlite3.Connection, root: Path) -> None:
    """A for-gate run re-points the go-live gate at its set (`remember_root`), whatever the
    set's size: refused on another folder while the gate's set is still there (window tuning
    L1), so a pilot or partial set can't become the gate's evidence by a missed flag."""
    stored = slack_admin.setting(conn, evalrun.EVAL_ROOT)
    if stored and Path(stored) != root.resolve() and (Path(stored) / "labels.jsonl").is_file():
        raise InvalidInputError(f"the go-live gate's set is at {stored}: a run for the gate on"
                                f" {root} would point the gate there. Pass --not-for-gate for an"
                                " experiment; if the set has moved, remove the old folder's"
                                " labels.jsonl first")  # fmt: skip


def _source_key(conn: sqlite3.Connection, source_run: str | None) -> str | None:
    """A source run's `eval_runs.digest`: a D run's Jev gate key, a frozen C run's pin key."""
    if source_run is None:
        return None
    row = conn.execute("SELECT digest FROM eval_runs WHERE run_id = ?", (source_run,)).fetchone()
    return str(row["digest"]) if row else None


def _prepare(connect: Callable[[], sqlite3.Connection], clock: Clock, run: Run,
             got: dict[str, dict[str, Any]] | None = None,
             derived: dict[str, jev_mapping.Derived] | None = None) -> None:  # fmt: skip
    """ecf's analysis and the excerpts for each case, in order; each is claimable once ready. A
    case of a run with a source (D's Jev run, or a frozen C run's) is planned at once on its
    source classification (`got`), so only its actor is asked."""
    scratch = ruletest.Scratch(clock)
    try:
        for w in run.works:
            if run.stop.is_set():
                return
            raw = w.case.path.read_bytes()
            # as `ecf eval run`: the card's expected facts applied (Q1), so runs score alike
            facts, (cls_text, act_text), measured = evalrun.case_inputs_full(
                w.case, scratch, triggers.redact_injection, limit=run.opts.classifier_window
            )
            msg = ruletest.read_case(raw).parsed
            email = {
                "from": f"{msg.from_name} <{msg.from_addr}>" if msg.from_name and msg.from_addr
                else (msg.from_addr or msg.from_name or ""),
                "subject": msg.subject,
                "date": (msg.headers.get("date") or ("",))[0],
                "attachments_meta": [{"name": a.name, "type": a.content_type, "size": a.size}
                                     for a in msg.attachments if not a.inline or a.name],
            }  # fmt: skip
            with run.lock:
                if not run.open:
                    return
                w.facts, w.email, w.cls_text, w.act_text = facts, email, cls_text, act_text
                w.measured = measured
                if run.source_run is not None:  # an empty answer (a faulted case) fails (P1)
                    w.classification = (got or {}).get(w.case.id) or None
                    w.derived = (derived or {}).get(w.case.id, jev_mapping.NO_BAND)
                    _classified(run, w)
                else:
                    w.stage = "classify"
        with run.lock:
            if run.state == "preparing":
                run.state = "ready"
            _maybe_finish(connect, clock, run)
    finally:
        scratch.close()


# Scoring: each case planned through policy once it has a classification (and scored after the
# actor's proposal where one is asked), then the run's finish: repeat classifications, claim
# records and the run's Claude figures.


def _context(run: Run, w: Work, *, risk: bool = False) -> policy.Context:
    """`risk`: with the case's unsure fields in `facts.jev`, as the service's actor stage sees
    them (OD-511). The rule plan is made without, and `evalrun.held` adds the caution after, as in
    step 3's runs, so its figures (hides the hold dropped) stay comparable; the actions agree."""
    facts = w.facts
    if risk and w.derived:
        facts = facts | {jev_facts.KEY: jev_facts.record(w.derived, None, None)}
    return policy.Context(_cls(w), facts, run.opts.sensitivity, run.rules, {},
                          frozenset())  # fmt: skip


def _cls(w: Work) -> dict[str, Any]:
    """The case's classification; only an `act` stage asks, and it always has one."""
    if w.classification is None:
        raise ConflictError("that case has no classification")
    return w.classification


def _classified(run: Run, w: Work) -> None:
    """After a classification (or none): plan it, then to the actor or scored."""
    if w.classification is None:
        _score(run, w)
        return
    w.plan = policy.plan(_context(run, w), run.known)
    if w.plan.to_actor:
        w.before_actor = frozenset(a.name for a in w.plan.actions)
        w.stage = "act"
    else:
        _score(run, w)


def _proposed(run: Run, w: Work, proposal: dict[str, Any] | None) -> None:
    if proposal is not None and w.plan is not None:
        w.proposal = {"action": str(proposal["action"]), "target": str(proposal["target"] or "")}
        if proposal["action"] != "needs_clarification":
            one = policy.proposal(_context(run, w, risk=True), w.plan, w.proposal["action"],
                                  w.proposal["target"] or None, run.known)  # fmt: skip
            if isinstance(one, policy.Planned):
                w.plan.actions.append(one)
    _score(run, w)


def _score(run: Run, w: Work) -> None:
    band: dict[str, Any] = {}
    if w.plan is not None and w.derived:  # preset D: the caution Jev's answer asks for (step 3)
        w.plan, band = evalrun.held(_context(run, w), w.plan, w.derived, run.known, run.rules)
    w.result = evalrun.score(w.case, w.classification, w.plan, w.proposal, w.derived,
                             before_actor=w.before_actor, labels=run.known)  # fmt: skip
    # the rules-alone route, as `ecf eval run` records it, so the window figures can count the
    # fraud cases only the model could route (OD-555, fix review E1)
    alone = evalrun.rules_alone(w.case, w.facts, run.rules, run.known, run.schema,
                                run.opts.sensitivity)  # fmt: skip
    w.result = w.result.model_copy(update=band | w.measured | {
        "rule_alone": alone,
        "model_only_fraud": w.case.expected.get("rule") == "fraud_guard" and alone != "fraud_guard",
    })  # fmt: skip
    w.stage = "done"


def _maybe_finish(connect: Callable[[], sqlite3.Connection], clock: Clock, run: Run) -> None:
    """Every case done: a run that made its own classifications classifies its first cases
    again (§16.5); one with a source run (D, or C on frozen classifications) asked no
    classifier, so it has nothing to repeat (step 6, P3). Then the result."""
    if run.state != "ready" or any(w.stage != "done" for w in run.works):
        return
    if run.source_run is None and not run.repeats_started:
        run.repeats_started = True
        for w in run.works[: evalrun.DETERMINISM_CASES]:
            w.stage = "repeat"
        return
    _finish(connect, clock, run, "done")


def _finish(connect: Callable[[], sqlite3.Connection], clock: Clock, run: Run,
            state: str, detail: str = "") -> None:  # fmt: skip
    """Save what was scored (a stopped or expired run too, marked incomplete) and end the run."""
    complete = state == "done"
    run.state, run.detail = state, detail
    for w in run.works:
        if w.claim != "free":
            _ended(w, "run_ended")
        w.claim, w.session = "free", None
    results = [w.result.model_copy(update={"claims": _claim_records(run, w)})
               for w in run.works if w.result is not None]  # fmt: skip
    if not results:
        return
    # only a run on the pinned models can pass the gate: an unpinned run is comparison only (J8;
    # a D run also needs its source run on D's current key, P8); a C run on frozen
    # classifications asked no classifier and never can (step 6, P2)
    frozen = run.opts.preset == "C" and run.source_run is not None
    summary: dict[str, Any] = evalrun.summarize(results, run.diffs, complete=complete,
                                                classifier=not frozen,
                                                gate_eligible=run.pinned and not frozen,
                                                for_gate=run.opts.for_gate,
                                                schema=run.schema,
                                                fraud_only=run.opts.fraud_only)  # fmt: skip
    if frozen:  # what the classifier was asked, and with: the source run's
        summary.update(run.source)
        summary["classifier_source"] = {"run_id": run.source_run,
                                        "digest": run.source_key}  # fmt: skip
    elif run.opts.preset == "D":  # what Jev was asked, with what: the source run's (P13)
        summary.update(run.source)
        # the caution its source's answers asked for (step 3): the same mapping, by the source's
        # prompt digest
        summary["band"] = evalrun.band_figures(results, [w.case for w in run.works], run.rules)
        # the source run and the pin it matched (`_jev_run`), for the D Check (P8)
        summary["jev_source"] = {"run_id": run.source_run, "pin": jev_keys.pin(),
                                 **({"determinism": summary.pop("jev_determinism")}
                                    if "jev_determinism" in summary else {})}  # fmt: skip
    else:
        summary["schema_digest"] = run.schema.digest  # what the classifier was asked
        summary["prompt_digest"] = claude_review.prompt_digest(run.schema)  # and with (Q4)
        summary["read_bytes"] = read_bytes(run.schema)  # per classify read, user fields (R9)
        summary["input_format"] = CLAUDE_INPUT_FORMAT  # Jev vs C prints "different inputs"
        # how much of each email the models read: compare prints a difference (window tuning)
        summary["excerpt_cap"] = {"classifier_chars": run.opts.classifier_window,
                                  "actor_chars": ACTOR_CHARS}  # fmt: skip
    summary["options"] |= _window_options(run.opts)
    summary["rules_digest"] = rules.composed_digest(run.schema, None)  # as items (OD-507, P14)
    summary["excerpt_prep"] = excerpt.VERSION
    summary["facts_version"] = ruletest.FACTS_VERSION
    summary["plugin_digest"] = plugin_digest()  # the skills and agent prompts (v2.2.0)
    summary["actor_prompt_digest"] = actor_prompt_digest()  # the actor's instructions (OD-523)
    summary["keywords_digest"] = triggers.keywords_digest()
    summary["label_map_unlabelled"] = evalrun.map_effect([w.case for w in run.works])
    conn = connect()
    try:
        summary["claude"] = _figures(conn, run)
        if (spawns := spawn_figures(results)) is not None:
            summary["claude"]["spawns"] = spawns
        result = ResultFile(run_id=run.run_id, pair=claude_pins.PAIR.get(run.opts.preset, D_PAIR),
                            set_version=run.version, created_at=to_ts(clock.now()),
                            cases=results, digest=run.key, summary=summary)  # fmt: skip
        evalrun._save(conn, clock, run.data_dir, result,  # pyright: ignore[reportPrivateUsage]
                      copy_to=run.opts.copy_to)  # fmt: skip
    finally:
        conn.close()
    run.summary = summary
    log.info("claude_eval.saved", run_id=run.run_id[:8], state=state, cases=len(results))


def _window_options(opts: Options) -> dict[str, Any]:
    """A window-tuning run's options, written only when used (step 2)."""
    return ({} if opts.classifier_window == CLASSIFIER_CHARS
            else {"classifier_window": opts.classifier_window}) | (
        {"cases": list(opts.cases)} if opts.cases else {})  # fmt: skip


def _claim_records(run: Run, w: Work) -> list[ClaimRecord]:
    """A case's claims for its result: times in seconds from the run's registration (item 3)."""

    def s(t: datetime | None) -> float | None:
        return None if t is None else round((t - run.created).total_seconds(), 3)

    return [ClaimRecord(stage=c.stage, spawn=c.spawn, round=c.round, session=c.session,
                        preparing=c.preparing, claimed_s=s(c.claimed) or 0.0, read_s=s(c.read),
                        answered_s=s(c.answered), settled_s=s(c.settled), outcome=c.outcome,
                        high_risk=list(c.high_risk), invalid=c.invalid)
            for c in w.claims]  # fmt: skip


def _figures(conn: sqlite3.Connection, run: Run) -> dict[str, Any]:
    """The run's Claude figures (OD-290): its sessions' API requests since it was registered,
    by model and source; plan usage at the first and last reading of those sessions. Approximate
    when `/ecf-review` ran in the same session."""
    used = used_roles(run.opts)
    return {
        "preset": run.opts.preset,
        **({"source_run": run.source_run} if run.source_run is not None else {}),
        "sensitivity": run.opts.sensitivity,
        "models": {r: run.models[r] for r in used},
        "pinned": run.pinned,
        "batch": run.opts.batch,
        "sessions": len(run.sessions),
        "registered_at": to_ts(run.created),  # claims' times count from here (item 3)
        # round -> its session's next `eval_next`, in seconds as the claims' (review R1): read
        # minus next call per spawn is the margin under `UNREAD_MIN`
        "next_call_s": {
            str(r): round((t - run.created).total_seconds(), 3)
            for r, t in sorted(run.next_call.items())
        },
    } | claude_usage.run_report(conn, run.sessions, run.created)


# The session's side: what the `/ecf-eval` session calls through MCP. Expiry, handing out claims
# in rounds and spawns with their timing limits, checking each submission against its claim,
# and settling what was submitted.


def _expired(run: Run, clock: Clock) -> bool:
    return clock.now() - run.created >= EXPIRY


def _check_expiry(connect: Callable[[], sqlite3.Connection], clock: Clock, run: Run) -> None:
    if run.open and _expired(run, clock):
        run.stop.set()
        _finish(connect, clock, run, "expired", "expired 24 hours after it was registered")


def session_info(connect: Callable[[], sqlite3.Connection], clock: Clock) -> dict[str, Any] | None:
    """What `ecf claude` needs at start: the waiting run and any `ecf-eval-*` agents to render."""
    run = current()
    if run is None:
        return None
    with run.lock:
        _check_expiry(connect, clock, run)
        if not run.open:
            return None
        return {"run_id": run.run_id, "total": len(run.works),
                "left": sum(w.stage != "done" for w in run.works),
                "agents": agents_for(run)}  # fmt: skip


def eval_next(connect: Callable[[], sqlite3.Connection], clock: Clock, session_id: str, *,
              limit: int = claude_review.LIMIT_DEFAULT,
              stopped: str | None = None) -> dict[str, Any]:  # fmt: skip
    """Claim the next cases for this session: `{id, need, agent, spawn}` each, and `spawns`, one
    `{agent, prompt}` per Agent spawn, the prompt written by the service: the tokens are only there
    (OD-499). `results` reports this session's earlier claims."""
    if not 1 <= limit <= claude_review.LIMIT_MAX:
        raise InvalidInputError(f"limit must be between 1 and {claude_review.LIMIT_MAX}")
    run = current()
    if run is None:
        raise NotFoundError(NO_RUN)
    with run.lock:
        _check_expiry(connect, clock, run)
        now = clock.now()
        for r in run.awaiting.pop(session_id, []):  # this call follows the session's rounds
            run.next_call.setdefault(r, now)
        for w in run.works:  # expired claims go back; their agent may still be running
            if w.claim == "claimed" and w.expires is not None and w.expires <= now:
                _outcome(run, w, "claim_expired")
                if w.session is not None:
                    run.grace.setdefault(w.session, {})[w.agent] = now + GRACE
                w.claim, w.session = "free", None
        _free_unread(run, now)
        results = run.outcomes.pop(session_id, [])
        base: dict[str, Any] = {"run_id": run.run_id[:8], "results": results,
                                "left": sum(w.stage != "done" for w in run.works)}  # fmt: skip
        if not run.open:
            return base | {"items": [], "spawns": [], "more": False, "done": True,
                           "state": run.state}  # fmt: skip
        if stopped is not None:
            return base | {"items": [], "spawns": [], "more": False, "done": False,
                           "stopped": stopped}  # fmt: skip
        if session_id not in run.sessions:
            run.sessions.append(session_id)
        free = [w for w in run.works if w.stage in ("classify", "act", "repeat")
                and w.claim == "free"]  # fmt: skip
        picked, more = _round(run, session_id, free, limit, now)
        tokens = [_claim(run, w, session_id, now) for w in picked]
        out = [{"id": w.ref, "need": w.need, "agent": w.agent} for w in picked]
        _spawns(run, out)
        if out:
            _record_claims(run, picked, out, session_id, limit, now)
            run.awaiting.setdefault(session_id, []).append(run.rounds)
        busy = sum(w.claim != "free" for w in run.works)
        reply = base | {"items": out, "spawns": claude_review.spawn_prompts(out, tokens),
                        "more": more, "done": False,
                        "preparing": run.state == "preparing", "in_progress": busy,
                        "progress": progress_line(_progress(run))}  # fmt: skip
        return reply if out else reply | _next(run, session_id, now)


def _round(run: Run, session_id: str, free: list[Work], limit: int,
           now: datetime) -> tuple[list[Work], bool]:  # fmt: skip
    """The cases this round hands out: one agent type at a time per session. Two agent types at
    once would defeat the model check, which can't tell their work apart (telemetry.py; V1.0.0
    run 6cea77b1). An expired claim's agent may still be running for `GRACE`: until then no other
    agent type starts, and that agent is preferred. Parallel spawns of one type stay: Claude Code
    restarts their shared `ecf-mcp` server when one ends, and a call cut that way is retried and
    answered by `_again` (H1 `ee1915bc`, SPEC §21.2)."""
    grace = {a: t for a, t in run.grace.get(session_id, {}).items() if t > now}
    run.grace[session_id] = grace
    claimed = [w.agent for w in run.works if w.session == session_id and w.claim == "claimed"]
    busy = set(claimed)
    if not free:
        return [], False
    preferred = [w for w in free if _agent_of(run, w) in busy | set(grace)]
    agent = _agent_of(run, (preferred or free)[0])
    if (busy | set(grace)) - {agent}:
        return [], True
    # The session goes straight back for more, so the service paces it: Claude Code runs a
    # bounded number of subagents at once, and a claim it can't spawn only expires.
    room = max(0, min(limit, OUT_LIMITS * limit - claimed.count(agent)))
    same = [w for w in free if _agent_of(run, w) == agent]
    return same[:room], len(same) > room


def _next(run: Run, session_id: str, now: datetime) -> dict[str, Any]:
    """What the session does after a reply with no items (the skills obey it, so the session never
    judges): call again while work may still free up, with the seconds until the soonest change;
    otherwise end. A held submission counts for `HOLD_WAIT` after its answer; after that it is
    judged at session end, as before."""
    ends = [w.expires for w in run.works if w.claim == "claimed" and w.expires is not None]
    ends += [t for t in run.grace.get(session_id, {}).values() if t > now]
    ends += [w.claims[-1].answered + HOLD_WAIT for w in run.works
             if w.session == session_id and w.claim == "held" and w.claims
             and w.claims[-1].answered is not None
             and w.claims[-1].answered + HOLD_WAIT > now]  # fmt: skip
    ends += [at for at, _ws in _unread_spawns(run).values()]  # a dropped spawn's free (P7)
    if run.state == "preparing" or ends:
        soonest = min(ends, default=now)
        return {"next": "call again", "frees_in_s": max(0, round((soonest - now).total_seconds()))}
    return {"next": "end"}


def _unread_spawns(run: Run) -> dict[tuple[int, str], tuple[datetime, list[Work]]]:
    """Each spawn out with none of its claims called under (P3: every claim of its round and
    label, a re-claimed sibling's too) and a later `eval_next` from its session: when it counts as
    dropped (`UNREAD_MIN` after that call) and its live claims. The session issues every Agent
    call of a round before its next call, so a spawn no agent has read by then was never made."""
    out: dict[tuple[int, str], tuple[datetime, list[Work]]] = {}
    for w in run.works:
        if w.claim != "claimed" or not w.claims:
            continue
        c = w.claims[-1]
        if (t := run.next_call.get(c.round)) is not None:
            out.setdefault((c.round, c.spawn), (t + claude_review.UNREAD_MIN, []))[1].append(w)
    if out:
        called = {(c.round, c.spawn) for w in run.works for c in w.claims if c.called}
        out = {k: v for k, v in out.items() if k not in called}
    return out


def _free_unread(run: Run, now: datetime) -> None:
    """A dropped spawn's claims go back at once (`claim_unread`, OD-501), not after `CLAIM_TTL`
    and then `GRACE`: nothing ran. Its agent type counts as out for `TAIL`, as `_mismatch`, and
    its tokens are kept, so a late agent's refused request extends that (`_late`, P2)."""
    for (rnd, _spawn), (at, ws) in _unread_spawns(run).items():
        if at > now:
            continue
        for w in ws:
            session = w.session or ""
            if w.expires is not None:
                freed = claude_review.Unread(w.expires, str(rnd), now)
                run.unread.setdefault(session, {})[w.token_hash] = freed
            _outcome(run, w, "claim_unread")
            mine = run.grace.setdefault(session, {})
            mine[w.agent] = max(mine.get(w.agent, now), now + TAIL)
            w.claim, w.session = "free", None
        after = (now - ws[0].claims[-1].claimed).total_seconds()
        log.warning("claude_eval.claim_unread", run_id=run.run_id[:8], round=rnd, items=len(ws),
                    after_s=round(after))  # fmt: skip


def _agent_of(run: Run, w: Work) -> str:
    """The agent a case's next step needs: by sensitivity to classify, by risk to act."""
    if w.stage != "act":
        role = "classifier_high" if run.opts.sensitivity == "high" else "classifier"
    else:
        role = "actor_high" if w.plan is not None and w.plan.high_risk else "actor"
    return agent_name(role, run.pinned)


WAIT_S = 6.0  # under ecf-mcp's 10 s request limit (§15.4)
POLL_S = 2.0
GRACE = timedelta(seconds=300)  # an expired claim's agent may still be running (plan S3)
HOLD_WAIT = timedelta(seconds=120)  # a held submission keeps the session polling this long
# After its last submission an agent still writes its report (H2 `fc997ef9`: at most 1.2 s), and
# the session no longer waits for its spawns: its agent counts as out this long (review R1).
TAIL = timedelta(seconds=15)
OUT_LIMITS = 2  # a session's claims out per agent type: at most this many `limit`s (review R2)


def eval_next_waiting(connect: Callable[[], sqlite3.Connection], clock: Clock, session_id: str,
                      *, limit: int = claude_review.LIMIT_DEFAULT, stopped: str | None = None,
                      sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:  # fmt: skip
    """`eval_next`, but a reply that would be empty only because work is still out or being
    prepared waits up to `WAIT_S` for some: the session can't pause itself (`ecf claude` denies
    every tool that could), so each call it makes waits a little before `next` sends it back."""
    waited, results = 0.0, list[dict[str, Any]]()
    while True:
        got = eval_next(connect, clock, session_id, limit=limit, stopped=stopped)
        results += got["results"]  # each call hands over the outcomes since the last: keep them
        got["results"] = results
        busy = got.get("in_progress", 0) or got.get("preparing") or got.get("next") == "call again"
        if got.get("items") or got.get("done") or got.get("stopped") or not busy:
            return got
        if waited >= WAIT_S:
            return got
        sleep(POLL_S)
        waited += POLL_S


def _claim(run: Run, w: Work, session_id: str, now: datetime) -> str:
    """Claim the case; its token, for the spawn's prompt only."""
    need = "act" if w.stage == "act" else "classify"
    if w.token_hash:
        w.hashes.add(w.token_hash)
    w.fence += 1
    token = f"{w.fence}.{secrets.token_urlsafe(24)}"
    w.session, w.token_hash, w.expires = session_id, _hash(token), now + claude_review.CLAIM_TTL
    w.claim, w.need, w.agent, w.invalid = "claimed", need, _agent_of(run, w), 0
    return token


def _spawns(run: Run, items: list[dict[str, Any]]) -> None:
    """Group the round's items into spawns as `/ecf-review` hands them out: one per batched agent;
    the classifier-high agent `batch` each (one by default), the actor-high agent one each."""
    n: dict[str, int] = {}
    for it in items:
        agent = str(it["agent"])
        if agent.endswith("-high"):
            k = n.get(agent, 0)
            n[agent] = k + 1
            size = run.opts.batch if role_of(agent) == "classifier_high" else 1
            it["spawn"] = f"{agent}-{k // size + 1}"
        else:
            it["spawn"] = agent


def _record_claims(run: Run, picked: list[Work], out: list[dict[str, Any]], session_id: str,
                   limit: int, now: datetime) -> None:  # fmt: skip
    """One `Claim` per item of a round that handed out items."""
    run.rounds += 1
    ordinal = run.sessions.index(session_id) + 1
    preparing = run.state == "preparing"
    for w, it in zip(picked, out, strict=True):
        reasons = policy.high_risk_reasons(_context(run, w)) if w.stage == "act" else ()
        stage = cast(ClaimStage, w.stage)  # a claimable stage
        w.claims.append(Claim(stage, str(it["spawn"]), run.rounds, ordinal, limit, len(out),
                              preparing, now, high_risk=reasons))  # fmt: skip


def _ended(w: Work, outcome: Outcome) -> None:
    """The current claim's outcome, set once."""
    if w.claims and w.claims[-1].outcome is None:
        w.claims[-1].outcome = outcome


def _outcome(run: Run, w: Work, outcome: Outcome) -> None:
    _ended(w, outcome)
    if w.session is not None:
        run.outcomes.setdefault(w.session, []).append({"id": w.ref, "outcome": outcome})


def owns(ref: str) -> bool:
    run = current()
    return run is not None and any(w.ref == ref for w in run.works)


def _claimed(run: Run, clock: Clock, session_id: str, ref: str, token: str, agent: str,
             need: str | None = None) -> Work:  # fmt: skip
    """The claim the token names, checked; the caller holds `run.lock`."""
    w = run.by_ref.get(ref)
    if w is None:
        raise NotFoundError("no claim on that item")
    fence = token.partition(".")[0]
    own = (w.session == session_id and fence == str(w.fence)
           and hmac.compare_digest(_hash(token), w.token_hash))  # fmt: skip
    if not own:
        _late(run, clock, session_id, token, agent)
    if w.agent != agent:
        if own:  # this case's own token from an agent of the wrong type (P6)
            _mismatch(run, clock, w, session_id, agent)
        raise ForbiddenProfileError(f"that claim is for {w.agent}, not {agent}")
    if not own:
        if _anothers(run, clock, w, session_id, token):
            _mismatch(run, clock, w, session_id, agent)
        raise ConflictError("that claim isn't current: the item was claimed again or by another"
                            " session")  # fmt: skip
    if w.claim != "claimed" or w.expires is None or w.expires <= clock.now():
        raise ConflictError("that claim has ended; run eval_next again")
    if w.claims:  # before the need check: a wrong-need call is a running agent too (review R2)
        w.claims[-1].called = True
    if need is not None and w.need != need:
        raise ConflictError(f"that claim is to {w.need}, not to {need}")
    return w


def _late(run: Run, clock: Clock, session_id: str, token: str, agent: str) -> None:
    """A token an unread free ended: that spawn started after all (P2). The refusal stands, and
    its agent's final request is still to come: its type counts as out for `TAIL` from now."""
    now, h = clock.now(), _hash(token)
    if session_id not in run.unread:
        return
    mine = {t: u for t, u in run.unread[session_id].items() if u.until > now}
    run.unread[session_id] = mine
    hit = [u for t, u in mine.items() if hmac.compare_digest(h, t)]
    if not hit:
        return
    grace = run.grace.setdefault(session_id, {})
    grace[agent] = max(grace.get(agent, now), now + TAIL)
    if not hit[0].late:  # counted and logged once per token (review R1)
        hit[0].late = True
        run.late += 1
        log.warning("claude_eval.claim_unread_late", run_id=run.run_id[:8], round=hit[0].round,
                    agent=agent, after_s=round((now - hit[0].at).total_seconds()))  # fmt: skip


def _anothers(run: Run, clock: Clock, w: Work, session_id: str, token: str) -> bool:
    """The token is the live one of another case this session claimed, or one a mix-up freed
    before its claim would have expired (a two-way swap, review R1): the session put the wrong
    token in this case's spawn prompt (d157a948; OD-499)."""
    now, h = clock.now(), _hash(token)
    live = any(y is not w and y.session == session_id
               and (y.claim == "held" or (y.claim == "claimed" and y.expires is not None
                                          and y.expires > now))
               and hmac.compare_digest(h, y.token_hash) for y in run.works)  # fmt: skip
    # never this case's own tokens: its late agent's, after a re-claim, isn't a mix-up (P4)
    freed = [hmac.compare_digest(h, t) for t, until in run.freed.get(session_id, {}).items()
             if until > now and t != w.token_hash and t not in w.hashes]  # fmt: skip
    return live or any(freed)


def _mismatch(run: Run, clock: Clock, w: Work, session_id: str, agent: str) -> None:
    """A mixed-up token: this case's claim, still out to this session, ends now and the case goes
    back (`claim_mismatch`), instead of waiting `CLAIM_TTL` and then `GRACE` (OD-499). The
    refused agent still makes its final request: its type counts as out for `TAIL`, as `_hold`."""
    now = clock.now()
    if not (w.claim == "claimed" and w.session == session_id and w.expires is not None
            and w.expires > now):  # fmt: skip
        return
    if w.claims:  # an agent of its spawn called (OD-501)
        w.claims[-1].called = True
    if sum(c.outcome == "claim_mismatch" for c in w.claims) >= claude_review.MISMATCH_MAX:
        return  # a session that keeps mangling it: today's expiry (review R2)
    mine_freed = run.freed.setdefault(session_id, {})
    mine_freed[w.token_hash] = w.expires
    _outcome(run, w, "claim_mismatch")
    w.claim, w.session = "free", None
    mine = run.grace.setdefault(session_id, {})
    mine[agent] = max(mine.get(agent, now), now + TAIL)
    log.warning("claude_eval.claim_mismatch", run_id=run.run_id[:8])


def get_message(clock: Clock, session_id: str, ref: str, token: str,
                agent: str) -> dict[str, Any]:  # fmt: skip
    """As `/ecf-review`'s: only the named agent's server gets the text (OD-307); no facts but the
    derived signals (OD-484), no labels."""
    run = _run_or_missing()
    with run.lock:
        w = _claimed(run, clock, session_id, ref, token, agent)
        if w.claims and w.claims[-1].read is None:
            w.claims[-1].read = clock.now()
        text = w.cls_text if w.need == "classify" else w.act_text
        out: dict[str, Any] = {"id": ref, "need": w.need,
                               "untrusted_email": w.email | {"text": text},
                               "notice": claude_review.NOTICE}  # fmt: skip
        if w.need == "classify":
            sent = signals.as_object(signals.sent(w.facts))
            out |= claude_review.schema_fields(run.schema) | {"signals": sent}
        else:
            out |= {"classification": _cls(w),
                    "actions": list(actor.offered(_context(run, w, risk=True))),
                    "labels": sorted(run.known), "move_folders": [],
                    "earlier_answers": []}  # fmt: skip
    return out


def record_classification(clock: Clock, session_id: str, ref: str, token: str,
                          classification: Any, agent: str) -> dict[str, Any] | Hold:  # fmt: skip
    run = _run_or_missing()
    with run.lock:  # the claim is checked and held in one step (plan S4)
        digest = _digest({"classification": classification})
        if (again := _again(run, session_id, ref, token, agent, digest)) is not None:
            return again
        w = _claimed(run, clock, session_id, ref, token, agent, "classify")
        result, errors = claude_review.check_classification(classification, run.schema)
        if result is None:
            return _invalid(run, w, errors, digest)
        return _hold(clock, run, w, session_id, {"classification": result}, digest)


def propose_action(clock: Clock, session_id: str, ref: str, token: str, body: dict[str, Any],
                   agent: str) -> dict[str, Any] | Hold:  # fmt: skip
    run = _run_or_missing()
    proposal = {k: body.get(k) for k in ("action", "target", "reason", "question", "text")}
    with run.lock:
        digest = _digest({"proposal": proposal})
        if (again := _again(run, session_id, ref, token, agent, digest)) is not None:
            return again
        w = _claimed(run, clock, session_id, ref, token, agent, "act")
        why = claude_review.check_proposal(proposal, run.known, frozenset(),
                                           actor.offered(_context(run, w, risk=True)))  # fmt: skip
        if why is not None:
            return _invalid(run, w, [why], digest)
        return _hold(clock, run, w, session_id, {"proposal": proposal}, digest)


def _run_or_missing() -> Run:
    run = current()
    if run is None:
        raise NotFoundError("no claim on that item")
    return run


def _digest(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _again(run: Run, session_id: str, ref: str, token: str, agent: str,
           digest: str) -> dict[str, Any] | None:  # fmt: skip
    """A resubmission of a submission already taken (its reply was lost when Claude Code
    restarted the agent's server; plan S2): the same submission gets the same reply again, never
    a second record; a different one under a claim that already has its answer is refused."""
    w = run.by_ref.get(ref)
    if w is None:
        return None
    fence, h = token.partition(".")[0], _hash(token)
    if (w.claim == "held" and w.session == session_id and fence == str(w.fence)
            and w.agent == agent and hmac.compare_digest(h, w.token_hash)):  # fmt: skip
        if digest != w.held_digest:
            raise ConflictError("that claim already has an answer")
        return {"accepted": True, "pending": True, "errors": []}
    if w.last is not None:
        s_id, f, th, a, d, ended, reply = w.last
        if s_id == session_id and fence == str(f) and a == agent and hmac.compare_digest(h, th):
            if d == digest:
                return dict(reply)
            if ended:
                raise ConflictError("that claim already has an answer")
    return None


def _keep(w: Work, digest: str, reply: dict[str, Any], *, ended: bool) -> None:
    """Keep the reply to the current claim's last submission, for `_again`."""
    if w.session is not None:
        w.last = (w.session, w.fence, w.token_hash, w.agent, digest, ended, dict(reply))


def _invalid(run: Run, w: Work, errors: list[str], digest: str) -> dict[str, Any]:
    """As `/ecf-review`: 3 tries per claim; after the last, the case is scored as the local
    model's failures are (no classification, or no proposal)."""
    w.invalid += 1
    if w.claims:  # counted per claim for the result (P19)
        w.claims[-1].invalid += 1
    ended = w.invalid >= claude_review.INVALID_MAX
    reply = {"accepted": False, "errors": errors,
             "tries_left": 0 if ended else claude_review.INVALID_MAX - w.invalid}  # fmt: skip
    if ended:  # a lost non-final reply costs a try: replaying it could loop forever
        _keep(w, digest, reply, ended=True)
        _outcome(run, w, "invalid")
        w.claim, w.session = "free", None
        if w.stage == "classify":
            _classified(run, w)
        elif w.stage == "act":
            _proposed(run, w, None)
        else:  # repeat: no second classification differs from the first
            run.diffs += w.classification is not None
            w.stage = "done"
    return reply


def _hold(clock: Clock, run: Run, w: Work, session_id: str, payload: dict[str, Any],
          digest: str) -> Hold:  # fmt: skip
    w.claim, w.held_digest = "held", digest
    now = clock.now()
    if w.claims:
        w.claims[-1].answered = now
    mine = run.grace.setdefault(session_id, {})
    mine[w.agent] = max(mine.get(w.agent, now), now + TAIL)
    return Hold(session_id, w.ref, w.fence, w.need, w.agent, payload, kind="eval")


def settle(connect: Callable[[], sqlite3.Connection], clock: Clock, notifier: Notifier,
           tel: Telemetry, hold: Hold, seen: Seen | None) -> dict[str, Any]:  # fmt: skip
    """Apply a held submission when the subagent work in its window was all on the run's model
    for its role; refuse it otherwise (as `/ecf-review`, OD-307)."""
    run = current()
    ended = {"accepted": False, "errors": ["that claim has ended"], "tries_left": 0}
    if run is None:
        return ended
    with run.lock:
        w = run.by_ref.get(hold.stable_id)
        if w is None or w.claim != "held" or w.fence != hold.fence or not run.open:
            return ended
        expected = run.models[role_of(hold.agent)]
        if w.claims:
            w.claims[-1].settled = clock.now()
        if seen is None or seen.model != expected:
            return _refuse(connect, clock, notifier, tel, run, w, seen, expected)
        _outcome(run, w, "recorded")
        _keep(w, w.held_digest, {"accepted": True, "errors": []}, ended=True)
        w.claim, w.session = "free", None
        if hold.need == "classify":
            cls: dict[str, Any] = hold.payload["classification"]
            if w.stage == "repeat":
                run.diffs += cls != w.classification
                w.stage = "done"
            else:
                w.classification = cls
                _classified(run, w)
        else:
            _proposed(run, w, hold.payload["proposal"])
        _maybe_finish(connect, clock, run)
    return {"accepted": True, "errors": []}


def _refuse(connect: Callable[[], sqlite3.Connection], clock: Clock, notifier: Notifier,
            tel: Telemetry, run: Run, w: Work, seen: Seen | None,
            expected: str) -> dict[str, Any]:  # fmt: skip
    session_id = w.session or ""
    if seen is None:
        model, why = "none (no subagent telemetry for the submission)", "unbound"
    else:
        model, why = seen.model, "model"
    _outcome(run, w, "model_refused")
    why_text = f"refused: the call came from {model}; this agent's model is {expected}"
    _keep(w, w.held_digest, {"accepted": False, "tries_left": 0, "errors": [why_text]}, ended=True)
    w.claim, w.session = "free", None  # the case waits for another round
    log.warning("claude_eval.model_refused", run_id=run.run_id[:8], check=why)
    if tel.refused(session_id, model, expected):
        conn = connect()
        try:
            alerts.event(conn, clock, notifier, "system_error",
                         "Claude eval work refused by the model check: "
                         f"{telemetry.refusal_line(1, model, expected)}. /ecf-eval stopped for"
                         " this session; see ecf doctor.")  # fmt: skip
        finally:
            conn.close()
    return {"accepted": False, "tries_left": 0, "errors": [why_text]}


def release_session(session_id: str) -> None:
    """The session ended: its claims end with it (the run carries on in the next session)."""
    run = current()
    if run is None:
        return
    with run.lock:
        for w in run.works:
            if w.session == session_id and w.claim != "free":
                _ended(w, "session_ended")
                w.claim, w.session = "free", None
        run.outcomes.pop(session_id, None)
        run.freed.pop(session_id, None)
        run.unread.pop(session_id, None)
        run.awaiting.pop(session_id, None)


# Status: stopping a run, and the progress and results the status line and `eval_results`
# show (counts and rates only, no case IDs).


def stop(connect: Callable[[], sqlite3.Connection], clock: Clock) -> dict[str, Any] | None:
    """`ecf eval stop`: end the run now and save what was scored (OD-291)."""
    run = current()
    if run is None or not run.open:
        return None
    run.stop.set()
    with run.lock:
        if run.open:
            scored = sum(w.result is not None for w in run.works)
            _finish(connect, clock, run, "stopped",
                    f"stopped after {scored} of {len(run.works)} cases")  # fmt: skip
    return snapshot(run)


def snapshot(run: Run) -> dict[str, Any]:
    with run.lock:
        return {
            "run_id": run.run_id, "state": run.state, "detail": run.detail,
            "done": sum(w.result is not None for w in run.works), "total": len(run.works),
            "prepared": sum(w.stage != "preparing" for w in run.works),
            "preset": run.opts.preset, "sensitivity": run.opts.sensitivity,
            "pinned": run.pinned, "batch": run.opts.batch,
            "models": {r: run.models[r] for r in used_roles(run.opts)},
            **({"source_run": run.source_run} if run.source_run is not None else {}),
            **({} if run.opts.for_gate else {"for_gate": False}),
            # echoed so the CLI can tell a service that ignored them (window tuning step 2)
            "classifier_window": run.opts.classifier_window, "cases": list(run.opts.cases),
            "created_at": to_ts(run.created), "result": run.summary,
        }  # fmt: skip


def status(connect: Callable[[], sqlite3.Connection], clock: Clock) -> dict[str, Any] | None:
    run = current()
    if run is None:
        return None
    with run.lock:
        _check_expiry(connect, clock, run)
    return snapshot(run)


def progress() -> dict[str, Any] | None:
    """The open run's progress, for the status line (SPEC §13.4): counts only, no case IDs."""
    run = current()
    if run is None or not run.open:
        return None
    with run.lock:
        return _progress(run)


def _progress(run: Run) -> dict[str, Any]:
    return {"run_id": run.run_id[:8], "scored": sum(w.result is not None for w in run.works),
            "total": len(run.works), "in_progress": sum(w.claim != "free" for w in run.works),
            "repeats_left": sum(w.stage == "repeat" for w in run.works),
            "round": run.rounds,
            "mismatches": sum(c.outcome == "claim_mismatch" for w in run.works
                              for c in w.claims),
            "unread": sum(c.outcome == "claim_unread" for w in run.works
                          for c in w.claims),
            "late": run.late}  # fmt: skip


def progress_line(p: dict[str, Any]) -> str:
    """The line `/ecf-eval` quotes after each round, so the session never counts for itself."""
    line = f"{p['scored']}/{p['total']} scored, {p['in_progress']} in progress, round {p['round']}"
    line += f", {p['repeats_left']} repeats left" if p["repeats_left"] else ""
    line += f", {p['mismatches']} mixed-up claims freed" if p.get("mismatches") else ""
    line += f", {p['unread']} unread claims freed" if p.get("unread") else ""
    return line + (f", {p['late']} late after an unread free" if p.get("late") else "")


def results() -> dict[str, Any]:
    """`eval_results`: progress and, once saved, metrics only: counts and rates, no case IDs."""
    run = current()
    if run is None:
        raise NotFoundError(NO_RUN)
    s = snapshot(run)
    m: dict[str, Any] = s["result"] or {}
    out: dict[str, Any] = {k: s[k] for k in ("state", "done", "total")} | {"run_id": run.run_id[:8]}
    if m:
        out["metrics"] = {k: m.get(k) for k in ("confirmed", "scored", "rule_undecided",
                                                 "correct", "accuracy", "wilson95",
                                                 "per_field", "determinism_diffs", "complete",
                                                 "fraud_guard_cases", "fraud_guard_recall",
                                                 "gate_passed")} | {
            "unsafe": len(m.get("unsafe") or [])}  # fmt: skip
    return out
