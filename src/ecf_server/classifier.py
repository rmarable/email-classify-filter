"""Recording a classification (SPEC §5.1 step 5, §7; V1.3 step 3): `store`, which Claude's
classifications go through (`claude_review`). The local classifier (Gemma 4 12B through Ollama),
its prompt and its prompt digest were removed in v3.0.0 (ADR 0031; R-D14 keeps the module name);
the classifier's prompt digest is Claude's (`claude_review.prompt_digest`).

- **Recorded:** the classification, the pinned models and the pin key (`pinned_models`, which the
  go-live gate binds to) and a `batch_id`; then the item moves to `classified` and the policy
  applies.

Model output can raise risk but never lower it: what the classification may do is decided by the
rules and policy (V1.3 step 4), never here.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ecf.errors import ConflictError
from ecf.ids import StableId
from ecf_server import claude_pins, config, decide, items
from ecf_server.clock import Clock, to_ts
from ecf_server.db import write_tx
from ecf_server.log_bridge import log
from ecf_server.state_machine import Status, TransitionContext


def store(  # noqa: PLR0913 - the record, then its state, batch, actor and schema
    conn: sqlite3.Connection,
    clock: Clock,
    stable_id: str,
    classification: dict[str, Any],
    models: dict[str, str],
    *,
    expected: Status = Status.NEW,
    batch_id: str | None = None,
    actor: str = "classifier",
    schema_digest: str | None = None,
) -> bool:
    """Record a classification and move the item to `classified`, then apply the policy
    (Claude's from `awaiting_claude`, V1.4 step 3).
    `schema_digest` is the schema the model was asked with: when the effective schema changed
    since (`ecf config apply` during the call), nothing is recorded and False is returned, so the
    item is asked again with the new one (OD-478)."""
    aid = conn.execute("SELECT address_id FROM items WHERE stable_id = ?",
                       (stable_id,)).fetchone()["address_id"]  # fmt: skip
    with write_tx(conn):  # read and record in one transaction: the schema can't move between
        schema = config.current_schema(conn)
        if schema_digest is not None and schema_digest != schema.digest:
            log.info("classifier.schema_changed", stable_id=stable_id[:8])
            return False
        pinned = {
            "schema": schema.digest,  # was the integer 1 before v2 (S16)
            "pin_key": claude_pins.address_key(conn, aid),
        } | models  # the gate's key (V1.4 step 2)
        conn.execute(
            "UPDATE items SET classification = ?, pinned_models = ?, batch_id = ?, updated_at = ?,"
            " schema_version = ? WHERE stable_id = ? AND status = ?",
            (
                json.dumps(classification, sort_keys=True),
                json.dumps(pinned, sort_keys=True),
                batch_id or f"single:{stable_id[:16]}",
                to_ts(clock.now()),
                schema.version,
                stable_id,
                expected.value,
            ),
        )
    try:
        items.transition(conn, clock, StableId(stable_id), Status.CLASSIFIED, TransitionContext(),
                         actor=actor, expected=expected)  # fmt: skip
    except ConflictError:
        log.info("classifier.item_moved_on", stable_id=stable_id[:8])  # resolved meanwhile
        return True
    try:
        decide.apply(conn, clock, stable_id)
    except Exception as exc:  # the tick's sweep tries again; the classification is kept
        log.error("policy.apply_failed", stable_id=stable_id[:8], error_type=type(exc).__name__)
    return True
