"""Rules and policy after classification (SPEC §8.2, §8.3, §8.6, §9.1; V1.3 step 4a).

`plan` is pure: it takes what the service knows about one classified item and returns what may be
done, and how. It never touches the mailbox, the database or a model. Its invariants are tested with
Hypothesis over the whole classification space (`tests/test_policy.py`).

- **I1, model output can raise risk, never lower it.** A hide action (mark_read, archive, move,
  junk) survives only when the rule allows hiding, the stage allows it (`live`; step 4b), the
  address isn't `high` (rules 6-8 become label + leave there, §8.3), none of the blockers is set
  (`content_unscanned`, quarantine, the regulator trigger, any fraud signal: a fraud or weak fraud
  trigger, a lookalike domain, or `fraud_risk` of low or more; a risky attachment, v2.1.0; from
  v3.0.0 S3, OD-500, the fixed items, each through `ecf.schema.fixed_item`: a bank detail change,
  a regulator claim, a claimed colleague from outside, and phishing, which blocks every hide but
  `junk`), and it is corroborated from facts alone: authenticated bulk mail from a sender seen
  before (`bulk_corroborates`), or a category a person confirmed for this sender that matches
  this classification, on a message whose `auth_result` is pass (OD-057). A hide that doesn't
  survive leaves the label and adds `leave`; the digest may offer "confirm this sender's
  category".
- **I2:** nothing here removes a label or flag; the pre-check's actions are never undone by a
  classification.
- **I3:** `high_risk` (§8.2) and `payment_or_fraud` (step-up, Approve-all exclusions) are the facts
  OR the classification, never AND.
- **I4:** targets are checked here, not left to the model's output grammar: a label must be a
  known label name (lowercase letters, digits and underscores, so it is a safe IMAP keyword) and a
  move target must be in `move_folders` (checked again at execution, step 5).
- **Modes:** each surviving action is `auto` or `approve`. On `standard` the action policy decides
  hide actions (default auto, §8.3); on `high` hide actions need approval, and so do hides decided
  in a Claude batch that held a risky or still unclassified item (§5.6, V1.4 step 3), and any hide
  of phishing (P6, OD-500: a compromised, confirmed sender's phishing is never auto-junked).
  High-risk items (§8.2): label, flag, escalate and leave are automatic; hide actions need
  approval; drafts and sends need approval. The local pair's stricter `local_high_risk` (sends
  rejected, OD-056) went with the local model in v3.0.0 (ADR 0031, P22).
- **Drafts and sends** (V1.5, OD-317): always need approval. A send's target must be an enabled
  template or a forward allow-list entry, the email must pass §8.4's guardrails
  (send_refusal), and outbound must be on for the address; while it's off the proposal is
  dropped as suppressed (kept on the item as `suppressed_action`) and the item is flagged (§8.4).
  The payload (recipient and text) is resolved by the caller, which has the database.
- **Jev's unsure fields** (preset D, v3.0.0 step 4, OD-511): the risk readers here (the fraud
  signal, the hide blockers, payment or fraud, the approval mode's phishing test, hence high risk
  and the alert echo) read `jev_facts.risk_view`: an unsure field as yes, `fraud_risk` as `low`.
  The rules read the stored classification (`rule_input`), so an unsure field fires no rule. The
  hold that drops every hide on such a case is `jev_mapping.hold`, applied after this plan.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from ecf.schema import CompiledSchema, fixed_item
from ecf_server import jev_facts, precheck
from ecf_server.rules import HIDE_ACTIONS, CompiledRules, Hide, LabelFrom, RuleInput

Mode = Literal["auto", "approve"]
SAFE = frozenset({"label", "flag", "escalate", "leave"})
SENDS = frozenset({"forward_internal", "reply_template"})
BUILTIN_LABELS = frozenset({"suspicious", "unverified_sender", "regulatory", "alert_echo"})
_LABEL = re.compile(r"^[a-z0-9_]{1,40}$")
FRAUD_RISKY = ("low", "medium", "high")  # fraud_risk levels that block hiding (I1)
NOT_STORED = "label not stored by this provider"


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
    templates: frozenset[str] = frozenset()  # enabled template ids
    forwards: frozenset[str] = frozenset()  # forward allow-list entry ids
    keywords_stored: bool = True  # the provider keeps custom keywords (probe; OD-439)
    historical: bool = False  # a scan's or reprocess's item: every hide needs approval (OD-507)


def labels(schema: CompiledSchema, rules: CompiledRules) -> frozenset[str]:
    """Every label name ecf may write: built-ins, category values (with any the install's
    extension adds), the rules' own names, and the values of extension enum fields a rule
    labels from (`{label: {field: x}}`; OD-478)."""
    category = schema.fields.get("category")  # a schema without category: no category labels
    names = set(BUILTIN_LABELS) | set(category.values if category is not None else ())
    ext = set(schema.extension_fields)
    for r in rules.rules:
        for a in r.then:
            if a.action != "label":
                continue
            if isinstance(a.target, str):
                names.add(a.target)
            elif isinstance(a.target, LabelFrom) and a.target.field in ext:
                names |= set(schema.fields[a.target.field].values)
    return frozenset(n for n in names if _LABEL.fullmatch(n))


def risk(ctx: Context) -> Mapping[str, Any]:
    """The classification the risk readers read: Jev's unsure fields as yes (OD-511)."""
    return jev_facts.risk_view(ctx.classification, ctx.facts)


def fraud_signal(ctx: Context) -> bool:
    t: dict[str, Any] = ctx.facts.get("triggers") or {}
    return bool(t.get("fraud") or t.get("fraud_weak") or t.get("lookalikes")
                or ctx.facts.get("quarantined")
                or risk(ctx).get("fraud_risk") in FRAUD_RISKY)  # fmt: skip


def hide_blockers(ctx: Context, action: str | None = None) -> list[str]:
    """What refuses a hide on this item (I1), by fixed name. `action`: the hide action asked
    about; phishing blocks every hide but `junk`, so the fixed rule can still junk it (OD-500).
    Without one, every blocker is listed."""
    t: dict[str, Any] = ctx.facts.get("triggers") or {}
    cls = risk(ctx)
    out: list[str] = []
    if ctx.facts.get("content_unscanned"):
        out.append("content not fully scanned")
    if t.get("regulator") or fixed_item(cls, "regulator_claim"):
        out.append("regulatory")  # the name _alert_echo reads
    if fraud_signal(ctx):
        out.append("fraud signal")
    if ctx.facts.get("risky_attachment"):
        out.append("risky attachment")
    if fixed_item(cls, "bank_detail_change"):
        out.append("bank detail change")
    if fixed_item(cls, "claims_internal") and ctx.facts.get("sender_origin") == "external":
        out.append("claims to be a colleague, from outside")
    if fixed_item(cls, "phishing") and action != "junk":
        out.append("phishing")
    return out


def draft_refusal(ctx: Context, *, check_language: bool = True) -> str | None:  # noqa: PLR0911
    """Why no draft is offered for this item, as the first failing clause's code, or None (P-a,
    P-b; M8 plan §2). It lists the hide blockers itself: `hide_blockers` counts `fraud_risk` low as
    a fraud signal, and a draft allows low. `check_language` False only at the approval of an item
    fetched before the `language` fact existed."""
    f = ctx.facts
    t: dict[str, Any] = f.get("triggers") or {}
    cls = risk(ctx)
    if f.get("auth_result") != "pass":
        return "not_authenticated"
    if f.get("quarantined"):
        return "quarantined"
    if f.get("content_unscanned"):
        return "unscanned"
    if f.get("bulk_signal"):
        return "bulk"
    if f.get("from_count") != 1 or f.get("from_ambiguous"):
        return "from_ambiguous"
    if t.get("fraud") or t.get("lookalikes") or t.get("unverified_payment"):
        return "fraud"
    if t.get("fraud_weak"):
        return "fraud_weak"
    if t.get("regulator") or fixed_item(cls, "regulator_claim"):
        return "regulator"
    if (
        cls.get("fraud_risk") in ("medium", "high")
        or fixed_item(cls, "phishing")
        or fixed_item(cls, "bank_detail_change")
        or (fixed_item(cls, "claims_internal") and f.get("sender_origin") == "external")
    ):
        return "risk"
    if f.get("risky_attachment"):
        return "risky_attachment"
    if check_language and f.get("language") != "en":
        return "language_other" if f.get("language") == "other" else "language_unknown"
    return None


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
    cls = risk(ctx)
    return (precheck.payment_or_fraud(ctx.facts)
            or cls.get("payment_related") is True
            or cls.get("fraud_risk") in ("medium", "high"))  # fmt: skip


HighRiskReason = Literal["high_sensitivity", "first_time_sender", "fraud_signal", "payment"]


def high_risk_reasons(ctx: Context) -> tuple[HighRiskReason, ...]:
    """Why an item is high risk (§8.2), in a fixed order; empty when it isn't (v2.2.0: the eval's
    actor-high routing figures)."""
    tests: tuple[tuple[HighRiskReason, bool], ...] = (
        ("high_sensitivity", ctx.sensitivity == "high"),
        ("first_time_sender", not ctx.facts.get("sender_seen_before")),
        ("fraud_signal", fraud_signal(ctx)),
        ("payment", payment_or_fraud(ctx)),
    )
    return tuple(name for name, hit in tests if hit)


def high_risk(ctx: Context) -> bool:
    """§8.2: a `high` address, a first-time sender, any fraud signal, or payment (I3: OR)."""
    return bool(high_risk_reasons(ctx))


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
    for a in decision.actions:
        why = _refuse(ctx, a.name, a.target, known_labels)
        if why is None and a.name in HIDE_ACTIONS:
            why = _hide_refusal(ctx, decision.hide, hide_blockers(ctx, a.name))
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
    if "alert_echo" in known_labels and ctx.keywords_stored:
        _add(echo, Planned("label", "alert_echo", _mode(ctx, "label", echo.high_risk)))
    _add(echo, Planned("leave", None, "auto"))
    return echo


def proposal(ctx: Context, p: Plan, name: str, target: str | None,
             known_labels: frozenset[str]) -> Planned | Dropped:  # fmt: skip
    """The same checks for one action the actor proposed (step 4c), plus drafts and sends (V1.5;
    see the module docstring)."""
    if name in SENDS or name == "draft_reply":
        why = _outbound_refusal(ctx, p, name, target)
        if why is not None:
            return Dropped(name, target, why)
        return Planned(name, target if name in SENDS else None, "approve")
    why = _refuse(ctx, name, target, known_labels)
    if why is None and name in HIDE_ACTIONS:
        why = _hide_refusal(ctx, Hide.CORROBORATED, hide_blockers(ctx, name))
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
    if name == "reply_template" and target not in ctx.templates:
        return f"{str(target)[:40]!r} isn't an enabled template"
    if name == "forward_internal" and target not in ctx.forwards:
        return f"{str(target)[:40]!r} isn't on the forward allow-list"
    why = draft_refusal(ctx) if name == "draft_reply" else None
    why = why or send_refusal(name, ctx.facts, fraud_signal=fraud_signal(ctx))
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
    if name == "label" and not ctx.keywords_stored:  # skipped, not a failure (OD-439)
        return NOT_STORED
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
        if high_risk or ctx.sensitivity == "high" or ctx.batch_risky or ctx.historical:
            return "approve"
        if fixed_item(risk(ctx), "phishing"):  # P6 (OD-500)
            return "approve"
        return "auto" if ctx.action_policy.get(name, "auto") == "auto" else "approve"
    return "approve"


def _add(p: Plan, a: Planned) -> None:
    if all((x.name, x.target) != (a.name, a.target) for x in p.actions):
        p.actions.append(a)
