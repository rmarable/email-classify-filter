"""What an import carries over and how it changes on the way in (SPEC §11.9; OD-356 to OD-358;
V1.5 step 9b: the preview; step 9c applies the same rules).

**Settings** are allow-listed (stricter than a drop list; operator decision 2026-10-03, OD-358):
your configuration carries over (`org_domains`, the `config apply` sections, `export_schedule`,
the `settings set` keys, `log_retention_days`, and alert routes with `email` removed, since alert
email itself doesn't carry over). Everything else in `settings` is the source computer's state
(install identity, Slack identity, backup key and folder, alert email, the secret store's
interpreter, marks and timestamps) and is dropped; the target keeps its own.

**Rows**: addresses arrive paused, at stage `assist` at most, outbound off. Slack routes are
dropped (reconnect Slack). Items waiting for a decision (`awaiting_approval`, `awaiting_stepup`,
`approved`, `delayed`) go back to `awaiting_approval` to be re-posted for a fresh decision (one
with no actions to approve becomes `needs_human`); items
`executing` or `undoing` become `failed_unknown`, since they may have run on the other computer.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, cast

from ecf_server import config, retention, settings
from ecf_server.bundle_reader import Parsed

REPOST = ("awaiting_approval", "awaiting_stepup", "approved", "delayed")
UNKNOWN = ("executing", "undoing")
STAGE_CAP = "assist"
STAGES_ABOVE_CAP = ("live",)
ROUTE_CLASSES = ("mail", "system", "operator", "slack")


def setting_kept(key: str) -> bool:
    return (
        key in config.KEY.values()
        or key in settings.KEYS
        or key in (retention.KEY, "alerts.routes")
        or key in {f"alerts.{c}.routes" for c in ROUTE_CLASSES}
    )


def kept_setting_value(key: str, value: str) -> str:
    """Alert routes lose `email` (alert email doesn't carry over); others are as they were."""
    if key.startswith("alerts.") and key.endswith("routes"):
        routes = [r for r in json.loads(value) if r != "email"] or ["slack"]
        return json.dumps(routes)
    return value


def preview(conn: sqlite3.Connection, parsed: Parsed) -> dict[str, Any]:
    """`ecf import --dry-run` (OD-356): what would come in, and what wouldn't."""
    t = parsed.tables
    items = t.get("items", [])
    statuses = [str(r.get("status")) for r in items]
    repost = [r for r in items if r.get("status") in REPOST and _has_actions(r)]
    kept = [r for r in t.get("settings", []) if setting_kept(str(r.get("key")))]
    m = parsed.manifest
    ext = next((r.get("value") for r in t.get("settings", [])
                if r.get("key") == config.KEY["schema"]), None)  # fmt: skip
    return {
        "source": {
            k: m.get(k)
            for k in (
                "install",
                "install_id",
                "generation",
                "kind",
                "created_at",
                "seq",
                "product_version",
                "schema_version",
                "data_format",
                "creator",
            )
        },
        "signer": parsed.signer,
        "counts": {name: len(rows) for name, rows in sorted(t.items())},
        "addresses": [
            {
                "address_id": a.get("address_id"),
                "email": a.get("email"),
                "stage": STAGE_CAP if a.get("stage") in STAGES_ABOVE_CAP else a.get("stage"),
            }
            for a in t.get("addresses", [])
            if a.get("removed_at") is None
        ],
        "reposted": len(repost),
        "needs_human": sum(s in REPOST for s in statuses) - len(repost),
        "failed_unknown": sum(s in UNKNOWN for s in statuses),
        "settings_kept": len(kept),
        "settings_dropped": len(t.get("settings", [])) - len(kept),
        "routes_dropped": len(t.get("routes", [])),
        "target_empty": target_empty(conn),
        # the bundle's own schema fields, left out (P11): named, with why
        "schema_dropped": None if ext is None else schema_dropped(t, str(ext)),
        "not_carried": [
            "app passwords, Slack tokens and the models API key: enter them again",
            "the Slack connection: reconnect Slack",
            "the backup key and backup folder: set them here",
            "alert email: turn it on again with ecf alerts email set",
            "approvals in progress: re-posted for a fresh decision",
        ],
    }


def schema_dropped(tables: dict[str, list[dict[str, Any]]], ext: str) -> dict[str, Any]:
    """The preview's note on a bundle's own schema fields (`importer.schema_note`'s)."""
    from ecf_server import importer  # noqa: PLC0415 - importer imports this module

    found = next((r.get("value") for r in tables.get("settings", [])
                  if r.get("key") == config.KEY["rules"]), None)  # fmt: skip
    doc: Any = json.loads(str(found)) if found is not None else None
    parsed: Any = json.loads(ext)
    return importer.schema_note(parsed, importer.rules_on_fields(doc, parsed))


def _has_actions(item: dict[str, Any]) -> bool:
    """An approval without actions can't be re-posted; it arrives as `needs_human`."""
    try:
        doc: Any = json.loads(str(item.get("proposal") or "{}"))
    except ValueError:
        return False
    return isinstance(doc, dict) and bool(cast("dict[str, Any]", doc).get("actions"))


def target_empty(conn: sqlite3.Connection) -> bool:
    """OD-357: no addresses (removed ones count) and no items."""
    a = conn.execute("SELECT count(*) FROM addresses").fetchone()[0]
    i = conn.execute("SELECT count(*) FROM items").fetchone()[0]
    return a == 0 and i == 0
