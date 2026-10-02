"""`/ecf-eval`: the synthetic set through Claude (SPEC §10.4, §16.2-§16.3; V1.4 step 7; operator
decisions 2026-10-02, OD-287 to OD-292).

- **A run is registered from the command line** (`ecf eval run --claude`, OD-288), never through
  MCP: preset B or C, the sensitivity it runs as, `--fraud-only`, other models for the classifier
  or actor roles, and how many items a `-high` spawn takes (`--batch`). One run at a time. The
  cases are prepared in a service thread (ecf's own analysis and the excerpts, as `ecf eval run`
  does: about 40 s for the 158-case set); each becomes claimable as soon as it is ready.
- **The same agents and tools** (OD-287): `eval_next` claims cases like `review_queue` claims
  items, under a random reference (never the case ID, whose name gives the answer away), and the
  subagents read and submit through `get_message`, `record_classification` and `propose_action`,
  as in `/ecf-review`. Claims, fencing, the 3 invalid tries and the telemetry model check are the
  same; the main session never sees case text (OD-274), and no gold label leaves the service.
- **Models** (OD-288): a run on the pins in force uses the production agents (`ecf:classifier`,
  ...) and is keyed by the preset's pin key, so it counts for the go-live gate (§9.3). A run with
  other models gets the `ecf:eval-*` agents (the same prompts, rendered by `ecf claude` on the
  run's models), is keyed `eval-...`, and is for comparison only.
- **Preset B** (OD-289) takes Gemma's classification of each case from the latest complete
  `ecf eval run` on the pinned digest and the current set: Gemma isn't run again, and the actor
  comparison is exactly paired. Only the actor stage goes to Claude.
- **Scoring** is `ecf eval run`'s (`evalrun.score`, the starter rules, policy, the safety checks);
  the result file and `eval_runs` row have its format, plus the run's Claude figures. Preset C
  classifies its first 10 cases twice (determinism, §16.5).
- The run lives in service memory: a claim ends with its session and the run carries on in the
  next `ecf claude` session; a service restart loses it (no result saved). It expires 24 hours
  after it was registered; `ecf eval stop` ends it and saves what was scored (OD-291).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ecf.errors import (
    ConflictError,
    ForbiddenProfileError,
    InvalidInputError,
    NotFoundError,
)
from ecf.eval.results import CaseResult, ResultFile, load_result
from ecf.ids import new_random_id
from ecf.schema import load_schema_v1
from ecf_server import (
    actor,
    alerts,
    claude_pins,
    claude_review,
    claude_usage,
    evalrun,
    ollama,
    policy,
    rules,
    ruletest,
    telemetry,
    triggers,
)
from ecf_server.clock import Clock, to_ts
from ecf_server.log_bridge import log
from ecf_server.message import CLASSIFIER_CHARS, parse
from ecf_server.notify import Notifier
from ecf_server.telemetry import Hold, Seen, Telemetry

EXPIRY = timedelta(hours=24)
BATCH_MAX = 5
ACTOR_CHARS = 4000  # as `ecf eval run` and the stored actor excerpt
ROLES = ("classifier", "classifier_high", "actor", "actor_high")
NO_RUN = ("no Claude eval is waiting: register one with `ecf eval run --claude`, then open"
          " `ecf claude`")  # fmt: skip


@dataclass(frozen=True)
class Options:
    root: Path
    preset: str = "C"  # B | C
    sensitivity: str = "standard"  # the address sensitivity the cases run as
    fraud_only: bool = False
    classifier_model: str | None = None
    actor_model: str | None = None
    batch: int = 1  # items per `-high` spawn


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
    classification: dict[str, Any] | None = None
    plan: policy.Plan | None = None
    proposal: dict[str, str] | None = None
    result: CaseResult | None = None
    # the claim
    session: str | None = None
    token_hash: str = ""
    fence: int = 0
    expires: datetime | None = None
    claim: str = "free"  # free | claimed | held
    need: str = ""
    agent: str = ""
    invalid: int = 0


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
    source_run: str | None = None  # preset B: the `ecf eval run` whose classifications it uses
    state: str = "preparing"  # preparing | ready | done | stopped | expired | failed
    detail: str = ""
    sessions: list[str] = field(default_factory=list[str])
    # session -> its ended claims, each reported once by eval_next
    outcomes: dict[str, list[dict[str, str]]] = field(default_factory=dict)  # pyright: ignore[reportUnknownVariableType]
    repeats_started: bool = False
    diffs: int = 0
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


# ---------------------------------------------------------------------------- agents and models


def agent_name(role: str, pinned: bool) -> str:
    return f"ecf:{'' if pinned else 'eval-'}{role.replace('_', '-')}"


def role_of(agent: str) -> str:
    return agent.removeprefix("ecf:").removeprefix("eval-").replace("-", "_")


def models_for(conn: sqlite3.Connection, opts: Options) -> tuple[dict[str, str], bool, str]:
    """The run's model per role, whether they are the pins in force for the preset's roles, and
    the run's key (the gate's pin key, or `eval-...`)."""
    eff = claude_pins.effective(conn)
    models = {r: eff[r] for r in ROLES}
    for model, roles in ((opts.classifier_model, ("classifier", "classifier_high")),
                         (opts.actor_model, ("actor", "actor_high"))):  # fmt: skip
        if model is None:
            continue
        if claude_pins.family(model) is None:
            raise InvalidInputError(f"{model!r} isn't a Claude model ID (claude-haiku-*,"
                                    " claude-sonnet-*, claude-opus-*)")  # fmt: skip
        models |= dict.fromkeys(roles, model)
    used = claude_pins.GATE_ROLES[opts.preset]
    pinned = all(models[r] == eff[r] for r in used)
    if pinned:
        return models, True, claude_pins.key(claude_pins.pins(conn, opts.preset))
    keyed = {r: models[r] for r in used}
    if opts.preset in claude_pins.LOCAL_PRESETS:
        keyed["digest"] = ollama.load_pin().digest
    return models, False, "eval-" + claude_pins.key(keyed)


def agents_for(run: Run) -> dict[str, str]:
    """The `ecf:eval-*` agents `ecf claude` renders for a comparison run: name -> model."""
    if run.pinned:
        return {}
    return {agent_name(r, False).removeprefix("ecf:"): run.models[r] for r in ROLES}


# ---------------------------------------------------------------------------- registering


def _a_run(conn: sqlite3.Connection, version: str) -> tuple[str, dict[str, dict[str, Any]]]:
    """Preset B (OD-289): the latest complete `ecf eval run` with the classifier, on the pinned
    digest and this version of the set: its run ID and each case's classification."""
    digest = ollama.load_pin().digest
    rows = conn.execute("SELECT run_id, metrics, path FROM eval_runs WHERE digest = ? AND"
                        " set_version = ? ORDER BY created_at DESC",
                        (digest, version)).fetchall()  # fmt: skip
    for row in rows:
        metrics: dict[str, Any] = json.loads(row["metrics"])
        opts: dict[str, Any] = metrics.get("options") or {}
        if not (metrics.get("complete") and opts.get("classifier")):
            continue
        result = load_result(Path(row["path"]))
        if any(not c.got for c in result.cases):
            break  # a run from before OD-259 holds no classifications
        got = {c.id: {k: v for k, v in c.got.items() if k != "rule"} for c in result.cases}
        return str(row["run_id"]), got
    raise InvalidInputError("preset B uses Gemma's classifications from a complete `ecf eval run`"
                            " on the pinned model and this version of the set, and there is none:"
                            " run `ecf eval run` first")  # fmt: skip


def start(connect: Callable[[], sqlite3.Connection], clock: Clock, data_dir: Path, opts: Options,
          *, spawn: Callable[[Callable[[], None]], None] | None = None,
          ) -> dict[str, Any]:  # fmt: skip
    if opts.preset not in ("B", "C"):
        raise InvalidInputError("a Claude eval runs preset B or C")
    if opts.sensitivity not in ("standard", "high"):
        raise InvalidInputError("sensitivity must be standard or high")
    if not 1 <= opts.batch <= BATCH_MAX:
        raise InvalidInputError(f"batch must be between 1 and {BATCH_MAX}")
    if opts.preset == "B" and opts.classifier_model is not None:
        raise InvalidInputError("preset B classifies with the local model: no classifier model")
    cases, version = evalrun.load(opts.root, fraud_only=opts.fraud_only)
    if not cases:
        raise InvalidInputError("no cases to run (build the set with `ecf eval build`)")
    conn = connect()
    try:
        models, pinned, key = models_for(conn, opts)
        source, got = _a_run(conn, version) if opts.preset == "B" else (None, {})
    finally:
        conn.close()
    schema = load_schema_v1()
    starter = rules.load_starter_rules(schema)
    works = [Work(c, secrets.token_hex(16)) for c in cases]
    with _SLOT.lock:
        old = _SLOT.run
        if old is not None and old.open and not _expired(old, clock):
            raise ConflictError(f"Claude eval {old.run_id[:8]} is already registered; see"
                                " `ecf eval status`, or `ecf eval stop`")  # fmt: skip
        run = Run(new_random_id(), opts, models, pinned, key, version, clock.now(), works,
                  starter, policy.labels(schema, starter), data_dir, source)  # fmt: skip
        _SLOT.run = run
    evalrun._remember_root(connect, clock, opts.root)  # pyright: ignore[reportPrivateUsage]
    log.info("claude_eval.registered", run_id=run.run_id[:8], cases=len(works),
             preset=opts.preset, pinned=pinned)  # fmt: skip

    def work() -> None:
        try:
            _prepare(connect, clock, run, got)
        except Exception as exc:  # reported in status; never raised into the thread
            log.error("claude_eval.prepare_failed", error_type=type(exc).__name__)
            with run.lock:
                run.state = "failed"
                run.detail = f"preparing the cases failed ({type(exc).__name__})"

    (spawn or _thread)(work)
    return snapshot(run)


def _thread(work: Callable[[], None]) -> None:
    threading.Thread(target=work, name="ecf-claude-eval", daemon=True).start()


def _prepare(connect: Callable[[], sqlite3.Connection], clock: Clock, run: Run,
             got: dict[str, dict[str, Any]]) -> None:  # fmt: skip
    """ecf's analysis and the excerpts for each case, in order; each is claimable once ready."""
    scratch = ruletest._Scratch(clock)  # pyright: ignore[reportPrivateUsage]
    try:
        for w in run.works:
            if run.stop.is_set():
                return
            raw = w.case.path.read_bytes()
            facts = scratch.facts(raw)
            msg = parse(raw)
            email = {
                "from": f"{msg.from_name} <{msg.from_addr}>" if msg.from_name and msg.from_addr
                else (msg.from_addr or msg.from_name or ""),
                "subject": msg.subject,
                "date": (msg.headers.get("date") or ("",))[0],
                "attachments_meta": [{"name": a.name, "type": a.content_type, "size": a.size}
                                     for a in msg.attachments if not a.inline or a.name],
            }  # fmt: skip
            cls_text = msg.excerpt(CLASSIFIER_CHARS, triggers.redact_injection)
            act_text = msg.excerpt(ACTOR_CHARS, triggers.redact_injection)
            with run.lock:
                if not run.open:
                    return
                w.facts, w.email, w.cls_text, w.act_text = facts, email, cls_text, act_text
                if run.opts.preset == "C":
                    w.stage = "classify"
                else:
                    w.classification = got.get(w.case.id) or None
                    _classified(run, w)
        with run.lock:
            if run.state == "preparing":
                run.state = "ready"
            _maybe_finish(connect, clock, run)
    finally:
        scratch.close()


# ---------------------------------------------------------------------------- scoring


def _context(run: Run, w: Work) -> policy.Context:
    return policy.Context(_cls(w), w.facts, run.opts.sensitivity, run.rules, {},
                          frozenset())  # fmt: skip


def _cls(w: Work) -> dict[str, Any]:
    """The case's classification; only an `act` stage asks, and it always has one."""
    if w.classification is None:
        raise ConflictError("that case has no classification")
    return w.classification


def _classified(run: Run, w: Work) -> None:
    """After a classification (or none): plan it, then to the actor or scored."""
    if w.classification is None:
        _score(w)
        return
    w.plan = policy.plan(_context(run, w), run.known)
    if w.plan.to_actor:
        w.stage = "act"
    else:
        _score(w)


def _proposed(run: Run, w: Work, proposal: dict[str, Any] | None) -> None:
    if proposal is not None and w.plan is not None:
        w.proposal = {"action": str(proposal["action"]), "target": str(proposal["target"] or "")}
        if proposal["action"] != "needs_clarification":
            one = policy.proposal(_context(run, w), w.plan, w.proposal["action"],
                                  w.proposal["target"] or None, run.known)  # fmt: skip
            if isinstance(one, policy.Planned):
                w.plan.actions.append(one)
    _score(w)


def _score(w: Work) -> None:
    w.result = evalrun.score(w.case, w.classification, w.plan, w.proposal)
    w.stage = "done"


def _maybe_finish(connect: Callable[[], sqlite3.Connection], clock: Clock, run: Run) -> None:
    """Every case done: preset C classifies its first cases again (§16.5), then the result."""
    if run.state != "ready" or any(w.stage != "done" for w in run.works):
        return
    if run.opts.preset == "C" and not run.repeats_started:
        run.repeats_started = True
        for w in run.works[: evalrun.DETERMINISM_CASES]:
            w.stage = "repeat"
        return
    _finish(connect, clock, run, "done")


def _finish(connect: Callable[[], sqlite3.Connection], clock: Clock, run: Run,
            state: str, detail: str = "") -> None:  # fmt: skip
    """Save what was scored (a stopped or expired run too, marked incomplete) and end the run."""
    results = [w.result for w in run.works if w.result is not None]
    complete = state == "done"
    run.state, run.detail = state, detail
    for w in run.works:
        w.claim, w.session = "free", None
    if not results:
        return
    summary: dict[str, Any] = evalrun.summarize(results, run.diffs, complete=complete)
    conn = connect()
    try:
        summary["claude"] = _figures(conn, run)
        result = ResultFile(run_id=run.run_id, pair=claude_pins.PAIR[run.opts.preset],
                            set_version=run.version, created_at=to_ts(clock.now()),
                            cases=results, digest=run.key, summary=summary)  # fmt: skip
        evalrun._save(conn, clock, run.data_dir, result)  # pyright: ignore[reportPrivateUsage]
    finally:
        conn.close()
    run.summary = summary
    log.info("claude_eval.saved", run_id=run.run_id[:8], state=state, cases=len(results))


def _figures(conn: sqlite3.Connection, run: Run) -> dict[str, Any]:
    """The run's Claude figures (OD-290): its sessions' API requests since it was registered,
    by model and source; plan usage at the first and last reading of those sessions. Approximate
    when `/ecf-review` ran in the same session."""
    used = claude_pins.GATE_ROLES[run.opts.preset]
    return {
        "preset": run.opts.preset,
        "sensitivity": run.opts.sensitivity,
        "models": {r: run.models[r] for r in used},
        "pinned": run.pinned,
        "batch": run.opts.batch,
        "source_run": run.source_run,
        "sessions": len(run.sessions),
    } | claude_usage.run_report(conn, run.sessions, run.created)


# ---------------------------------------------------------------------------- the session's side


def _expired(run: Run, clock: Clock) -> bool:
    return clock.now() - run.created >= EXPIRY


def _check_expiry(connect: Callable[[], sqlite3.Connection], clock: Clock, run: Run) -> None:
    if run.open and _expired(run, clock):
        run.stop.set()
        _finish(connect, clock, run, "expired", "expired 24 hours after it was registered")


def session_info(connect: Callable[[], sqlite3.Connection], clock: Clock) -> dict[str, Any] | None:
    """What `ecf claude` needs at start: the waiting run and any `ecf:eval-*` agents to render."""
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
    """Claim the next cases for this session: `{id, need, agent, claim_token, spawn}` each; the
    items of one `spawn` go to one Agent spawn. `results` reports this session's earlier claims."""
    if not 1 <= limit <= claude_review.LIMIT_MAX:
        raise InvalidInputError(f"limit must be between 1 and {claude_review.LIMIT_MAX}")
    run = current()
    if run is None:
        raise NotFoundError(NO_RUN)
    with run.lock:
        _check_expiry(connect, clock, run)
        now = clock.now()
        for w in run.works:  # expired claims go back
            if w.claim == "claimed" and w.expires is not None and w.expires <= now:
                _outcome(run, w, "claim_expired")
                w.claim, w.session = "free", None
        results = run.outcomes.pop(session_id, [])
        base: dict[str, Any] = {"run_id": run.run_id[:8], "results": results,
                                "left": sum(w.stage != "done" for w in run.works)}  # fmt: skip
        if not run.open:
            return base | {"items": [], "more": False, "done": True, "state": run.state}
        if stopped is not None:
            return base | {"items": [], "more": False, "done": False, "stopped": stopped}
        if session_id not in run.sessions:
            run.sessions.append(session_id)
        free = [w for w in run.works if w.stage in ("classify", "act", "repeat")
                and w.claim == "free"]  # fmt: skip
        out = [_claim(run, w, session_id, now) for w in free[:limit]]
        _spawns(run, out)
        busy = sum(w.claim != "free" for w in run.works)
        return base | {"items": out, "more": len(free) > limit, "done": False,
                       "preparing": run.state == "preparing", "in_progress": busy}  # fmt: skip


def _claim(run: Run, w: Work, session_id: str, now: datetime) -> dict[str, Any]:
    need = "act" if w.stage == "act" else "classify"
    if need == "classify":
        role = "classifier_high" if run.opts.sensitivity == "high" else "classifier"
    else:
        role = "actor_high" if w.plan is not None and w.plan.high_risk else "actor"
    w.fence += 1
    token = f"{w.fence}.{secrets.token_urlsafe(24)}"
    w.session, w.token_hash, w.expires = session_id, _hash(token), now + claude_review.CLAIM_TTL
    w.claim, w.need, w.agent, w.invalid = "claimed", need, agent_name(role, run.pinned), 0
    return {"id": w.ref, "need": need, "agent": w.agent, "claim_token": token}


def _spawns(run: Run, items: list[dict[str, Any]]) -> None:
    """Group the round's items into spawns: one per batched agent; `-high` agents `batch` each
    (one by default, as `/ecf-review` hands them out)."""
    n: dict[str, int] = {}
    for it in items:
        agent = str(it["agent"])
        if agent.endswith("-high"):
            k = n.get(agent, 0)
            n[agent] = k + 1
            it["spawn"] = f"{agent.removeprefix('ecf:')}-{k // run.opts.batch + 1}"
        else:
            it["spawn"] = agent.removeprefix("ecf:")


def _outcome(run: Run, w: Work, outcome: str) -> None:
    if w.session is not None:
        run.outcomes.setdefault(w.session, []).append({"id": w.ref, "outcome": outcome})


def owns(ref: str) -> bool:
    run = current()
    return run is not None and any(w.ref == ref for w in run.works)


def _claimed(clock: Clock, session_id: str, ref: str, token: str,
             need: str | None = None) -> tuple[Run, Work]:  # fmt: skip
    run = current()
    w = run.by_ref.get(ref) if run is not None else None
    if run is None or w is None:
        raise NotFoundError("no claim on that item")
    fence = token.partition(".")[0]
    if not (w.session == session_id and fence == str(w.fence)
            and hmac.compare_digest(_hash(token), w.token_hash)):  # fmt: skip
        raise ConflictError("that claim isn't current: the item was claimed again or by another"
                            " session")  # fmt: skip
    if w.claim != "claimed" or w.expires is None or w.expires <= clock.now():
        raise ConflictError("that claim has ended; run eval_next again")
    if need is not None and w.need != need:
        raise ConflictError(f"that claim is to {w.need}, not to {need}")
    return run, w


def get_message(clock: Clock, session_id: str, ref: str, token: str,
                seen: Callable[[], Seen | None]) -> dict[str, Any]:  # fmt: skip
    """As `/ecf-review`'s: only a plugin agent gets the text (OD-274); no facts, no labels."""
    run, w = _claimed(clock, session_id, ref, token)
    s = seen()
    if s is None or s.source != telemetry.SUBAGENT:
        log.warning("claude_eval.read_refused", run_id=run.run_id[:8],
                    read="unbound" if s is None else "not_subagent")  # fmt: skip
        raise ForbiddenProfileError("get_message answers only an ecf agent, called from the"
                                    " agent the service named")  # fmt: skip
    with run.lock:
        text = w.cls_text if w.need == "classify" else w.act_text
        out: dict[str, Any] = {"id": ref, "need": w.need,
                               "untrusted_email": w.email | {"text": text},
                               "notice": claude_review.NOTICE}  # fmt: skip
        if w.need == "classify":
            out["schema"] = load_schema_v1().json_schema()
        else:
            out |= {"classification": _cls(w),
                    "actions": list(actor.allowed(_cls(w))),
                    "labels": sorted(run.known), "move_folders": [],
                    "earlier_answers": []}  # fmt: skip
    return out


def record_classification(clock: Clock, session_id: str, ref: str, token: str,
                          classification: Any,
                          tool_use_id: str | None) -> dict[str, Any] | Hold:  # fmt: skip
    run, w = _claimed(clock, session_id, ref, token, "classify")
    if tool_use_id is None:
        raise InvalidInputError(claude_review.NO_CALL_ID)
    result, errors = claude_review.check_classification(classification)
    with run.lock:
        if result is None:
            return _invalid(run, w, errors)
        return _hold(run, w, session_id, tool_use_id, {"classification": result})


def propose_action(clock: Clock, session_id: str, ref: str, token: str, body: dict[str, Any],
                   tool_use_id: str | None) -> dict[str, Any] | Hold:  # fmt: skip
    run, w = _claimed(clock, session_id, ref, token, "act")
    if tool_use_id is None:
        raise InvalidInputError(claude_review.NO_CALL_ID)
    proposal = {k: body.get(k) for k in ("action", "target", "reason", "question")}
    with run.lock:
        why = claude_review.check_proposal(proposal, run.known, frozenset(),
                                           actor.allowed(_cls(w)))  # fmt: skip
        if why is not None:
            return _invalid(run, w, [why])
        return _hold(run, w, session_id, tool_use_id, {"proposal": proposal})


def _invalid(run: Run, w: Work, errors: list[str]) -> dict[str, Any]:
    """As `/ecf-review`: 3 tries per claim; after the last, the case is scored as the local
    model's failures are (no classification, or no proposal)."""
    w.invalid += 1
    ended = w.invalid >= claude_review.INVALID_MAX
    if ended:
        _outcome(run, w, "invalid")
        w.claim, w.session = "free", None
        if w.stage == "classify":
            _classified(run, w)
        elif w.stage == "act":
            _proposed(run, w, None)
        else:  # repeat: no second classification differs from the first
            run.diffs += w.classification is not None
            w.stage = "done"
    return {"accepted": False, "errors": errors,
            "tries_left": 0 if ended else claude_review.INVALID_MAX - w.invalid}  # fmt: skip


def _hold(run: Run, w: Work, session_id: str, tool_use_id: str,
          payload: dict[str, Any]) -> Hold:  # fmt: skip
    w.claim = "held"
    return Hold(session_id, tool_use_id, w.ref, w.fence, w.need, w.agent, payload, kind="eval")


def settle(connect: Callable[[], sqlite3.Connection], clock: Clock, notifier: Notifier,
           tel: Telemetry, hold: Hold, seen: Seen | None) -> dict[str, Any]:  # fmt: skip
    """Apply a held submission when its call came from a plugin agent on the run's model for its
    role; refuse it otherwise (as `/ecf-review`, OD-268)."""
    run = current()
    ended = {"accepted": False, "errors": ["that claim has ended"], "tries_left": 0}
    if run is None:
        return ended
    with run.lock:
        w = run.by_ref.get(hold.stable_id)
        if w is None or w.claim != "held" or w.fence != hold.fence or not run.open:
            return ended
        expected = run.models[role_of(hold.agent)]
        if seen is None or seen.source != telemetry.SUBAGENT or seen.model != expected:
            return _refuse(connect, clock, notifier, tel, run, w, seen, expected)
        _outcome(run, w, "recorded")
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
        model, why = "none (no telemetry for the call)", "unbound"
    elif seen.source != telemetry.SUBAGENT:
        model, why = f"{seen.model} in the main session", "not_subagent"
    else:
        model, why = seen.model, "model"
    _outcome(run, w, "model_refused")
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
    why_text = f"refused: the call came from {model}; this agent's model is {expected}"
    return {"accepted": False, "tries_left": 0, "errors": [why_text]}


def release_session(session_id: str) -> None:
    """The session ended: its claims end with it (the run carries on in the next session)."""
    run = current()
    if run is None:
        return
    with run.lock:
        for w in run.works:
            if w.session == session_id and w.claim != "free":
                w.claim, w.session = "free", None
        run.outcomes.pop(session_id, None)


# ---------------------------------------------------------------------------- status


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
            "models": {r: run.models[r] for r in claude_pins.GATE_ROLES[run.opts.preset]},
            "created_at": to_ts(run.created), "result": run.summary,
        }  # fmt: skip


def status(connect: Callable[[], sqlite3.Connection], clock: Clock) -> dict[str, Any] | None:
    run = current()
    if run is None:
        return None
    with run.lock:
        _check_expiry(connect, clock, run)
    return snapshot(run)


def results() -> dict[str, Any]:
    """`eval_results`: progress and, once saved, metrics only: counts and rates, no case IDs."""
    run = current()
    if run is None:
        raise NotFoundError(NO_RUN)
    s = snapshot(run)
    m: dict[str, Any] = s["result"] or {}
    out: dict[str, Any] = {k: s[k] for k in ("state", "done", "total")} | {"run_id": run.run_id[:8]}
    if m:
        out["metrics"] = {k: m.get(k) for k in ("confirmed", "correct", "accuracy", "wilson95",
                                                 "per_field", "determinism_diffs", "complete",
                                                 "gate_passed")} | {
            "unsafe": len(m.get("unsafe") or [])}  # fmt: skip
    return out
