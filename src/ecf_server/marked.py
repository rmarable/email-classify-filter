"""The scan and reprocess marker (OD-507, plan v3.0.0-STEP5 §2): one per-item mark drives every
special case of old mail. `facts.backfill = 1` marks mail an `ecf backfill` read (with
`facts.scan_run`, the scan's run id, from v3.0.0); `facts.reprocess = <run id>` marks an item
`ecf reprocess` reclassified or planned again. Never a time window or an address flag: new mail
read during a scan behaves exactly as without one (SPEC §5.4, immediate escalation).

Marked items: no Slack post or card of their own (the run's summary instead), every hide needs
approval, not in "Approve all", digests, the Claude backlog switch, reminders or the overdue
alert; `/ecf-review` hands them out only when no new mail waits; they don't count toward sender
history.
"""

from __future__ import annotations

from typing import Any


def sql(alias: str = "") -> str:
    """An SQL term true for a marked item (`alias`: the items table's, e.g. `i.`)."""
    return (f"(coalesce(json_extract({alias}facts, '$.backfill'), 0) = 1"
            f" OR json_extract({alias}facts, '$.reprocess') IS NOT NULL)")  # fmt: skip


def is_marked(facts: dict[str, Any]) -> bool:
    return facts.get("backfill") == 1 or facts.get("reprocess") is not None


def run_of(facts: dict[str, Any]) -> str | None:
    """The run that marked the item: a reprocess run, else the scan's (None for a backfill from
    before v3.0.0)."""
    run = facts.get("reprocess") or facts.get("scan_run")
    return str(run) if run else None


def head_key(run: str | None) -> str:
    """The Slack key of a run's summary post (`scans.py`); a pre-v3.0.0 backfill has none."""
    return f"scan:{run or '-'}"
