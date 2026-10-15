"""Recording a classification (SPEC §5.1 step 5, §7; V1.3 step 3): `store`, which Claude's
classifications go through (`claude_review`). The local classifier (Gemma 4 12B through Ollama),
its prompt and its prompt digest were removed in v3.0.0 (ADR 0031; R-D14 keeps the module name);
the classifier's prompt digest is Claude's (`claude_review.prompt_digest`).

- **Recorded:** the classification, the pinned models and the pin key (`pinned_models`, which the
  go-live gate binds to) and a `batch_id`; then the item moves to `classified` and the policy
  applies.
- **Reprocessing key** (OD-507 §4.1; P14-P16): `pinned_models` also holds `provider`, `prompt`
  (the full digest of the provider's own prompt function) and `rules` (`rules.composed_digest`
  of the rules in force), beside `classifier` and `schema`; `ecf reprocess` compares them.
- **`item.classified`** (OD-507 §5, D12): one audit row per stored classification: each schema
  field's value (enum, level or boolean; never text), the model and 16-hex digest prefixes.
  Never pruned, like the rest of the audit log; the values outlive the item (SPEC §6.5).

- **Preset D** (v3.0.0 step 4; OD-509): TypeSafe Jev's drain (`jev_dispatch`) stores with
  `provider="jev"`. Its UPDATE also requires `classification IS NULL` (P3): a store that touched
  no row (classified meanwhile) writes no audit row and moves nothing. Jev's prompt digest is the
  one `jev_dispatch` stored (`jev_keys.stored`): this module never imports `jev`.

Model output can raise risk but never lower it: what the classification may do is decided by the
rules and policy (V1.3 step 4), never here.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ecf.errors import ConflictError
from ecf.ids import StableId
from ecf.schema import CompiledSchema, FieldKind
from ecf_server import claude_pins, config, decide, items, jev_keys
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
    provider: str = "claude",
    context: TransitionContext | None = None,
    jev: dict[str, Any] | None = None,
) -> bool:
    """Record a classification and move the item to `classified`, then apply the policy
    (Claude's from `awaiting_claude`, V1.4 step 3).
    `schema_digest` is the schema the model was asked with: when the effective schema changed
    since (`ecf config apply` during the call), nothing is recorded and False is returned, so the
    item is asked again with the new one (OD-478); Jev's guarded store over a classification also
    writes nothing and returns False. `context` is the transition's (a reprocessed
    item's, OD-507 §4.3). `jev`: Jev's `facts.jev` (unsure and below-bar fields, probabilities,
    confidence; `jev_facts.record`), stored beside the classification in the same write; any
    other store removes an earlier one, so Claude's classification is never read with Jev's
    caution (OD-511)."""
    aid = conn.execute("SELECT address_id FROM items WHERE stable_id = ?",
                       (stable_id,)).fetchone()["address_id"]  # fmt: skip
    # digests read before the write lock (P27); recomputed in it only if the schema moved
    known = config.current_schema(conn)
    keys = _keys(conn, known, provider)
    with write_tx(conn):  # read and record in one transaction: the schema can't move between
        schema = config.current_schema(conn)
        if schema_digest is not None and schema_digest != schema.digest:
            log.info("classifier.schema_changed", stable_id=stable_id[:8])
            return False
        if schema.digest != known.digest:
            keys = _keys(conn, schema, provider)
        pinned = (
            {
                "schema": schema.digest,  # was the integer 1 before v2 (S16)
                "pin_key": claude_pins.address_key(conn, aid),
            }
            | keys
            | models
        )  # the gate's key (V1.4 step 2); the reprocessing key (OD-507)
        # Jev's drain stores only over no classification (P3): never twice
        guard = " AND classification IS NULL" if provider == "jev" else ""
        facts = (
            "json_set(coalesce(facts, '{}'), '$.jev', json(?))" if jev is not None
            else "CASE WHEN ? IS NULL AND json_extract(facts, '$.jev') IS NOT NULL"
                 " THEN json_remove(facts, '$.jev') ELSE facts END"
        )  # fmt: skip
        cur = conn.execute(
            "UPDATE items SET classification = ?, pinned_models = ?, batch_id = ?, updated_at = ?,"  # noqa: S608
            f" schema_version = ?, facts = {facts} WHERE stable_id = ? AND status = ?{guard}",
            (
                json.dumps(classification, sort_keys=True),
                json.dumps(pinned, sort_keys=True),
                batch_id or f"single:{stable_id[:16]}",
                to_ts(clock.now()),
                schema.version,
                json.dumps(jev, sort_keys=True) if jev is not None else None,
                stable_id,
                expected.value,
            ),
        )
        if guard and cur.rowcount != 1:  # nothing written: the caller's claim isn't done
            log.info("classifier.already_classified", stable_id=stable_id[:8])
            return False
        conn.execute(
            "INSERT INTO audit (ts, address_id, stable_id, event, actor, outcome, data)"
            " VALUES (?, ?, ?, 'item.classified', ?, 'ok', ?)",
            (to_ts(clock.now()), aid, stable_id, actor,
             json.dumps(classified_row(classification, schema, pinned), sort_keys=True)),
        )  # fmt: skip
    try:
        items.transition(conn, clock, StableId(stable_id), Status.CLASSIFIED,
                         context or TransitionContext(), actor=actor,
                         expected=expected)  # fmt: skip
    except ConflictError:
        log.info("classifier.item_moved_on", stable_id=stable_id[:8])  # resolved meanwhile
        return True
    try:
        decide.apply(conn, clock, stable_id)
    except Exception as exc:  # the tick's sweep tries again; the classification is kept
        log.error("policy.apply_failed", stable_id=stable_id[:8], error_type=type(exc).__name__)
    return True


_PROMPTS: dict[tuple[str, str], str] = {}  # (provider, schema digest) -> its prompt digest


def prompt_digest(schema: CompiledSchema, provider: str) -> str | None:
    """The full digest of `provider`'s own prompt function (P15): Claude's for C (Jev's, for D, is
    the stored one: `jev_prompt`); another provider records none. Kept per schema digest (the
    prompt is a function of the schema and this release)."""
    if provider != "claude":
        return None
    key = (provider, schema.digest)
    if key not in _PROMPTS:
        from ecf_server import claude_review  # noqa: PLC0415 - claude_review imports this module

        if len(_PROMPTS) >= 32:
            _PROMPTS.clear()
        _PROMPTS[key] = claude_review.prompt_digest(schema)
    return _PROMPTS[key]


def _keys(conn: sqlite3.Connection, schema: CompiledSchema, provider: str) -> dict[str, Any]:
    """The reprocessing key's new parts (module notes)."""
    out: dict[str, Any] = {"provider": provider, "rules": config.current_rules(conn).digest}
    prompt = prompt_digest(schema, provider) if provider != "jev" else jev_prompt(conn, schema)
    if prompt is not None:
        out["prompt"] = prompt
    return out


def jev_prompt(conn: sqlite3.Connection, schema: CompiledSchema) -> str | None:
    """Jev's prompt digest for `schema`, as `jev_dispatch` stored it (`jev.keys`); None when the
    stored one was for another schema."""
    k = jev_keys.stored(conn)
    return str(k["prompt_digest"]) if k.get("schema") == schema.digest and k.get(
        "prompt_digest") else None  # fmt: skip


def classified_row(classification: dict[str, Any], schema: CompiledSchema,
                   pinned: dict[str, Any]) -> dict[str, Any]:  # fmt: skip
    """`item.classified`'s data (D12, P20): each schema field's value when it is one of the
    field's own values or a boolean, so no text a model wrote can reach the audit log; the model
    and 16-hex prefixes of the digests."""
    fields: dict[str, Any] = {}
    for name, spec in schema.fields.items():
        v = classification.get(name)
        if spec.kind is FieldKind.BOOLEAN:
            if isinstance(v, bool):
                fields[name] = v
        elif isinstance(v, str) and v in spec.values:
            fields[name] = v
    digests = {k: str(pinned[k]).removeprefix("schema-")[:16]
               for k in ("schema", "prompt", "rules") if pinned.get(k)}  # fmt: skip
    return {"fields": fields, "model": pinned.get("classifier"),
            "provider": pinned.get("provider"), "digests": digests}  # fmt: skip
