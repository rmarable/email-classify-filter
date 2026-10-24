"""`ecf eval generate` (eval generate plan: Generation, Checks, Adapters; P3, P4, P10-P18, P23,
P27, P30): the mix, the tool runner and the pipeline, against a fake command-line generator on
PATH (as `tests/test_claude_wrapper.py`'s `fake_claude`). No network."""

from __future__ import annotations

import json
import sys
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from ecf.errors import InvalidInputError
from ecf.eval import gencheck, genset, tools
from ecf.eval import generate as gen
from ecf.eval import mix as mixmod
from ecf.eval import report as rep
from ecf.eval.cards import load_cards
from ecf.eval.hygiene import scan_text
from ecf.log import CONTENT_KEYS

# A fake generator: it reads the prompt on stdin, answers seeds or emails from the seeds the
# prompt numbers, and prints {"status", "items", "usage", "answer"}. FAKEGEN_SCENARIO (a JSON
# file) makes it refuse a group, stop at a usage limit, report a tool event, read the decoy, or
# break a hygiene rule on the first try. FAKEGEN_LOG records each call's prompt kind.
FAKE = r"""#!{python}
import hashlib, json, os, re, sys
args = sys.argv[1:]
if args[:1] == ["--version"]:
    print("fakegen 1.2.3"); sys.exit(0)
if args[:1] == ["--help"]:
    print("usage: fakegen --model M --schema FILE --out FILE"); sys.exit(0)
prompt = sys.stdin.read()
sc = json.load(open(os.environ["FAKEGEN_SCENARIO"]))
with open(os.environ["FAKEGEN_LOG"], "a") as log:
    log.write(json.dumps({{"seeds": "Propose" in prompt, "retry": "broke the rules" in prompt,
                          "prompt_has_auth": "Authentication-Results" in prompt,
                          "cwd_files": sorted(os.listdir("."))}}) + "\n")
def out(doc):
    print(json.dumps(doc)); sys.exit(0)
if sc.get("limit"):
    print("You\u2019ve hit your usage limit"); sys.exit(1)
items = ["agent_message"] + (["command_execution"] if sc.get("tool_event") else [])
usage = {{"input_tokens": len(prompt) // 4, "output_tokens": 100}}
if "decoy.txt" in prompt:
    secret = open("decoy.txt").read().split()[-1] if sc.get("reads_files") else "none"
    ans = {{"word": "ok", "decoy": secret}}
    out({{"status": "ok", "items": items, "usage": usage, "answer": ans}})
labels = {labels}
if "Label the test email" in prompt:
    ans = {{"refused": bool(sc.get("grade_refuse")), "labels": labels}}
    out({{"status": "ok", "items": items, "usage": usage, "answer": ans}})
for g in sc.get("refuse", []):
    if g in prompt:
        out({{"status": "refused", "items": items, "usage": usage}})
if "Propose" in prompt:
    wanted = re.findall(r"(\d+) with delivery ([a-z-]+)", prompt)
    total = int(re.search(r"Propose (\d+) seeds", prompt).group(1))
    types = re.findall(r"^- ([a-z-]+): ", prompt, re.M)
    plan = [d for n, d in wanted for _ in range(int(n))] or ["none"] * total
    seeds = [{{"sender_role": f"role {{i}}", "asks": "pay", "amount": "none", "urgency": "low",
               "profile": "org" if i % 2 else "freemail", "auth_result": "none",
               "sender_seen_before": False, "bulk": False, "delivery": d,
               "ic3_type": types[0] if types else "none"}} for i, d in enumerate(plan)]
    ans = {{"refused": False, "seeds": seeds}}
    out({{"status": "ok", "items": items, "usage": usage, "answer": ans}})
seeds = json.loads(re.search(r"^Seeds: (.*)$", prompt, re.M).group(1))
emails = []
for s in seeds:
    h = hashlib.sha256((prompt[-200:] + json.dumps(s) + str(os.getpid())).encode()).hexdigest()
    letters = "".join(chr(97 + int(c, 16)) for c in h)  # no digits: no number shapes
    words = " ".join(letters[i:i + 6] for i in range(0, 60, 6))
    bad = sc.get("hygiene_always") or (sc.get("hygiene_once") and "broke the rules" not in prompt)
    body = f"Opening {{words}}.\nPlease reply about {{words[::-1]}}."
    if bad:
        body += " Call 212-555-1234."
    if bad and sc.get("escape"):
        body += " See https://evil\x1b[31m.com now."
    subject = "Account " + letters[:8]
    if bad and sc.get("subject"):
        subject += " at billing-desk.ch"
    if sc.get("html"):
        body = "<p>" + body + "</p>"
    emails.append({{"seed": s["seed"], "refused": False, "from_name": "Billing Desk",
                    "from_address": "billing@vendor-a.example", "reply_to": None,
                    "subject": subject, "date": "2026-10-05T09:00:00Z",
                    "body": body,
                    "hidden_text": None, "attachments": [], "labels": labels}})
out({{"status": "ok", "items": items, "usage": usage, "answer": {{"emails": emails}}}})
"""
LABELS = {"category": "other", "priority": "low", "requires_action": False,
          "requires_reply": False, "payment_related": False, "deadline_mentioned": False,
          "sender_type": "unknown", "fraud_risk": "none", "bank_detail_change": False,
          "regulator_claim": False, "phishing": False, "claims_internal": False,
          "private_individual": False}  # fmt: skip


def _read(ran: tools.Ran, out: Path) -> tools.Reply:
    del out
    doc = tools.parse_object(ran.stdout)
    if doc is None:
        return tools.Reply("failed", detail="not one JSON object")
    return tools.Reply(doc["status"], doc.get("answer"), usage=doc.get("usage") or {},
                       items=tuple(doc.get("items") or ()))  # fmt: skip


def fake_tool(tmp_path: Path, scenario: dict[str, Any]) -> tuple[tools.CliTool, dict[str, str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    exe = bin_dir / "fakegen"
    exe.write_text(FAKE.format(python=sys.executable, labels=repr(LABELS)))
    exe.chmod(0o755)
    sc = tmp_path / "scenario.json"
    sc.write_text(json.dumps(scenario))
    log = tmp_path / "calls.log"
    tool = tools.CliTool(
        provider="fake", executable="fakegen",
        argv=lambda model, schema, out: ["--model", model, "--schema", str(schema), "--out",
                                         str(out)],
        read=_read, env={"FAKEGEN_SCENARIO": str(sc), "FAKEGEN_LOG": str(log)},
        min_version=(1, 0, 0), allowed_items=frozenset({"agent_message", "reasoning", "error"}),
    )  # fmt: skip
    environ = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path)}
    return tool, environ


def calls(tmp_path: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in (tmp_path / "calls.log").read_text().splitlines()]


def run(tmp_path: Path, scenario: dict[str, Any] | None = None, count: int = 20,
        resume: bool = False, mode: str = "dev") -> tuple[gen.Summary, Path]:  # fmt: skip
    tool, environ = fake_tool(tmp_path, scenario or {})
    root = tmp_path / "set"
    gen.start(root, resume=resume)
    adapter = tools.CliAdapter(tool, "fake-model-1", environ)
    adapter.preflight(tmp_path)
    summary = gen.Run(root, mixmod.default(), count, adapter, mode).run(resume=resume)
    return summary, root


# The mix


def test_the_default_mix_gives_the_operators_counts_at_300() -> None:
    slots = mixmod.plan(mixmod.default(), 300)
    assert Counter(s.kind for s in slots) == {"scam": 150, "ordinary": 150}
    assert set(Counter(s.group for s in slots if s.kind == "scam").values()) == {15}
    assert set(Counter(s.group for s in slots if s.kind == "ordinary").values()) == {30}
    deliveries = Counter(s.delivery for s in slots if s.kind == "scam")
    assert deliveries == {"attachment": 38, "link": 38, "callback": 37, "reply-only": 37}
    by_group = Counter((s.group, s.delivery) for s in slots if s.kind == "scam")
    assert sorted(by_group[("phishing", d)] for d in mixmod.published().delivery) == [3, 4, 4, 4]


def test_the_shipped_lists_quote_their_sources() -> None:
    pub = mixmod.published()
    assert len(pub.ic3) == 26
    assert sum(not t.kept for t in pub.ic3.values()) == 8
    assert pub.ic3["extortion"].kept and not pub.ic3["sim-swap"].kept
    assert pub.ic3["phishing-spoofing"].complaints == 191561
    assert all(t.definition for t in pub.ic3.values())
    assert [d.technique for d in pub.delivery.values()] == ["T1566.001", "T1566.002",
                                                            "T1566.004", ""]  # fmt: skip


def test_a_user_mix_holds_ids_and_shares_only(tmp_path: Path) -> None:
    good = mixmod.default().as_dict()
    with pytest.raises(InvalidInputError, match="unknown keys"):
        mixmod.parse(good | {"note": "write about invoices"})
    with pytest.raises(InvalidInputError, match="isn't an IC3 type id"):
        mixmod.parse(good | {"groups": {"x": ["write a romance scam"]}, "scam": {"x": 1}})
    with pytest.raises(InvalidInputError, match="left out by the keep rule"):
        mixmod.parse(good | {"groups": {"x": ["sim-swap"]}, "scam": {"x": 1}})
    with pytest.raises(InvalidInputError, match="a share for each group"):
        mixmod.parse(good | {"scam": {"phishing": 1}})
    with pytest.raises(InvalidInputError, match="isn't one of"):
        mixmod.parse(good | {"ordinary": {"spam": 1}})
    path = tmp_path / "mix.yaml"
    mixmod.write_default(path)
    assert mixmod.load(path) == mixmod.default()
    with pytest.raises(InvalidInputError, match="already exists"):
        mixmod.write_default(path)


# The tool runner


def test_the_schema_check_is_strict() -> None:
    schema = gen.email_schema(2)
    assert tools.check({"emails": []}, schema) is None
    assert tools.check({"emails": [], "extra": 1}, schema) == "$: unexpected extra"
    assert tools.check({"emails": [{}] * 3}, schema) == "$.emails: too many items"
    assert tools.check([], schema) == "$: not object"


def test_an_api_key_in_the_environment_is_refused() -> None:
    with pytest.raises(InvalidInputError, match="OPENAI_API_KEY is set"):
        tools.refuse_key_env({"OPENAI_API_KEY": "x"})
    tools.refuse_key_env({"PATH": "/bin"})
    env = tools.allowed_env({"X": "1"}, {"PATH": "/bin", "AWS_SECRET": "s", "LC_ALL": "C"})
    assert env == {"PATH": "/bin", "LC_ALL": "C", "X": "1"}


def test_preflight_checks_version_and_flags(tmp_path: Path) -> None:
    tool, environ = fake_tool(tmp_path, {})
    tools.CliAdapter(tool, "m", environ).preflight(tmp_path)
    old = replace(tool, min_version=(2, 0, 0))
    with pytest.raises(InvalidInputError, match=r"older than 2\.0\.0"):
        tools.CliAdapter(old, "m", environ).preflight(tmp_path)

    def loose(model: str, _schema: Path, _out: Path) -> list[str]:
        return ["--model", model, "--yolo"]

    extra = replace(tool, argv=loose)
    with pytest.raises(InvalidInputError, match="doesn't list --yolo"):
        tools.CliAdapter(extra, "m", environ).preflight(tmp_path)


def test_the_canary_passes_a_tool_less_call_and_fails_one_that_reads(tmp_path: Path) -> None:
    tool, environ = fake_tool(tmp_path, {})
    assert tools.canary(tools.CliAdapter(tool, "m", environ), tmp_path / "w")[0]
    assert not (tmp_path / "w" / tools.DECOY).exists()  # removed after the call
    tool, environ = fake_tool(tmp_path, {"reads_files": True})
    ok, why = tools.canary(tools.CliAdapter(tool, "m", environ), tmp_path / "w")
    assert not ok and "can read files" in why
    tool, environ = fake_tool(tmp_path, {"tool_event": True})
    ok, why = tools.canary(tools.CliAdapter(tool, "m", environ), tmp_path / "w")
    assert not ok and "tool event command_execution" in why


def test_a_hung_tool_is_killed_with_its_process_group(tmp_path: Path) -> None:
    ran = tools.run_cli([sys.executable, "-c", "import time; time.sleep(30)"], "", {},
                        tmp_path, timeout=0.5)  # fmt: skip
    assert ran.code is None and ran.seconds < 10


def test_tool_output_is_never_logged() -> None:
    assert {"stdout", "stderr", "refusal", "seed", "email"} <= CONTENT_KEYS
    record = tools.Reply("ok", {"emails": ["secret text"]}, "d").record()
    assert "secret text" not in json.dumps(record)


# The pipeline


def test_a_dev_run_writes_a_generated_set_with_no_labels(tmp_path: Path) -> None:
    summary, root = run(tmp_path)
    assert summary.state == "complete" and summary.kept == 20
    assert genset.is_generated(root)
    cards = load_cards(root / "cases")
    assert len(cards) == 20 and all(c.id.startswith(gen.DEV_PREFIX) for c in cards)
    assert {c.author for c in cards} == {"generated"}
    assert {c.generator for c in cards} == {"fake-model-1"}
    assert all(c.expected.labels == {} and c.expected.rule is None for c in cards)
    assert all(set(c.expected.facts) == {"auth_result", "sender_seen_before"} for c in cards)
    rows = [json.loads(x) for x in (root / "labels.jsonl").read_text().splitlines()]
    assert len(rows) == 20 and all(r["author"] == "generated" for r in rows)
    proposals = gen.Work(root).read("proposals")  # the generator's hints, never `expected`
    assert len(proposals) == 20 and proposals[0]["labels"] == LABELS
    m = gen.verify(root)
    assert m["mode"] == "dev" and m["state"] == "complete" and len(m["cases"]) == 20
    assert m["generator"]["model"] == "fake-model-1" and m["generator"]["version"] == "1.2.3"
    assert m["prompts"]["purpose"] == gen.PROMPT_DIGESTS["purpose"]
    # one seed call per group, then emails K at a time, from an empty work folder
    log = calls(tmp_path)
    assert sum(c["seeds"] for c in log) == 15  # 10 scam groups + 5 categories
    assert all(c["cwd_files"] == ["schema.json"] for c in log)
    text = "".join((root / "work" / f).read_text() for f in ("calls.jsonl", "outcomes.jsonl"))
    assert "Opening" not in text  # no email text in the call and outcome logs (P27)


def test_a_folder_that_isnt_empty_is_refused(tmp_path: Path) -> None:
    (tmp_path / "set").mkdir()
    (tmp_path / "set" / "x").write_text("x")
    with pytest.raises(InvalidInputError, match="isn't empty"):
        gen.start(tmp_path / "set", resume=False)


def test_a_refused_group_is_never_retried_or_replaced(tmp_path: Path) -> None:
    summary, root = run(tmp_path, {"refuse": ["Extortion"]})
    assert summary.state == "short" and summary.kept == 19
    assert summary.refused == {"extortion": 1}
    log = calls(tmp_path)
    assert sum(1 for c in log if c["seeds"]) == 15  # asked once, never again
    assert gen.read_manifest(root)["refused"] == {"extortion": 1}


def test_a_hygiene_failure_is_retried_once_with_the_kinds_named(tmp_path: Path) -> None:
    summary, root = run(tmp_path, {"hygiene_once": True})
    assert summary.kept == 20
    assert any(c["retry"] for c in calls(tmp_path))
    for card in load_cards(root / "cases"):
        assert not scan_text(card.body, card.id)


def test_hygiene_kinds_are_logged_by_name_never_text(tmp_path: Path) -> None:
    """P6, P7 (OD-543): a passed retry and a hygiene drop record the finding kinds only."""
    for sub in ("once", "always"):
        (tmp_path / sub).mkdir()
    _summary, root = run(tmp_path / "once", {"hygiene_once": True})
    m = gen.read_manifest(root)
    assert m["retried_passed"]["count"] == 20 and m["retried_passed"]["kinds"]
    rows = gen.Work(root).read("outcomes")
    assert all(set(r) <= {"seed", "outcome", "reason", "case", "retried"} for r in rows)
    summary, root = run(tmp_path / "always", {"hygiene_always": True})
    assert summary.kept == 0 and summary.dropped == {"hygiene": 60}
    m = gen.read_manifest(root)
    assert m["hygiene_kinds"] and sum(m["hygiene_kinds"].values()) >= 60
    rows = gen.Work(root).read("outcomes")
    assert all(set(r["findings"]) == {"first", "retry"} for r in rows)
    text = (root / "work" / "outcomes.jsonl").read_text()
    assert "555" not in text and "Call" not in text  # kinds, never the email's text
    lines = "\n".join(rep.report(root, []))
    assert "hygiene drops by finding kind:" in lines


def test_markup_is_dropped_and_replaced_from_the_seed_pool(tmp_path: Path) -> None:
    summary, root = run(tmp_path, {"html": True})
    assert summary.kept == 0 and summary.state == "short"
    # each bucket tried 3 x its count (10 scam x 3 + 5 ordinary x 6): when a bucket's seeds run
    # out while it is short, the group asks for more (OD-543)
    assert summary.dropped == {"HTML markup": 60}
    seed_calls = [c for c in calls(tmp_path) if c["seeds"]]
    assert len(seed_calls) == 30  # one first call and one top-up per group
    assert gen.read_manifest(root)["selected"]  # groups with over a third dropped


def test_a_usage_limit_stops_the_run_and_resume_finishes_it(tmp_path: Path) -> None:
    summary, root = run(tmp_path, {"limit": True})
    assert summary.state == "stopped" and "--resume" in summary.detail
    assert gen.read_manifest(root)["state"] == "stopped"
    assert genset.is_generated(root)  # never the gate's, even half made
    with pytest.raises(InvalidInputError, match="--resume"):
        gen.verify(root)
    summary, root = run(tmp_path, {}, resume=True)
    assert summary.state == "complete" and summary.kept == 20
    assert len(load_cards(root / "cases")) == 20


def test_a_tool_event_fails_the_call_and_three_stop_the_run(tmp_path: Path) -> None:
    summary, _root = run(tmp_path, {"tool_event": True})
    assert summary.state == "stopped" and "tool event" in summary.detail
    assert len(calls(tmp_path)) == 3


def test_an_edited_set_is_refused(tmp_path: Path) -> None:
    _summary, root = run(tmp_path)
    eml = next((root / "eml").glob("*.eml"))
    eml.write_bytes(eml.read_bytes() + b"\nedited\n")
    with pytest.raises(InvalidInputError, match="changed since it was generated"):
        gen.verify(root)


# The checks


def test_markup_brands_and_near_duplicates() -> None:
    assert gencheck.markup(["Hello <b>there</b>"]) == "HTML markup"
    assert gencheck.markup(["see javascript:alert(1)"]) == "script, form or handler"
    assert gencheck.markup(["a < b and c > d", "Sam <sam@vendor-a.example>"]) is None
    assert gencheck.named_brand("PayPal Billing") and gencheck.named_brand("IRS Notice")
    assert not gencheck.named_brand("Vendor A Billing")
    d = gencheck.NearDuplicates()
    body = "Dear team,\n" + " ".join(f"word{i}" for i in range(40))
    assert d.seen(body) is None
    assert d.seen("Hello,\n" + " ".join(f"word{i}" for i in range(38))) == "overlapping text"
    assert d.seen("Dear team,\n" + " ".join(f"word{i}" for i in range(40))) == "identical text"


def test_a_shared_greeting_or_stock_sentence_is_not_a_duplicate() -> None:
    """The DEV run of 2026-10-20 lost 20 of 46 emails to a shared first line (OD-543)."""
    d = gencheck.NearDuplicates()
    stock = "Dear Accounts Payable Team,\nI hope this message finds you well.\n"
    one = stock + " ".join(f"alpha{i}" for i in range(40))
    two = stock + " ".join(f"beta{i}" for i in range(40))
    assert d.seen(one) is None and d.seen(two) is None
    assert d.repeated_openings() == 1  # counted, not dropped


def test_short_emails_sharing_a_sentence_are_not_overlap_drops() -> None:
    d = gencheck.NearDuplicates()
    footer = "please see the attached invoice for payment today"
    assert d.seen(footer + " " + " ".join(f"long{i}" for i in range(30))) is None
    assert d.seen("Thanks Pat. " + footer) is None  # under MIN_SHINGLES: identical text only
    assert d.seen("Thanks Pat. " + footer) == "identical text"


def test_words_keep_non_ascii_letters() -> None:
    assert gencheck.opening("Señor García,\nhola") == "señor garcía"


def test_crypto_wallet_shapes_fail_the_hygiene_scan() -> None:
    kinds = {f.kind for f in scan_text("send to 0x52908400098527886E0F7030069857D2E4169EE7", "b")}
    assert "crypto wallet address" in kinds
    assert not scan_text("invoice INV-1001 is due", "b")
