"""Rules and policy after classification (SPEC §8.2, §8.3, §8.6, §9.1; V1.3 step 4a).

`plan` is pure: it takes what the service knows about one classified item and returns what may be
done, and how. It never touches the mailbox, the database or a model. Its invariants are tested with
Hypothesis over the whole classification space (`tests/test_policy.py`).

- **I1, model output can raise risk, never lower it.** A hide action (mark_read, archive, move,
  junk) survives only when the rule allows hiding, the stage allows it (`live`; step 4b), the
  address isn't `high` (rules 6-8 become label + leave there, §8.3), none of the blockers is set
  (`content_unscanned`, quarantine, the regulator trigger, any fraud signal: a fraud or weak fraud
  trigger, a lookalike domain, or `fraud_risk` of low or more), and it is corroborated from facts
  alone: authenticated bulk mail from a sender seen before (`bulk_corroborates`), or a category a
  person confirmed for this sender that matches this classification, on a message whose
  `auth_result` is pass (OD-057). A hide that doesn't survive leaves the label and adds `leave`;
  the digest may offer "confirm this sender's category".
- **I2:** nothing here removes a label or flag; the pre-check's actions are never undone by a
  classification.
- **I3:** `high_risk` (§8.2) and `payment_or_fraud` (step-up, Approve-all exclusions) are the facts
  OR the classification, never AND.
- **I4:** targets are checked here, not left to the model's output grammar: a label must be a
  known label name (lowercase letters, digits and underscores, so it is a safe IMAP keyword) and a
  move target must be in `move_folders` (checked again at execution, step 5).
- **Modes:** each surviving action is `auto` or `approve`. On `standard` the action policy decides
  hide actions (default auto, §8.3); on `high` hide actions need approval, and so do hides decided
  in a Claude batch that held a risky or still unclassified item (§5.6, V1.4 step 3). High-risk
  items on the local pair follow `local_high_risk` (§8.2, OD-056): label, flag, escalate and leave
  are automatic; hide actions need approval; sends are rejected; drafts need approval.
- **Drafts and sends** (V1.5, OD-317): always need approval. A send's target must be an enabled
  template or a forward allow-list entry, the email must pass §8.4's guardrails
  (send_refusal), and outbound must be on for the address; while it's off the proposal is
  dropped as suppressed (kept on the item as `suppressed_action`) and the item is flagged (§8.4).
  The payload (recipient and text) is resolved by the caller, which has the database.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from ecf.schema import CompiledSchema
from ecf_server import precheck
from ecf_server.rules import HIDE_ACTIONS, CompiledRules, Hide, RuleInput

Mode = Literal["auto", "approve"]
SAFE = frozenset({"label", "flag", "escalate", "leave"})
SENDS = frozenset({"forward_internal", "reply_template"})
BUILTIN_LABELS = frozenset({"suspicious", "unverified_sender", "regulatory", "alert_echo"})
_LABEL = re.compile(r"^[a-z0-9_]{1,40}$")
FRAUD_RISKY = ("low", "medium", "high")  # fraud_risk levels that block hiding (I1)


@dataclass(frozen=True)
class Planned:
    name: str
    target: str | None
    mode: Mode
    payload: dict[str, Any] | None = None  # a draft's or send's text and recipient (V1.5)


@dataclass(frozen=True)
class Dropped:
    name: str
    target: str | None
    why: str


@dataclass
class Plan:
    rule_id: str
    actions: list[Planned] = field(default_factory=list[Planned])
    dropped: list[Dropped] = field(default_factory=list[Dropped])
    to_actor: bool = False
    high_risk: bool = False
    payment_or_fraud: bool = False
    offer_confirm: bool = False  # the digest may offer "confirm this sender's category"
    actor: dict[str, Any] | None = None  # the local actor's proposal and its (cleaned) reason
    suppressed: str | None = None  # a send proposed while outbound is off (§8.4)

    @property
    def hides(self) -> list[Planned]:
        return [a for a in self.actions if a.name in HIDE_ACTIONS]


@dataclass(frozen=True)
class Context:
    classification: dict[str, Any]
    facts: dict[str, Any]
    sensitivity: str  # standard | high
    rules: CompiledRules
    action_policy: dict[str, str]  # standard column for hide actions: auto | approve
    move_folders: frozenset[str]
    confirmed_category: str | None = None  # a person's category for this sender
    batch_risky: bool = False  # a model batch with a risky or unclassified item (§5.6)
    outbound: bool = False  # the address's outbound switch (§8.4)
    local_pair: bool = True  # the local actor decides (preset A, or the local fallback; §8.2)
    templates: frozenset[str] = frozenset()  # enabled template ids
    forwards: frozenset[str] = frozenset()  # forward allow-list entry ids


def labels(schema: CompiledSchema, rules: CompiledRules) -> frozenset[str]:
    """Every label name ecf may write: built-ins, category values and the rules' own names."""
    names = set(BUILTIN_LABELS) | set(schema.fields["category"].values)
    for r in rules.rules:
        for a in r.then:
            if a.action == "label" and isinstance(a.target, str):
                names.add(a.target)
    return frozenset(n for n in names if _LABEL.fullmatch(n))


def fraud_signal(ctx: Context) -> bool:
    t: dict[str, Any] = ctx.facts.get("triggers") or {}
    return bool(t.get("fraud") or t.get("fraud_weak") or t.get("lookalikes")
                or ctx.facts.get("quarantined")
                or ctx.classification.get("fraud_risk") in FRAUD_RISKY)  # fmt: skip


def hide_blockers(ctx: Context) -> list[str]:
    t: dict[str, Any] = ctx.facts.get("triggers") or {}
    out: list[str] = []
    if ctx.facts.get("content_unscanned"):
        out.append("content not fully scanned")
    if t.get("regulator") or ctx.classification.get("category") == "regulatory":
        out.append("regulatory")
    if fraud_signal(ctx):
        out.append("fraud signal")
    return out


def corroborated(ctx: Context) -> bool:
    """From facts alone (I1): authenticated bulk mail from a sender seen before, or a category a
    person confirmed for this sender, matching this classification, on an authenticated message."""
    if ctx.facts.get("bulk_corroborates"):
        return True
    return (ctx.confirmed_category is not None
            and ctx.facts.get("auth_result") == "pass"
            and ctx.confirmed_category == ctx.classification.get("category"))  # fmt: skip


def payment_or_fraud(ctx: Context) -> bool:
    """I3: the pre-check's test OR the classifier's `payment_related` or fraud risk."""
    return (precheck.payment_or_fraud(ctx.facts)
            or ctx.classification.get("payment_related") is True
            or ctx.classification.get("fraud_risk") in ("medium", "high"))  # fmt: skip


def high_risk(ctx: Context) -> bool:
    """§8.2: a `high` address, a first-time sender, any fraud signal, or payment (I3: OR)."""
    return (ctx.sensitivity == "high"
            or not ctx.facts.get("sender_seen_before")
            or fraud_signal(ctx)
            or payment_or_fraud(ctx))  # fmt: skip


def rule_input(ctx: Context) -> RuleInput:
    triggers = precheck.fired(ctx.facts)
    if ctx.facts.get("quarantined"):
        triggers.add("fraud")
    return RuleInput(ctx.classification, ctx.facts, frozenset(triggers),
                     {"sensitivity": ctx.sensitivity})  # fmt: skip


def plan(ctx: Context, known_labels: frozenset[str]) -> Plan:
    decision = ctx.rules.evaluate(rule_input(ctx))
    p = Plan(decision.rule_id, to_actor=decision.to_actor, high_risk=high_risk(ctx),
             payment_or_fraud=payment_or_fraud(ctx))  # fmt: skip
    blockers = hide_blockers(ctx)
    for a in decision.actions:
        why = _refuse(ctx, a.name, a.target, known_labels)
        if why is None and a.name in HIDE_ACTIONS:
            why = _hide_refusal(ctx, decision.hide, blockers)
            if why == "not corroborated":
                p.offer_confirm = True
        if why is not None:
            p.dropped.append(Dropped(a.name, a.target, why))
            continue
        _add(p, Planned(a.name, a.target, _mode(ctx, a.name, p.high_risk)))
    if p.dropped and any(d.name in HIDE_ACTIONS for d in p.dropped):
        _add(p, Planned("leave", None, "auto"))
    return _alert_echo(ctx, p, known_labels) or p


def _alert_echo(ctx: Context, p: Plan, known_labels: frozenset[str]) -> Plan | None:
    """A bounce, auto-reply or copy of an alert email (own_mail.alert_echo; OD-330, OD-337):
    labelled `alert_echo` and left, no actor, unless the plan escalates or a fraud or regulatory
    signal holds, which keep their plan (only the alert email is suppressed for them)."""
    if not ctx.facts.get("alert_echo") or "regulatory" in hide_blockers(ctx):
        return None
    if fraud_signal(ctx) or any(a.name == "escalate" for a in p.actions):
        return None
    echo = Plan("alert_echo", to_actor=False, high_risk=p.high_risk,
                payment_or_fraud=p.payment_or_fraud)  # fmt: skip
    if "alert_echo" in known_labels:
        _add(echo, Planned("label", "alert_echo", _mode(ctx, "label", echo.high_risk)))
    _add(echo, Planned("leave", None, "auto"))
    return echo


def proposal(ctx: Context, p: Plan, name: str, target: str | None,
             known_labels: frozenset[str]) -> Planned | Dropped:  # fmt: skip
    """The same checks for one action the actor proposed (step 4c), plus drafts and sends (V1.5;
    see the module docstring). Sends on the local pair's high-risk items are rejected."""
    if name in SENDS or name == "draft_reply":
        why = _outbound_refusal(ctx, p, name, target)
        if why is not None:
            return Dropped(name, target, why)
        return Planned(name, target if name in SENDS else None, "approve")
    why = _refuse(ctx, name, target, known_labels)
    if why is None and name in HIDE_ACTIONS:
        why = _hide_refusal(ctx, Hide.CORROBORATED, hide_blockers(ctx))
    if why is not None:
        return Dropped(name, target, why)
    return Planned(name, target, _mode(ctx, name, p.high_risk))


def send_refusal(name: str, facts: dict[str, Any], *, fraud_signal: bool) -> str | None:
    """The §8.4 guardrails that depend on the email: replies (drafts too) need a single From
    mailbox, a template reply an authenticated sender; nothing is sent for bulk mail, mail not
    fully scanned, or any fraud signal. A draft is never sent, so only the From rule applies."""
    reply = name in ("draft_reply", "reply_template")
    send = name in SENDS
    checks = [
        (reply and facts.get("from_count") != 1, "not exactly one From address"),
        (name == "reply_template" and facts.get("auth_result") != "pass",
         "replies need an authenticated sender"),
        (send and bool(facts.get("bulk_signal")), "no sends for bulk mail"),
        (send and bool(facts.get("content_unscanned")), "no sends for mail not fully scanned"),
        (send and fraud_signal, "no sends for mail with a fraud signal"),
        ((reply or send) and bool(facts.get("ecf_keywords")),
         "ecf handled this email before (its labels are on it)"),  # keywords.py (V1.5)
    ]  # fmt: skip
    return next((why for applies, why in checks if applies), None)


def _outbound_refusal(ctx: Context, p: Plan, name: str, target: str | None) -> str | None:
    if name in SENDS and p.high_risk and ctx.local_pair:
        return "sends are rejected for high-risk items (local_high_risk)"
    if name == "reply_template" and target not in ctx.templates:
        return f"{str(target)[:40]!r} isn't an enabled template"
    if name == "forward_internal" and target not in ctx.forwards:
        return f"{str(target)[:40]!r} isn't on the forward allow-list"
    why = send_refusal(name, ctx.facts, fraud_signal=fraud_signal(ctx))
    if why is not None:
        return why
    if name in SENDS and not ctx.outbound:
        p.suppressed = name
        _add(p, Planned("flag", None, "auto"))
        return "outbound is off (suppressed)"
    return None


def _refuse(ctx: Context, name: str, target: str | None, known: frozenset[str]) -> str | None:
    if name == "label" and (target is None or target not in known or not _LABEL.fullmatch(target)):
        return f"unknown label {str(target)[:40]!r}"
    if name == "move" and target not in ctx.move_folders:
        return f"{str(target)[:60]!r} isn't in move_folders"
    return None


def _hide_refusal(ctx: Context, hide: Hide, blockers: list[str]) -> str | None:
    if hide is Hide.NEVER:
        return "this rule never hides"
    if ctx.sensitivity == "high":
        return "high address: label and leave (§8.3)"
    if blockers:
        return ", ".join(blockers)
    if not corroborated(ctx):
        return "not corroborated"
    return None


def _mode(ctx: Context, name: str, high_risk: bool) -> Mode:
    if name in SAFE:
        return "auto"
    if name in HIDE_ACTIONS:
        if high_risk or ctx.sensitivity == "high" or ctx.batch_risky:
            return "approve"
        return "auto" if ctx.action_policy.get(name, "auto") == "auto" else "approve"
    return "approve"


def _add(p: Plan, a: Planned) -> None:
    if all((x.name, x.target) != (a.name, a.target) for x in p.actions):
        p.actions.append(a)
