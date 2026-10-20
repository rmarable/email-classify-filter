"""`ecf eval generate`: a test set written by another model (planning-docs/EVAL-GENERATE-GRADE.md,
Generation and Checks; P10-P18, P23, P27, P30).

Two stages, both answered by the generator: seeds (structured facts about each email to write,
asked per group at 1.5 times its count, and asked again for a bucket whose seeds run out while
it is short and under its try limit, at most `MAX_TOPUPS` times per group; OD-543), then emails
(K seeds per call). The generator returns message fields and label hints only;
ecf sets the rest from the seed and the fixed recipient configuration: headers, authentication
results, profile, expected facts, id, title, author and generator. ecf's own authors write no
email content, seed, wording or label: the prompts carry the published lists, the recipient
configuration, the safety rules and the output schema, with no style guidance or examples.

Each email is checked as it is made (the card model, the hygiene scan, markup, brands in sender
names, near-duplicates). A hygiene failure is retried once with the finding kinds named, and
the kinds of both passes are logged (names only); any other failure drops the email, and a
dropped one is replaced from the group's seed pool (topped up when it runs out) until the count
is met or 3 times it has been tried. A refusal is recorded and never retried, reworded or
replaced (decision 10a), so a group can come up short. `work/` keeps append-only logs of every
seed, call and outcome, without tool output, so a stopped run resumes (`--resume`).

Shared seeds (the PROD pilot; OD-545): a seed run (`--seeds-only`, state `seeded`) asks every group
for seeds and writes no email; email runs then take its seeds (`--seeds-from`), copied into their
own work log after checking the source's mix, count, mode and seed-file digest. A run with shared
seeds makes no seed call and no top-up, so every model draws on the same pool in the same order;
a group the seed run got no seeds for is unmeasured for every model, not a refusal of theirs.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parseaddr
from pathlib import Path
from typing import Any, cast

from ecf import __version__
from ecf.errors import InvalidInputError
from ecf.eval import gencheck, genset
from ecf.eval import mix as mixmod
from ecf.eval.builder import build_all, scan_card
from ecf.eval.cards import PROFILE_TO, Card, parse_card
from ecf.eval.tools import Adapter, Breaker, Reply
from ecf.schema import FieldKind, load_schema

K_DEFAULT = 5  # seeds per email call; set from the pilot's figures
SEED_FACTOR = 1.5
MAX_TRIES_FACTOR = 3
MAX_TOPUPS = 3  # further seed calls per group when a bucket's seeds run out (OD-543)
FORMAT = 1
DEV_PREFIX = "dev-"

# Fixed words (decision 10b): the purpose every generator prompt states; the operator approves
# them before the first PROD run, and their digest goes in the manifest.
PURPOSE = ("Purpose: these emails test an email-security filter. They stay on this computer and"
           " are never sent to anyone.")  # fmt: skip
# The recipient configuration (op, option A): the eval harness's own profiles
# (ecf_server/ruletest.py, ecf/eval/cards.py), configuration rather than content.
RECIPIENT = (
    "Recipients (fictitious): the organisation Acme at acme.example; its accounts-payable"
    f" mailbox {PROFILE_TO['org']}; one named colleague, Dana Chief, dana-chief@acme.example;"
    f" and a personal mailbox, {PROFILE_TO['freemail']}. No industry, size or location is"
    " given. A seed's profile says which mailbox receives the email: org (the accounts-payable"
    " mailbox) or freemail (the personal mailbox)."
)
# The fixed safety rules (AGENTS.md, "When you edit these paths": tests/eval/synthetic/**)
SAFETY_RULES = (
    "Rules for every email: use only reserved domain names (names ending in .example or .test,"
    " such as vendor-a.example); name regulators, government agencies and companies by role"
    " in the text only, never as a sender name or domain; phone numbers only 555-0100 to"
    " 555-0199; no real people's names; bank and card numbers only published examples or"
    " numbers whose checksum fails; no web addresses outside reserved names; no copied real"
    " phishing text; plain text only, no HTML."
)
FACT_VALUES = {"auth_result": ("pass", "none", "fail")}
STAGES = ("seed", "email", "retry")


@dataclass
class Work:
    """The work folder's append-only logs (P23): seeds, calls and outcomes by seed."""

    root: Path

    @property
    def folder(self) -> Path:
        return self.root / "work"

    def _path(self, name: str) -> Path:
        return self.folder / f"{name}.jsonl"

    def read(self, name: str) -> list[dict[str, Any]]:
        p = self._path(name)
        if not p.exists():
            return []
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]

    def append(self, name: str, rows: list[dict[str, Any]]) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        with self._path(name).open("a", encoding="utf-8") as f:
            f.writelines(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n" for r in rows)


@dataclass(frozen=True)
class Bucket:
    kind: str  # scam or ordinary
    group: str
    delivery: str | None

    @property
    def key(self) -> str:
        return f"{self.kind}/{self.group}/{self.delivery or '-'}"


@dataclass
class Tally:
    need: int
    kept: int = 0
    tried: int = 0


@dataclass(frozen=True)
class Summary:
    state: str  # complete, short (some group under its count) or stopped
    kept: int
    target: int
    dropped: dict[str, int]
    refused: dict[str, int]
    detail: str = ""


class Stopped(Exception):
    def __init__(self, reply: Reply) -> None:
        super().__init__(reply.detail)
        self.reply = reply


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def label_schema() -> dict[str, Any]:
    """The 13 schema v3 fields as JSON Schema (label hints here; the grader's answer)."""
    props: dict[str, Any] = {}
    for f in load_schema().fields.values():
        props[f.name] = ({"type": "boolean"} if f.kind is FieldKind.BOOLEAN
                         else {"type": "string", "enum": list(f.values)})  # fmt: skip
    return {"type": "object", "additionalProperties": False, "required": list(props),
            "properties": props}  # fmt: skip


def _string(n: int) -> dict[str, Any]:
    return {"type": "string", "maxLength": n}


def seed_schema(bucket_kind: str, deliveries: list[str], types: list[str]) -> dict[str, Any]:
    seed: dict[str, Any] = {
        "type": "object", "additionalProperties": False,
        "required": ["sender_role", "asks", "amount", "urgency", "profile", "auth_result",
                     "sender_seen_before", "bulk", "delivery", "ic3_type"],
        "properties": {
            "sender_role": _string(120), "asks": _string(300), "amount": _string(60),
            "urgency": {"type": "string", "enum": ["low", "medium", "high"]},
            "profile": {"type": "string", "enum": ["org", "freemail"]},
            "auth_result": {"type": "string", "enum": list(FACT_VALUES["auth_result"])},
            "sender_seen_before": {"type": "boolean"}, "bulk": {"type": "boolean"},
            "delivery": {"type": "string", "enum": deliveries or ["none"]},
            "ic3_type": {"type": "string", "enum": types or ["none"]},
        },
    }  # fmt: skip
    del bucket_kind
    return {"type": "object", "additionalProperties": False, "required": ["refused", "seeds"],
            "properties": {"refused": {"type": "boolean"},
                           "seeds": {"type": "array", "items": seed, "maxItems": 200}}}  # fmt: skip


def email_schema(n: int) -> dict[str, Any]:
    attachment = {
        "type": "object", "additionalProperties": False,
        "required": ["name", "text", "invoice_pdf"],
        "properties": {"name": _string(100),
                       "text": {"type": ["string", "null"], "maxLength": 20000},
                       "invoice_pdf": {"type": "boolean"}},
    }  # fmt: skip
    email = {
        "type": "object", "additionalProperties": False,
        "required": ["seed", "refused", "from_name", "from_address", "reply_to", "subject",
                     "date", "body", "hidden_text", "attachments", "labels"],
        "properties": {
            "seed": {"type": "integer"}, "refused": {"type": "boolean"},
            "from_name": _string(120), "from_address": _string(200),
            "reply_to": {"type": ["string", "null"], "maxLength": 200},
            "subject": _string(300), "date": _string(40), "body": _string(40000),
            "hidden_text": {"type": ["string", "null"], "maxLength": 4000},
            "attachments": {"type": "array", "items": attachment, "maxItems": 3},
            "labels": label_schema(),
        },
    }  # fmt: skip
    return {"type": "object", "additionalProperties": False, "required": ["emails"],
            "properties": {"emails": {"type": "array", "items": email,
                                      "maxItems": n}}}  # fmt: skip


def _published_block(b: Bucket, m: mixmod.Mix) -> str:
    pub = mixmod.published()
    if b.kind == "ordinary":
        cat = pub.ordinary[b.group]
        src = pub.sources["gmail"]
        return (f"Kind: an ordinary, legitimate email that belongs in Gmail's inbox category"
                f" \"{cat.name}\" ({src['title']}, {src['url']}). It is not a scam.")  # fmt: skip
    lines = [f"Kind: a scam email of this FBI IC3 crime type ({pub.sources['ic3']['title']},"
             " Appendix B):" if len(m.groups[b.group]) == 1 else
             f"Kind: a scam email of one of these FBI IC3 crime types"
             f" ({pub.sources['ic3']['title']}, Appendix B):"]  # fmt: skip
    for t in m.groups[b.group]:
        e = pub.ic3[t]
        lines.append(f'- {e.id}: {e.name}: "{e.definition}"')
    return "\n".join(lines)


def _delivery_line(method: str) -> str:
    e = mixmod.published().delivery[method]
    src = f"MITRE ATT&CK {e.technique}" if e.technique else "operator-defined"
    return f"{e.id}: {e.name} ({src})"


def seed_prompt(b: Bucket, m: mixmod.Mix, wanted: dict[str, int]) -> str:
    total = sum(wanted.values())
    if b.kind == "scam":
        how = "; ".join(f"{n} with delivery {_delivery_line(d)}" for d, n in wanted.items())
        ask = f"Propose {total} seeds: {how}. Set ic3_type to the crime type each seed is."
    else:
        ask = f'Propose {total} seeds. Set delivery and ic3_type to "none".'
    return "\n\n".join([
        PURPOSE, _published_block(b, m), RECIPIENT,
        "A seed is the plan for one test email: sender_role (who the sender claims to be),"
        " asks (what the email asks the recipient to do), amount (an amount of money, or"
        " \"none\"), urgency, profile (which mailbox receives it), auth_result (whether the"
        " sender's domain passes email authentication: pass, none or fail), sender_seen_before"
        " (whether the recipient has had mail from this sender before), bulk (sent to many),"
        " delivery and ic3_type. Make the seeds differ from one another.",
        ask,
        "If you won't write these, set refused to true and return no seeds.",
    ])  # fmt: skip


def email_prompt(b: Bucket, m: mixmod.Mix, seeds: list[dict[str, Any]],
                 findings: list[str] | None = None) -> str:  # fmt: skip
    schema_text = load_schema().prompt_block
    numbered = json.dumps([{"seed": i} | {k: s[k] for k in SEED_TEXT_KEYS}
                           for i, s in enumerate(seeds)], ensure_ascii=False)  # fmt: skip
    parts = [
        PURPOSE, _published_block(b, m), RECIPIENT, SAFETY_RULES,
        "Write one email for each seed below. For each, return: seed (its number), refused,"
        " from_name and from_address (the sender), reply_to (an address, or null), subject,"
        " date (ISO 8601 with a time zone, in 2026), body (plain text), hidden_text (text a"
        " mail client would not show, or null), attachments (each a file name with either its"
        " plain text, or invoice_pdf true for a generated PDF invoice; at most 3), and labels:"
        " your answer to every field of the classification schema below for the email you"
        " wrote. If you won't write an email for a seed, return it with refused true and the"
        " other fields empty.",
        f"Seeds: {numbered}",
        schema_text,
    ]  # fmt: skip
    if findings:
        parts.append("Your previous answer for this seed broke the rules above: "
                     + ", ".join(findings) + ". Write it again within the rules.")  # fmt: skip
    return "\n\n".join(parts)


SEED_TEXT_KEYS = ("sender_role", "asks", "amount", "urgency", "profile", "auth_result",
                  "sender_seen_before", "bulk", "delivery", "ic3_type")  # fmt: skip
PROMPT_DIGESTS = {"purpose": _sha(PURPOSE), "recipient": _sha(RECIPIENT),
                  "safety_rules": _sha(SAFETY_RULES)}  # fmt: skip


def _auth_results(domain: str, result: str) -> list[str]:
    if result == "none":
        return []
    return [f"mx.example.net; dkim={result} header.d={domain}; dmarc={result}"
            f" header.from={domain}"]  # fmt: skip


def _date(text: str) -> datetime | None:
    try:
        d = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None or not 2025 <= d.year <= 2027:
        return None
    return d.astimezone(UTC)


def card_text(case_id: str, b: Bucket, seed: dict[str, Any], email: dict[str, Any],
              generator: str) -> str:  # fmt: skip
    """The card for one generated email: ecf's fields from the seed and the configuration, the
    generator's message fields as data (JSON strings, valid YAML), no labels (P7)."""
    name, address = str(email["from_name"]).strip(), str(email["from_address"]).strip()
    domain = address.rsplit("@", 1)[-1].lower()
    date = _date(str(email["date"]))
    if date is None:
        raise ValueError("date")
    attachments: list[dict[str, Any]] = []
    for a in cast(list[dict[str, Any]], email["attachments"]):
        if a["invoice_pdf"]:
            attachments.append({"name": a["name"], "generate": "invoice_pdf",
                                "content_type": "application/pdf"})  # fmt: skip
        else:
            attachments.append({"name": a["name"], "content_type": "text/plain",
                                "text": a["text"] or ""})  # fmt: skip

    def j(v: Any) -> str:
        return json.dumps(v, ensure_ascii=False)

    title = f"Generated {b.kind}: {b.group}" + (f" ({b.delivery})" if b.delivery else "")
    facts = {"auth_result": seed["auth_result"],
             "sender_seen_before": seed["sender_seen_before"]}  # fmt: skip
    lines = [
        "---", f"id: {case_id}",
        f"title: {j(title)}",
        f"threat: {j(b.group if b.kind == 'scam' else 'none (ordinary mail)')}",
        "author: generated", f"generator: {j(generator)}", f"profile: {seed['profile']}",
        f"from: {j(f'{name} <{address}>' if name else address)}",
        *([f"reply_to: {j(email['reply_to'])}"] if email.get("reply_to") else []),
        f"subject: {j(email['subject'])}", f"date: {date.strftime('%Y-%m-%dT%H:%M:%SZ')}",
        f"auth_results: {j(_auth_results(domain, seed['auth_result']))}",
        f"bulk: {'true' if seed['bulk'] else 'false'}",
        *([f"hidden_text: {j(email['hidden_text'])}"] if email.get("hidden_text") else []),
        f"attachments: {j(attachments)}",
        "expected:", "  labels: {}", f"  facts: {j(facts)}",
        "---",
    ]  # fmt: skip
    return "\n".join(lines) + "\n" + str(email["body"]).strip("\n") + "\n"


def problems(card: Card, email: dict[str, Any]) -> tuple[str | None, list[str]]:
    """(a drop reason, hygiene finding kinds): markup and brand checks drop; findings retry."""
    texts = [str(email.get(k) or "") for k in ("from_name", "subject", "body", "hidden_text")]
    texts += [
        str(a.get("text") or "") + str(a.get("name") or "")
        for a in cast(list[dict[str, Any]], email["attachments"])
    ]
    if any("\n## html" in "\n" + t for t in texts):
        return "HTML markup", []
    if (kind := gencheck.markup(texts)) is not None:
        return kind, []
    if gencheck.named_brand(str(email.get("from_name") or "")):
        return "brand or agency as sender", []
    if not parseaddr(str(email.get("from_address") or ""))[1]:
        return "schema misfit", []
    findings = scan_card(card, card.id)
    return None, sorted({f.kind for f in findings})


@dataclass
class Run:
    root: Path
    mix: mixmod.Mix
    count: int
    adapter: Adapter
    mode: str  # prod or dev
    k: int = K_DEFAULT
    seeds_only: bool = False  # a seed run: seeds for every group, no email
    shared: dict[str, Any] | None = None  # `seeds_from` (OD-545): no seed call, no top-up
    now: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    echo: Callable[[str], None] = field(default=lambda _s: None)

    def __post_init__(self) -> None:
        self.work = Work(self.root)
        self.breaker = Breaker()
        self.dups = gencheck.NearDuplicates()
        self.tallies: dict[str, Tally] = {}
        self.buckets: dict[str, Bucket] = {}
        for slot, n in Counter(mixmod.plan(self.mix, self.count)).items():
            b = Bucket(slot.kind, slot.group, slot.delivery)
            self.buckets[b.key], self.tallies[b.key] = b, Tally(n)
        self.dropped: Counter[tuple[str, str]] = Counter()  # (reason, group)
        self.refused: Counter[str] = Counter()  # group
        self.hygiene_kinds: Counter[str] = Counter()  # finding kinds of hygiene drops
        self.retried_kinds: Counter[str] = Counter()  # first-pass kinds of retries that passed
        self.retried_passed = 0
        self.usage: Counter[str] = Counter()
        self.calls = 0
        self.seconds = 0.0
        self.case_no: Counter[str] = Counter()
        self.unmeasured: set[str] = set()  # groups the shared seed run has no seeds for

    # Calls and logs

    def _ask(self, stage: str, group: str, prompt: str, schema: dict[str, Any],
             seeds: list[str]) -> Reply:  # fmt: skip
        reply = self.adapter.ask(prompt, schema, self.root / "work" / "call")
        self.calls += 1
        self.seconds += reply.seconds
        self.usage.update(reply.usage)
        self.work.append("calls", [{"stage": stage, "group": group, "seeds": seeds,
                                    "prompt_sha256": _sha(prompt), "at": self.now().isoformat()}
                                   | reply.record()])  # fmt: skip
        if self.breaker.note(reply):
            raise Stopped(reply)
        return reply

    def _outcome(self, seed_id: str, outcome: str, reason: str = "",
                 case_id: str | None = None,
                 extra: dict[str, Any] | None = None) -> None:  # fmt: skip
        """`extra` holds finding kind names only (P27): never email text."""
        self.work.append("outcomes", [{"seed": seed_id, "outcome": outcome, "reason": reason,
                                       **({"case": case_id} if case_id else {}),
                                       **(extra or {})}])  # fmt: skip

    def _note_kinds(self, outcome: str, extra: dict[str, Any]) -> None:
        if outcome == "hygiene":
            self.hygiene_kinds.update(extra.get("findings", {}).get("retry") or [])
        if outcome == "kept" and "retried" in extra:
            self.retried_passed += 1
            self.retried_kinds.update(extra["retried"])

    # Resume: the logs rebuilt into the run's state

    def _restore(self) -> tuple[dict[str, list[dict[str, Any]]], set[str], set[str]]:
        """Seeds by group, seed ids already settled, groups whose seed call is done."""
        seeds: dict[str, list[dict[str, Any]]] = {}
        seeded: set[str] = set()
        for row in self.work.read("seeds"):
            seeded.add(row["group"])
            if row.get("no_shared_seeds"):
                self.unmeasured.add(row["group"])
            if row.get("refused"):
                self.refused[row["group"]] += (
                    int(row["need"]) if "need" in row else
                    sum(t.need for b, t in ((self.buckets[k], t) for k, t in self.tallies.items())
                        if b.group == row["group"]))  # fmt: skip
            if row.get("id"):
                seeds.setdefault(row["group"], []).append(row)
        settled: set[str] = set()
        by_id = {s["id"]: s for rows in seeds.values() for s in rows}
        for o in self.work.read("outcomes"):
            settled.add(o["seed"])
            s = by_id.get(o["seed"])
            if s is None:
                continue
            key = s["bucket"]
            if key in self.tallies:
                self.tallies[key].tried += 1
            if o["outcome"] == "kept":
                self.tallies[key].kept += 1
                self.case_no[s["group"]] += 1
            elif o["outcome"] == "refused":
                self.refused[s["group"]] += 1
            else:
                self.dropped[(o["reason"], s["group"])] += 1
            self._note_kinds(o["reason"] or o["outcome"], o)
        for path in sorted((self.root / "cases").glob("*.md")):
            self.dups.seen(parse_card(path.read_text(encoding="utf-8"), source=path.name).body)
        for c in self.work.read("calls"):
            self.calls += 1
            self.seconds += float(c.get("seconds") or 0)
            self.usage.update(c.get("usage") or {})
        return seeds, settled, seeded

    # The stages

    def _groups(self) -> dict[tuple[str, str], list[Bucket]]:
        out: dict[tuple[str, str], list[Bucket]] = {}
        for b in self.buckets.values():
            out.setdefault((b.kind, b.group), []).append(b)
        return out

    def _seed(self, kind: str, group: str, buckets: list[Bucket],
              offset: int = 0) -> list[dict[str, Any]]:  # fmt: skip
        """The group's seeds, or (`offset` > 0, a top-up) more for its short buckets."""
        short = {b.key: self.tallies[b.key].need - self.tallies[b.key].kept for b in buckets}
        wanted = {b.delivery or "none": math.ceil(SEED_FACTOR * short[b.key]) for b in buckets}
        types = list(self.mix.groups[group]) if kind == "scam" else []
        deliveries = [d for d in wanted if d != "none"]
        b0 = buckets[0]
        prompt = seed_prompt(b0, self.mix, wanted)
        schema = seed_schema(kind, deliveries, types)
        while True:
            reply = self._ask("seed", group, prompt, schema, [])
            if reply.status == "ok":
                break
            if reply.status == "refused":
                return self._seeds_refused(group, sum(short.values()))
        answer = cast(dict[str, Any], reply.answer)
        if answer["refused"]:
            return self._seeds_refused(group, sum(short.values()))
        by_delivery = {(b.delivery or "none"): b for b in buckets}
        rows: list[dict[str, Any]] = []
        for i, s in enumerate(cast(list[dict[str, Any]], answer["seeds"])):
            d = s["delivery"] if kind == "scam" else "none"
            b = by_delivery.get(d)
            if b is None or (kind == "scam" and s["ic3_type"] not in types):
                continue  # not a seed this group asked for
            rows.append({"id": f"{group}-s{offset + i:03d}", "group": group, "bucket": b.key,
                         **{k: s[k] for k in SEED_TEXT_KEYS}})  # fmt: skip
        self.work.append("seeds", rows or [{"group": group, "seeds": 0}])
        return rows

    def _seeds_refused(self, group: str, need: int) -> list[dict[str, Any]]:
        self.work.append("seeds", [{"group": group, "refused": True, "need": need}])
        self.refused[group] += need
        return []

    def _accept(self, b: Bucket, seed: dict[str, Any], email: dict[str, Any],
                retried: bool) -> tuple[str, list[str]]:  # fmt: skip
        """`kept` (with the case id), `retry` (with finding kinds) or a drop reason."""
        prefix = DEV_PREFIX if self.mode == "dev" else ""
        case_id = f"{prefix}{b.group}-{self.case_no[b.group] + 1:03d}"
        try:
            text = card_text(case_id, b, seed, email, self.adapter.model)
            card = parse_card(text, source=case_id)
        except (ValueError, InvalidInputError) as exc:
            return ("bad date" if str(exc) == "date" else "schema misfit"), []
        reason, findings = problems(card, email)
        if reason is not None:
            return reason, []
        if findings:
            return ("hygiene", findings) if retried else ("retry", findings)
        if (why := self.dups.seen(card.body)) is not None:
            return f"near-duplicate ({why})", []
        self.case_no[b.group] += 1
        (self.root / "cases").mkdir(exist_ok=True)
        (self.root / "cases" / f"{case_id}.md").write_text(text, encoding="utf-8")
        self.work.append("proposals", [{"id": case_id, "seed": seed["id"],
                                        "labels": email["labels"]}])  # fmt: skip
        return "kept", [case_id]

    def _settle(self, b: Bucket, seed: dict[str, Any], outcome: str,
                case_id: str | None = None,
                extra: dict[str, Any] | None = None) -> None:  # fmt: skip
        t = self.tallies[b.key]
        t.tried += 1
        self._note_kinds(outcome, extra or {})
        if outcome == "kept":
            t.kept += 1
            self._outcome(seed["id"], "kept", case_id=case_id, extra=extra)
        elif outcome == "refused":
            self.refused[b.group] += 1
            self._outcome(seed["id"], "refused")
        else:
            self.dropped[(outcome, b.group)] += 1
            self._outcome(seed["id"], "dropped", outcome, extra=extra)

    def _emails(self, group: str, seeds: list[dict[str, Any]], settled: set[str]) -> None:
        pool = [s for s in seeds if s["id"] not in settled]
        topups = 0
        while True:
            batch: list[dict[str, Any]] = []
            for s in pool:
                t = self.tallies[s["bucket"]]
                pending = sum(1 for x in batch if x["bucket"] == s["bucket"])
                if t.kept + pending < t.need and t.tried + pending < MAX_TRIES_FACTOR * t.need:
                    batch.append(s)
                if len(batch) == self.k:
                    break
            if not batch:
                if self.shared is not None:
                    return  # shared seeds: the pool is the same for every model (OD-545)
                short = [b for b in self.buckets.values() if b.group == group
                         and self.tallies[b.key].kept < self.tallies[b.key].need
                         and self.tallies[b.key].tried < MAX_TRIES_FACTOR
                         * self.tallies[b.key].need]  # fmt: skip
                if not short or topups >= MAX_TOPUPS:
                    return
                topups += 1
                self.echo(f"{group}: asking for more seeds")
                more = self._seed(short[0].kind, group, short, offset=len(seeds))
                if not more:
                    return
                seeds.extend(more)
                pool.extend(more)
                continue
            pool = [s for s in pool if s not in batch]
            self._email_call(group, batch)

    def _email_call(self, group: str, batch: list[dict[str, Any]],
                    findings: list[str] | None = None) -> None:  # fmt: skip
        b0 = self.buckets[batch[0]["bucket"]]
        prompt = email_prompt(b0, self.mix, batch, findings)
        reply = self._ask("retry" if findings else "email", group, prompt,
                          email_schema(len(batch)), [s["id"] for s in batch])  # fmt: skip
        if reply.status == "refused":
            for s in batch:
                self._settle(self.buckets[s["bucket"]], s, "refused")
            return
        if reply.status != "ok":
            for s in batch:
                self._settle(self.buckets[s["bucket"]], s, f"call {reply.detail}")
            return
        got = {int(e["seed"]): e for e in cast(dict[str, Any], reply.answer)["emails"]}
        for i, s in enumerate(batch):
            b = self.buckets[s["bucket"]]
            email = got.get(i)
            if email is None:
                self._settle(b, s, "missing from the answer")
                continue
            if email["refused"]:
                self._settle(b, s, "refused")
                continue
            outcome, more = self._accept(b, s, email, retried=findings is not None)
            if outcome == "retry":
                self._email_call(group, [s], more)
                continue
            extra: dict[str, Any] = {}
            if outcome == "hygiene":
                extra = {"findings": {"first": findings or [], "retry": more}}
            elif outcome == "kept" and findings is not None:
                extra = {"retried": findings}
            self._settle(b, s, outcome, more[0] if outcome == "kept" else None, extra)

    def run(self, *, resume: bool) -> Summary:
        empty: tuple[dict[str, list[dict[str, Any]]], set[str], set[str]] = ({}, set(), set())
        seeds, settled, seeded = self._restore() if resume else empty
        write_manifest(self, "stopped")  # until the end: generated from the start, resumable
        detail, state = "", "complete"
        try:
            for (kind, group), buckets in self._groups().items():
                if group in seeded and not seeds.get(group):
                    continue  # refused, or no seed this group asked for
                if group not in seeded:
                    if self.shared is not None:
                        self.unmeasured.add(group)  # never a seed call of this model's
                        continue
                    self.echo(f"{group}: asking for seeds")
                    seeds[group] = self._seed(kind, group, buckets)
                if seeds.get(group) and not self.seeds_only:
                    self.echo(f"{group}: writing emails")
                    self._emails(group, seeds[group], settled)
        except Stopped as exc:
            state = "stopped"
            detail = (f"stopped: {exc.reply.status} ({exc.reply.detail}); fix the cause, then"
                      " run the same command with --resume")  # fmt: skip
        kept = sum(t.kept for t in self.tallies.values())
        if state == "complete" and self.seeds_only:
            state = "seeded"
        elif state == "complete" and kept < self.count:
            state = "short"
        report = build_all(self.root) if any((self.root / "cases").glob("*.md")) else None
        if report is not None and report.findings:  # every card was scanned as it was made
            raise InvalidInputError("a card failed the hygiene scan at build time; nothing more"
                                    " is written")  # fmt: skip
        write_manifest(self, state)
        dropped: Counter[str] = Counter()
        for (reason, _g), n in self.dropped.items():
            dropped[reason] += n
        return Summary(state, kept, self.count, dict(dropped), dict(self.refused), detail)


def _counts_by_group(c: Counter[tuple[str, str]]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for (reason, group), n in sorted(c.items()):
        out.setdefault(reason, {})[group] = n
    return out


def case_hashes(root: Path) -> dict[str, dict[str, str]]:
    """Each case's built message and expected facts, hashed: what must not change after
    generation (labels and expected rule and safety are added later, so they're left out)."""
    out: dict[str, dict[str, str]] = {}
    index = root / "labels.jsonl"
    if not index.exists():  # a run that kept no email
        return out
    for line in index.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row: dict[str, Any] = json.loads(line)
        path = root / row["file"]
        expected: dict[str, Any] = row.get("expected") or {}
        facts: dict[str, Any] = expected.get("facts") or {}
        out[row["id"]] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                          "facts": _sha(json.dumps(facts, sort_keys=True))}  # fmt: skip
    return out


def write_manifest(run: Run, state: str) -> None:
    mix_doc = run.mix.as_dict()
    groups: dict[str, dict[str, int]] = {}
    for b in run.buckets.values():
        g = groups.setdefault(b.group, {"planned": 0, "kept": 0, "tried": 0})
        t = run.tallies[b.key]
        g["planned"] += t.need
        g["kept"] += t.kept
        g["tried"] += t.tried
    selected = sorted(g for g, v in groups.items() if v["tried"] and
                      (v["tried"] - v["kept"]) * 3 > v["tried"])  # fmt: skip
    has_cases = (run.root / "labels.jsonl").exists()
    doc = {
        "kind": genset.KIND, "format": FORMAT, "mode": run.mode, "state": state,
        "ecf_version": __version__, "written_at": run.now().isoformat(),
        "generator": run.adapter.describe(), "count": run.count, "k": run.k,
        "mix": mix_doc, "mix_sha256": _sha(json.dumps(mix_doc, sort_keys=True)),
        "groups": groups, "selected": selected,
        "dropped": _counts_by_group(run.dropped), "refused": dict(run.refused),
        "hygiene_kinds": dict(run.hygiene_kinds),
        "seeds_sha256": seeds_digest(run.root), "seeds_only": run.seeds_only,
        "seeds_from": run.shared, "no_shared_seeds": sorted(run.unmeasured),
        "kept_seeds": sorted(o["seed"] for o in run.work.read("outcomes")
                             if o.get("outcome") == "kept"),
        "retried_passed": {"count": run.retried_passed, "kinds": dict(run.retried_kinds)},
        "repeated_openings": run.dups.repeated_openings(),
        "prompts": PROMPT_DIGESTS | {"schema": load_schema().digest},
        "calls": run.calls, "seconds": round(run.seconds, 1), "usage": dict(run.usage),
        "cases": case_hashes(run.root) if has_cases else {},
    }  # fmt: skip
    tmp = run.root / f".{genset.MANIFEST}.tmp"
    tmp.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, run.root / genset.MANIFEST)


def seeds_digest(root: Path) -> str:
    """The sha256 of a set's seed log (`work/seeds.jsonl`), or of nothing when it has none."""
    path = root / "work" / "seeds.jsonl"
    return hashlib.sha256(path.read_bytes() if path.exists() else b"").hexdigest()


def share_seeds(root: Path, source: Path, mode: str, count: int, mix: mixmod.Mix,
                k: int) -> dict[str, Any]:  # fmt: skip
    """Copy a seed run's seeds into a new run's work log (OD-545): refused unless the source is
    a finished seed run of the same mode, count and mix whose seed log is as it recorded; a
    group it has no seeds for gets a `no_shared_seeds` marker (unmeasured, never re-seeded).
    Returns the manifest's `seeds_from`."""
    doc = verify(source)
    if doc.get("state") != "seeded":
        raise InvalidInputError(f"{source}: not a finished seed run (`ecf eval generate"
                                " --seeds-only`)")  # fmt: skip
    mix_sha = _sha(json.dumps(mix.as_dict(), sort_keys=True))
    for what, mine, theirs in (("mode", mode, doc.get("mode")),
                               ("count", count, doc.get("count")),
                               ("mix", mix_sha, doc.get("mix_sha256"))):  # fmt: skip
        if mine != theirs:
            raise InvalidInputError(f"{source}: its {what} differs from this run's; shared seeds"
                                    " need the same mode, count and mix")  # fmt: skip
    if int(doc.get("k") or k) != k:
        raise InvalidInputError(f"{source}: made with --k {doc.get('k')}; use the same --k")
    if seeds_digest(source) != doc.get("seeds_sha256"):
        raise InvalidInputError(f"{source}: its seeds changed since the seed run; make a new"
                                " seed run rather than edit one")  # fmt: skip
    rows = [r | {"shared": True} for r in Work(source).read("seeds") if r.get("id")]
    have = {r["group"] for r in rows}
    groups = sorted({s.group for s in mixmod.plan(mix, count)})
    markers = [{"group": g, "no_shared_seeds": True} for g in groups if g not in have]
    Work(root).append("seeds", rows + markers)
    g = cast(dict[str, Any], doc["generator"])
    return {"set": source.name, "generator": f"{g.get('provider')}:{g.get('model')}",
            "model": g.get("model"), "seeds_sha256": doc["seeds_sha256"],
            "rows_sha256": _sha(json.dumps(rows, sort_keys=True))}  # fmt: skip


def read_manifest(root: Path) -> dict[str, Any]:
    path = root / genset.MANIFEST
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise InvalidInputError(
            f"{root}: no readable {genset.MANIFEST}; not a generated set"
        ) from None
    if not isinstance(doc, dict) or cast(dict[str, Any], doc).get("kind") != genset.KIND:
        raise InvalidInputError(f"{root}: {genset.MANIFEST} isn't a generated set's")
    return cast(dict[str, Any], doc)


def verify(root: Path) -> dict[str, Any]:
    """The manifest, after checking every case's message and facts are as generated (P17): an
    edited set is refused. A guard against mistakes, not against a determined user."""
    doc = read_manifest(root)
    if doc.get("state") == "stopped":
        raise InvalidInputError(f"{root}: generation stopped before the end; finish it with"
                                " `ecf eval generate --resume`")  # fmt: skip
    if case_hashes(root) != doc.get("cases"):
        raise InvalidInputError(f"{root}: the set's emails or facts changed since it was"
                                " generated; generate a new set rather than edit one")  # fmt: skip
    return doc


def start(root: Path, *, resume: bool) -> None:
    """A new run needs an empty folder; `--resume` needs a stopped one (P17)."""
    if resume:
        doc = read_manifest(root) if (root / genset.MANIFEST).exists() else None
        if doc is not None and doc.get("state") != "stopped":
            raise InvalidInputError(f"{root}: this set is finished; nothing to resume")
        if not (root / "work").is_dir():
            raise InvalidInputError(f"{root}: nothing to resume (no work/ folder)")
        return
    if root.exists() and any(root.iterdir()):
        raise InvalidInputError(f"{root} isn't empty: a generated set goes in a new folder")
    root.mkdir(parents=True, exist_ok=True)


def mode_of(root: Path) -> str:
    return str(read_manifest(root).get("mode"))
