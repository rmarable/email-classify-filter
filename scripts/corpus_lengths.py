"""How long a corpus's emails are, as counts only (OD-553; classifier window tuning, step 1).

Opens a corpus session on a running install, reads each message's `prepared_chars` (the length
of the redacted text the excerpts are cut from, recorded at fetch since OD-553) and prints the
share in each band and the deciles. It prints no message text. A corpus fetched before OD-553
has no length; those messages are counted as unknown, never as 0.

Run in a terminal (the passphrase is read without echo):

  uv run python scripts/corpus_lengths.py --install accept ~/ecf-corpus/<name>.ecfcorpus
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

from ecf.client import LocalClient
from ecf.errors import EcfError
from ecf.paths import paths_for
from ecf.prompts import hidden

BANDS = ((1500, "up to 1,500"), (4000, "1,501 to 4,000"), (8000, "4,001 to 8,000"))


def summary(lengths: list[int | None]) -> list[str]:
    """The report's lines for these lengths (None: unknown)."""
    known = sorted(x for x in lengths if x is not None)
    lines = [f"messages: {len(lengths)} ({len(known)} with a length;"
             f" {len(lengths) - len(known)} from an older corpus, unknown)"]  # fmt: skip
    if not known:
        return lines
    low = -1  # an email with no text (attachments only) has length 0: the first band
    for high, name in BANDS:
        n = sum(low < x <= high for x in known)
        lines.append(f"  {name}: {n} ({100 * n / len(known):.0f}%)")
        low = high
    n = sum(x > low for x in known)
    lines.append(f"  over 8,000: {n} ({100 * n / len(known):.0f}%)")
    if len(known) >= 2:
        deciles = statistics.quantiles(known, n=10, method="inclusive")  # inside the data
        lines.append("  deciles: " + ", ".join(f"{int(x):,}" for x in deciles))
    lines.append(f"  median: {int(statistics.median(known)):,}")
    return lines


def main() -> int:
    ap = argparse.ArgumentParser(description="How long a corpus's emails are (counts only).")
    ap.add_argument("--install", default="default")
    ap.add_argument("corpus")
    args = ap.parse_args()
    try:  # no terminal, nothing entered, the service down, a wrong passphrase: one line, no trace
        lengths = _lengths(args.install, args.corpus)
    except (EcfError, OSError) as exc:
        print(f"corpus_lengths: {exc}", file=sys.stderr)
        return 2
    print("\n".join(summary(lengths)))
    return 0


def _lengths(install: str, corpus: str) -> list[int | None]:
    secret = hidden(f"Passphrase for {Path(corpus).name} (hidden): ")
    lengths: list[int | None] = []
    with LocalClient(paths_for(install)) as c:
        opened = c.request("POST", "/v1/corpus/session", {"path": corpus, "passphrase": secret},
                           timeout=600)  # fmt: skip
        del secret
        sid = str(opened["session_id"])
        try:
            for k in c.request("GET", f"/v1/corpus/session/{sid}/keys")["keys"]:
                item = c.request("GET", f"/v1/corpus/session/{sid}/items/{k['index']}")
                value = item.get("prepared_chars")
                lengths.append(None if value is None else int(value))
        finally:
            c.request("DELETE", f"/v1/corpus/session/{sid}")
    return lengths


if __name__ == "__main__":
    sys.exit(main())
