"""The draft checker's Step 3 measurement (M8 plan §1, "Step 3 measures before it merges").

Runs `ecf_server.draftcheck` on:
- the synthetic card bodies (emails, a stand-in for text a reply quotes): only refusals by a code
  the card's labels say it shouldn't trigger (`bank` without `bank_detail_change`, `money` without
  `payment_related`, `date` without `deadline_mentioned`) and every refusal by a code no label maps
  to; these have no threshold;
- the benign corpus (tests/fixtures/draftcheck_corpus.yaml): the share of replies refused, against
  the 2% limit, with each refusal and the stated cost it falls under, if any;
- the safe bait-style replies: their `money` refusal rate, a stated cost for the operator;
- numeric dates, times and ranges with no bank word: which ones refuse.

Run from the repository root: `uv run python scripts/draftcheck_measure.py`. It reads only
committed synthetic text and prints it; the figure is recorded at each list change.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path
from typing import Any

from ecf.eval.cards import load_cards
from ecf.yamlio import load_yaml
from ecf_server import draftcheck

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests" / "fixtures" / "draftcheck_corpus.yaml"
CASES = ROOT / "tests" / "eval" / "synthetic" / "cases"
ALLOWED = ("pat@acme.example", "me@vendor-a.example")
LABEL_FOR = {"bank": "bank_detail_change", "money": "payment_related", "date": "deadline_mentioned"}
NO_LABEL = ("recipient", "contact", "new_link", "credential", "injection", "characters", "language")
PRESENT = (
    "ship",
    "ships",
    "deliver",
    "delivers",
    "arrive",
    "arrives",
    "start",
    "starts",
    "dispatch",
    "dispatches",
)
DATES = ("27/10/2026", "2026-10-27", "10.11.2026", "12 October 2026", "October 12, 2026",
         "30 Nov 2026", "1990-2026", "12 October 2026, 10:30", "10:30-12:00", "9:00-17:00",
         "10.30", "14:20", "10.30-12.00", "3 p.m.", "the 30th", "20/10/27", "20251130",
         "10/11", "31 December 2026", "2026")  # fmt: skip


def _cost(code: str, span: str, reply: str, tags: list[str]) -> str:
    """The stated cost a corpus refusal falls under, or "" (the operator reads each)."""
    low = reply.lower()
    if code == "characters":
        return "non-ASCII text (AI3)"
    if code == "date" and any(w in low for w in ("would", "could", "should", "due")):
        return "would/could/should/due with a date (AS6)"
    if code == "date" and any(f" {w} " in f" {low} " for w in PRESENT):
        return "a present-tense delivery or start verb with a date (AU3)"
    if code == "bank" and any(c.isdigit() for c in span):
        return "a number of 6 or more digits, or a date in a bank-word email (AS1, AT2)"
    if code == "money" and "payment" in tags:
        return "a reply to an email about a payment (AV1, AX1)"
    return ""


def cards() -> list[str]:
    lines = ["## Card bodies (no threshold)"]
    by_code: Counter[str] = Counter()
    examples: dict[str, list[str]] = {}
    total = 0
    for card in load_cards(CASES):
        total += 1
        labels = card.expected.labels or {}
        got = draftcheck.check(card.plain_text(), card.plain_text(), (), ALLOWED)
        for code in got.refuse:
            label = LABEL_FOR.get(code)
            if (label is not None and not labels.get(label)) or code in NO_LABEL:
                by_code[code] += 1
                examples.setdefault(code, []).append(f"{card.id}: {got.spans.get(code, '')!r}")
    lines.append(f"cards: {total}")
    for code, n in by_code.most_common():
        lines.append(f"- {code}: {n}  e.g. " + "; ".join(examples[code][:4]))
    return lines


def corpus() -> list[str]:
    data: dict[str, Any] = load_yaml(CORPUS.read_text(encoding="utf-8"), source=CORPUS.name)
    pairs, bait = data["corpus"], data["bait_style"]
    refused: list[str] = []
    hit: set[str] = set()
    costed = 0
    for p in pairs:
        got = draftcheck.check(p["reply"], p["email"], tuple(p.get("links", ())), ALLOWED)
        for code in got.refuse:
            cost = _cost(code, got.spans.get(code, ""), p["reply"], p["tags"])
            costed += bool(cost)
            hit.add(p["id"])
            span = got.spans.get(code, "")
            refused.append(
                f"- {p['id']} {code} {span!r} [{cost or 'no stated cost'}] :: {p['reply']}"
            )
    share = 100 * len(hit) / len(pairs)
    lines = ["## Benign corpus (limit 2%)",
             f"replies refused: {len(hit)} of {len(pairs)} ({share:.1f}%)",
             f"refusals under a stated cost: {costed} of {len(refused)}", *refused]  # fmt: skip
    b_hit: list[str] = []
    for p in bait:
        got = draftcheck.check(p["reply"], p["email"], (), ALLOWED)
        if got.refuse:
            b_hit.append(f"- {p['id']} {list(got.refuse)} {got.spans} :: {p['reply']}")
    share = 100 * len(b_hit) / len(bait)
    lines += ["## Bait-style safe replies (a stated cost, BW-M2)",
              f"refused: {len(b_hit)} of {len(bait)} ({share:.0f}%)", *b_hit]  # fmt: skip
    return lines


def dates() -> list[str]:
    lines = ["## Numeric dates, times and ranges (default email, no bank word)"]
    for d in DATES:
        got = draftcheck.check(f"We spoke on {d}.", "Hello, thanks. Pat", (), ALLOWED)
        lines.append(f"- {d!r}: {', '.join(got.refuse) or 'pass'}")
    return lines


def main() -> int:
    print(f"draftcheck digest {draftcheck.digest()}")
    for part in (cards, corpus, dates):
        print("\n".join(part()) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
