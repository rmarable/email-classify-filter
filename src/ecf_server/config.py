"""`ecf config apply` (SPEC §9.7; V1.2 step 10b): security-relevant configuration.

One YAML document, `version: 1`, with optional sections; an omitted section is left unchanged,
and a section set to `default` returns to its shipped value (OD-225; `org_domains` has none).
Sections: `org_domains`, `forward_allow_list`, `move_folders`, `action_policy`, `rules` and
`templates`, from V1.5 `export_schedule` (`daily`, `weekly`, `off`; OD-343), and from V1.6
`org_addresses` (exact addresses with optional names: the internal set beside `org_domains`,
OD-431, OD-433; ADR 0021), and from v2.0.0 `schema` (the install's schema extension, OD-478;
ADR 0025). `org_domains` may be empty only when no watched address is at a
non-public domain (OD-441). A forward target is in `org_domains` or exactly in `org_addresses`
(OD-437); one at a public provider is named in the step-up dialog. Alert routes
change with `ecf alerts set` and alert email with `ecf alerts email set`. Unknown keys are
refused.

Every change needs step-up. The step-up target is the whole canonical document: the service
recomputes the diff and the dialog text from it, and the bound hash covers the document and the
configuration it replaces, so the dialog can't describe one change while another is applied, and a
change made in between voids the nonce. `security_config_delay_minutes` is 0 in local mode
(OD-074), so the change applies at once; it is audited (`config.applied`) and announced as a
Security Notice.

`schema` (your own fields, the user layer: v3.0.0 S4, OD-502; SPEC §7.1, §9.7) is validated
before `rules`: the rules in force (or the starter rules) must compile against the new effective
schema, so removing a field or value a rule uses is refused, naming the rule (the OD-452
pattern). Your fields are for preset C only (D8): a `schema` section other than `default` is
refused while any address is preset A or B (and `addresses.add_address` refuses an A or B
address while one is stored). An added value can't take a built-in label's name or the id of a
fixed, default or applied rule, always (P2); a rule that labels from your field is refused when
the field's values repeat another field's value or a label name (P12). Over a cap is refused with
every violation listed (`schema_limit`); the dry run carries the budget line (with the bytes per
classify read, P16), the prompt lines the extension adds, the warnings (`extension_warnings`),
the privacy line (§12.4, D10) and how the rules layer.

The user layer is behind `ecf.schema.EXTENSIONS_ENABLED` (OD-481, kept by OD-502 until M2
passes): off, a `schema` section other than `default` is refused, `schema: default` still
removes a stored extension, and a stored one is ignored (the effective schema is the shipped
one). Import and restore drop a bundle's `schema` (`importer.drop_schema`, P11): it needs a
re-apply here. A stored extension a new version first uses is activated once at service start
(`activate_stored`: audited, a Security Notice, live C addresses to assist; D11). Applied rules
that don't compile against this version (a field or fact a later release removed, or a fixed
rule's id) give way to the starter rules, or, if those fail too, to ecf's fixed rules and
`otherwise`, with a System Error until they are changed (`current_rules`, `schema_tick`;
classifier plan item 6); `current_rules` never raises (P2, C11).

`rules` is layered (v3.0.0 S4, D2; `rules.compose`): ecf's fixed rules always run first, then
your rules with new ids, then ecf's default rules with each one your file replaces by id at that
default's position and each one in `default_rules_off` left out; a file ending in a rule with no
`when` replaces the default rules instead. Apply, the rules-in-force check, `rules_problem`, `ecf
rules test` and the dry run check the composed set, never your file alone (P2, P3). Applied rules
from v2 hold copies of the fixed rules: `convert_rules` strips the unchanged copies once, at
service start and on import (P7), and with them each copy identical to a shipped default rule
when that leaves the composed set unchanged (P24). A stored file without a catch-all from before
S4 changes meaning (its rules now run before the default rules, which before didn't run): the dry
run of any `ecf config apply` and `ecf doctor` say so until rules are applied again
(`PRE_LAYERS`).

What reads each section in V1.2: `org_domains` (facts and triggers, §7.2, §8.5). The others are
validated and stored for their consumers: rules and the action policy (V1.3, with the classifier),
the move-folder allow-list (V1.3), the forward allow-list and templates (V1.5, with sending).
"""

from __future__ import annotations

import contextlib
import functools
import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from typing import Any, cast

from ecf.errors import InvalidInputError, SchemaLimitError
from ecf.schema import (
    EXTENSIONS_OFF,
    CompiledSchema,
    FieldKind,
    extend_schema,
    extension_budget,
    extension_text,
    extension_warnings,
    extensions_enabled,
    load_schema,
    shipped_fallback,
)
from ecf.yamlio import load_yaml
from ecf_server import (
    addresses,
    health,
    internal,
    policy,
    rules,
    slack_admin,
    stepup,
    templates,
)
from ecf_server.clock import Clock, to_ts
from ecf_server.db import write_tx
from ecf_server.facts import PUBLIC_DOMAINS
from ecf_server.log_bridge import log
from ecf_server.notify import Notifier

MAX_BYTES = 48 * 1024  # inside the API's 64 KB request limit
MAX_ENTRIES = 50
SECTIONS = ("org_domains", "org_addresses", "forward_allow_list", "move_folders", "action_policy",
            "schema", "rules", "templates", "export_schedule")  # fmt: skip
LATER = {
    "alerts": "change alert routes with `ecf alerts set`, alert email with `ecf alerts email set`",
    "export_dir": "change it with `ecf export dir set <path>` (step-up)",
}
KEY = {s: f"config.{s}" for s in SECTIONS} | {"org_domains": addresses.ORG_DOMAINS_KEY,
                                              "export_schedule": "export_schedule"}  # fmt: skip
EXPORT_SCHEDULES = ("daily", "weekly", "off")  # scheduled_export.py reads it (V1.5 step 8b)
POLICY_CHOICES = ("auto", "approve")
DEFAULT_POLICY = dict.fromkeys(sorted(rules.HIDE_ACTIONS), "auto")  # §8.3, `standard` column
_ENTRY_ID = re.compile(r"^[a-z0-9_]{1,40}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_POLICY_SHAPE = "{standard: {action: auto|approve}}"
MAX_ADDRESS = 254
MAX_NAME = 100
DEFAULT = "default"  # `<section>: default` returns the section to its shipped value (OD-225)
SCHEMA_ALERT = "schema_extension"  # System Error: the stored extension no longer compiles
# leads the step-up dialog and the Security Notice whenever `schema` changes (OD-478); `_lead`
# adds what live needs again (P15)
SCHEMA_LEAD = "changes the classifier prompt; live addresses go back to assist"
# D8: why your fields are for preset C only, in each refusal
LOCAL_REASON = ("the local model (presets A and B) isn't measured with your own fields (SPEC"
                " §1.5a item 2)")  # fmt: skip
PRIVACY_LINE = ("privacy: your field and value names and descriptions go to Anthropic with each"
                " Claude classification request, and the chosen values with each actor request"
                " (preset C); never put email content or secrets in them")  # fmt: skip
ACTIVATED = "schema.activated"  # setting: the SHA-256 of the stored extension in use (D11)
PRE_LAYERS = "rules.pre_layers"  # setting: a pre-S4 stored file without a catch-all (P3)
LAYERS_CHECKED = "rules.layers_checked"  # setting: the first S4 start looked (P3)


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def ordered_json(value: Any) -> str:
    """JSON in the given key order: the schema extension keeps the operator's order of fields
    and values (it is the prompt's order), so it is stored, cached and compared this way."""
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


# ---------------------------------------------------------------------------- current config


def current(conn: sqlite3.Connection) -> dict[str, Any]:
    """Each section's stored value; None where the shipped default applies."""
    out: dict[str, Any] = {}
    for s in SECTIONS:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (KEY[s],)).fetchone()
        out[s] = json.loads(row["value"]) if row else None
    return out


def _stored(conn: sqlite3.Connection, section: str) -> Any:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (KEY[section],)).fetchone()
    return json.loads(row["value"]) if row else None


def current_schema(conn: sqlite3.Connection) -> CompiledSchema:
    """The effective schema: the shipped one with the install's extension (OD-478). A stored
    extension that no longer compiles (a later release added a name it uses) gives the shipped
    schema keyed apart (`shipped_fallback`, fail closed); `schema_tick` raises a System Error.
    While extensions are off (OD-481) a stored extension is ignored: the shipped schema."""
    return _effective_schema(_stored(conn, "schema"))


def _effective_schema(ext: Any) -> CompiledSchema:
    if ext is None or not extensions_enabled():
        return load_schema()
    return _tolerant(ordered_json(ext))


@functools.lru_cache(maxsize=8)
def _tolerant(ext_json: str) -> CompiledSchema:
    try:
        return _extended(ext_json)
    except (InvalidInputError, SchemaLimitError) as exc:
        log.error("schema.extension_broken", violations=len(exc.extra.get("violations") or []))
        return shipped_fallback(ext_json)


@functools.lru_cache(maxsize=8)
def _extended(ext_json: str) -> CompiledSchema:
    """Cached on the extension in its stored order (C11); it was checked when applied."""
    return extend_schema(load_schema(), json.loads(ext_json))


def extension_problem(conn: sqlite3.Connection) -> str | None:
    """Why the stored extension no longer compiles; None when it does (or there is none, or
    extensions are off and it is ignored, OD-481)."""
    ext = _stored(conn, "schema")
    if ext is None or not extensions_enabled():
        return None
    try:
        _extended(ordered_json(ext))
    except (InvalidInputError, SchemaLimitError) as exc:
        return exc.detail
    return None


def schema_tick(conn: sqlite3.Connection, clock: Clock, notifier: Notifier) -> None:
    """Each tick: a System Error while the stored extension doesn't compile, or the applied
    rules don't compile against the effective schema; resolved after."""
    problem = extension_problem(conn)
    if problem is not None:
        health.open_alert(conn, clock, notifier, SCHEMA_ALERT, None,
                          "the schema extension no longer compiles with this version"
                          f" ({problem[:200]}); ecf classifies with the built-in schema and"
                          " live addresses are back in assist. Fix: change the extension, or"
                          " `schema: default`, with ecf config apply")  # fmt: skip
        return
    broken = rules_problem(conn)
    if broken is not None:
        health.open_alert(conn, clock, notifier, SCHEMA_ALERT, None,
                          f"the applied rules don't compile with this version"
                          f" ({broken[:200]}); ecf uses the starter rules until they change."
                          " Fix: apply rules this version accepts (no field or fact it doesn't"
                          " know, none of ecf's fixed rules), or `rules: default`, with ecf"
                          " config apply")  # fmt: skip
        return
    health.resolve_alert(conn, clock, notifier, SCHEMA_ALERT, None)


def rules_problem(conn: sqlite3.Connection) -> str | None:
    """Why the applied rules don't compile against the effective schema (they use a stored
    extension that is ignored or no longer compiles, or a field or fact this version doesn't
    know); None when they do, or none are applied."""
    stored = _stored(conn, "rules")
    if stored is None:
        return None
    changed = rules.convert_v2(stored).customised  # what stopped the v2 conversion (P7)
    if changed:
        return (f"applied rules: rule {', '.join(changed)} is a changed copy of one of ecf's"
                " fixed rules, which run first; remove it and the other fixed rules"
                f" ({', '.join(rules.fixed_ids())}) from your file")  # fmt: skip
    try:  # the composed set, as mail runs it (P2)
        rules.compose(current_schema(conn), canonical_json(stored), source="applied rules")
    except InvalidInputError as exc:
        return exc.detail
    return None


def rules_warnings(conn: sqlite3.Connection) -> list[str]:
    """What `ecf doctor` and the dry run warn about the stored rules, short of a refusal: an off
    id ecf skips (`rules.layer_warnings`; a later release may rename a default), and a pre-S4
    file without a catch-all, whose meaning changed (P3)."""
    stored = _stored(conn, "rules")
    if stored is None:
        return []
    out: list[str] = []
    try:
        user = rules.compose(current_schema(conn), canonical_json(stored), source="applied rules")
        out += [f"applied rules: {w}" for w in rules.layer_warnings(user)]
    except InvalidInputError:
        pass  # rules_problem reports it
    if slack_admin.setting(conn, PRE_LAYERS) == _rules_sha(stored):
        out.append("applied rules: your file has no catch-all; since v3.0.0 its rules run after"
                   " ecf's fixed rules and before ecf's default rules (before, the default rules"
                   " didn't run). End it with a rule with no `when` to keep the old meaning, or"
                   " apply it again (unchanged is fine) to confirm the new one")  # fmt: skip
    return out


def _rules_sha(doc: Any) -> str:
    return hashlib.sha256(canonical_json(doc).encode()).hexdigest()


def _acknowledge_layers(conn: sqlite3.Connection, doc: Any) -> None:
    """P3: applying the stored pre-S4 file again unchanged confirms its layered meaning; the note
    goes (nothing security-relevant changes, so no step-up)."""
    if slack_admin.setting(conn, PRE_LAYERS) == _rules_sha(doc):
        with write_tx(conn):
            conn.execute("DELETE FROM settings WHERE key = ?", (PRE_LAYERS,))


def mark_pre_layers(conn: sqlite3.Connection, now: str) -> None:
    """Once, at the first service start with S4's layering (P3): a stored file without a
    catch-all was written when it replaced the default rules; now they run after it. Its SHA-256
    is kept so the dry run and doctor name it until rules are applied again."""
    if slack_admin.setting(conn, LAYERS_CHECKED):
        return
    stored = _stored(conn, "rules")
    with write_tx(conn):
        if isinstance(stored, dict):
            listed: Any = cast("dict[str, Any]", stored).get("rules")
            last = cast("list[Any]", listed)[-1] if isinstance(listed, list) and listed else None
            if isinstance(last, dict) and "when" in cast("dict[str, Any]", last):
                slack_admin.put_setting(conn, PRE_LAYERS, _rules_sha(stored), now,
                                        actor="service")  # fmt: skip
                log.info("config.rules_pre_layers")
        slack_admin.put_setting(conn, LAYERS_CHECKED, now, now, actor="service")


def current_rules(conn: sqlite3.Connection) -> rules.CompiledRules:
    """The applied rules, or the starter rules when none were applied, composed against the
    effective schema. Applied rules that don't compile against it (they use a stored extension
    that is ignored, OD-481, or no longer compiles, or a field or fact this version doesn't know)
    give way to the starter rules, so mail is still decided; if those don't compile either, to
    ecf's fixed rules and `otherwise` (P2, C11: fail closed, nothing hides by a default rule).
    `schema_tick` raises a System Error until they change. Never raises."""
    schema = current_schema(conn)
    stored = _stored(conn, "rules")
    if stored is not None:
        text = canonical_json(stored)
        try:  # fixed rules first (OD-500); cached on the schema and the JSON (P8), a refusal too
            return rules.compose(schema, text, source="applied rules")
        except InvalidInputError:
            _log_once("config.rules_broken", schema, text)
    try:
        return rules.load_starter_rules(schema)
    except InvalidInputError:
        _log_once("config.starter_rules_broken", schema, "")
        return rules.fallback_rules(schema)


def _log_once(event: str, schema: CompiledSchema, text: str) -> None:
    key = (event, schema.digest, hashlib.sha256(text.encode()).hexdigest())
    if key not in _BROKEN_LOGGED:  # once per schema and rules, not per item (A4)
        if len(_BROKEN_LOGGED) >= 32:
            _BROKEN_LOGGED.clear()
        _BROKEN_LOGGED.add(key)
        log.error(event)


_BROKEN_LOGGED: set[tuple[str, str, str]] = set()


def convert_rules(conn: sqlite3.Connection, now: str, *, actor: str = "service") -> dict[str, Any]:
    """P7 (v3.0.0 S3, OD-500): applied rules copied from the v2 starter rules hold ecf's fixed
    rules, which a rules file can no longer define. Each one identical to the v2 starter text is
    stripped and the rest kept (`rules.convert_v2`), audited `config.rules_converted`. A
    customised fixed rule stops it: the stored rules stay as they are, don't compile, and the
    starter rules apply with a System Error naming the rule (`schema_tick`). Run once at service
    start after the migrations, and on an imported or restored bundle's staged copy (A2: the
    bundle isn't refused for it); idempotent. The refusal is audited once per stored document
    (outcome `denied`). A document of only unchanged fixed rules becomes `rules: default`."""
    stored = _stored(conn, "rules")
    c = rules.convert_v2(stored)
    if c.customised:
        log.error("config.rules_customised_fixed", rules=len(c.customised))
        data = json.dumps(
            {
                "customised": list(c.customised),
                "sha256": hashlib.sha256(canonical_json(stored).encode()).hexdigest(),
            }
        )
        with write_tx(conn):
            seen = conn.execute(
                "SELECT 1 FROM audit WHERE event = 'config.rules_converted'"
                " AND outcome = 'denied' AND data = ?",
                (data,),
            ).fetchone()
            if seen is None:
                conn.execute(
                    "INSERT INTO audit (ts, address_id, event, actor, outcome, data)"
                    " VALUES (?, NULL, 'config.rules_converted', ?, 'denied', ?)",
                    (now, actor, data),
                )
        return {"converted": False, "customised": list(c.customised)}
    if c.doc is None and not c.to_default:
        return {"converted": False}
    defaults: tuple[str, ...] = ()
    if c.doc is not None:  # and the copies of the default rules, where nothing changes (P24)
        doc, defaults = rules.strip_defaults(c.doc, current_schema(conn))
        c = rules.Conversion(doc, c.stripped, to_default=doc is None)
    with write_tx(conn):
        if c.to_default:
            conn.execute("DELETE FROM settings WHERE key = ?", (KEY["rules"],))
        else:
            conn.execute(
                "UPDATE settings SET value = ?, updated_at = ?, updated_by = ? WHERE key = ?",
                (canonical_json(c.doc), now, actor, KEY["rules"]),
            )
        conn.execute(
            "INSERT INTO audit (ts, address_id, event, actor, outcome, data)"
            " VALUES (?, NULL, 'config.rules_converted', ?, 'ok', ?)",
            (
                now,
                actor,
                json.dumps(
                    {"stripped": list(c.stripped)}
                    | ({"defaults_stripped": list(defaults)} if defaults else {})
                    | ({"default": True} if c.to_default else {})
                ),
            ),
        )
    out: dict[str, Any] = {"converted": True, "stripped": list(c.stripped)}
    if defaults:
        out["defaults_stripped"] = list(defaults)
    return out | ({"default": True} if c.to_default else {})


# ---------------------------------------------------------------------------- parse and validate


def parse(conn: sqlite3.Connection, text: str) -> dict[str, Any]:
    """The validated document in canonical form: only the sections it sets."""
    if len(text.encode()) > MAX_BYTES:
        raise InvalidInputError(f"the config file is over {MAX_BYTES // 1024} KB")
    raw = load_yaml(text, source="config")
    if not isinstance(raw, dict):
        raise InvalidInputError("config: expected a mapping with `version: 1` and sections")
    doc = cast("dict[str, Any]", raw)
    if doc.get("version") != 1:
        raise InvalidInputError("config: `version: 1` is required")
    return validate(conn, {k: v for k, v in doc.items() if k != "version"})


def _check_sections(doc: dict[str, Any]) -> None:
    for k in doc:
        if k in LATER:
            raise InvalidInputError(f"config: {k}: {LATER[k]}")
        if k not in SECTIONS:
            raise InvalidInputError(f"config: unknown section {k!r}; sections are "
                                    f"{', '.join(SECTIONS)}")  # fmt: skip
    if not doc:
        raise InvalidInputError("config: no sections to apply")
    if doc.get("org_domains") == DEFAULT:
        raise InvalidInputError("config: org_domains has no default; list your domains")


def validate(conn: sqlite3.Connection, doc: dict[str, Any], *,
             imported: bool = False) -> dict[str, Any]:  # fmt: skip
    """`imported`: a bundle's config (OD-359). A bundle's `schema` never gets here: import and
    restore drop it from the staged copy first (`importer.drop_schema`, P11), so it needs `ecf
    config apply` (dialog, step-up, Security Notice) on this install."""
    _check_sections(doc)
    now = current(conn)
    out: dict[str, Any] = {k: DEFAULT for k, v in doc.items() if v == DEFAULT}
    if "org_domains" in doc:
        out["org_domains"] = _org_domains(conn, _strings(doc["org_domains"], "org_domains"))
    org = out.get("org_domains", now["org_domains"] or [])
    if "org_addresses" in doc and "org_addresses" not in out:
        out["org_addresses"] = _org_addresses(doc["org_addresses"])
    listed: list[dict[str, str]] = _effective(out, now, "org_addresses") or []
    if "move_folders" in doc and "move_folders" not in out:
        out["move_folders"] = _folders(doc["move_folders"])
    folders: list[str] = _effective(out, now, "move_folders") or []
    if "forward_allow_list" in doc and "forward_allow_list" not in out:
        out["forward_allow_list"] = _forwards(conn, doc["forward_allow_list"], org, listed)
    elif (("org_domains" in doc or "org_addresses" in doc) and now["forward_allow_list"]
          and "forward_allow_list" not in out):  # fmt: skip
        # the new internal set must still cover them: refused otherwise (OD-452)
        _forwards(conn, now["forward_allow_list"], org, listed)
    if "action_policy" in doc and "action_policy" not in out:
        out["action_policy"] = _policy(doc["action_policy"])
    _schema_and_rules(conn, doc, out, now, folders, imported=imported)
    if "export_schedule" in doc and "export_schedule" not in out:
        if doc["export_schedule"] not in EXPORT_SCHEDULES:
            raise InvalidInputError("config: export_schedule: daily, weekly or off")
        out["export_schedule"] = doc["export_schedule"]
    if "templates" in doc and "templates" not in out:
        templates.load_templates(canonical_json(doc["templates"]), source="templates")
        out["templates"] = doc["templates"]
    return out


def _schema_and_rules(conn: sqlite3.Connection, doc: dict[str, Any], out: dict[str, Any],
                      now: dict[str, Any], folders: list[str], *,
                      imported: bool = False) -> None:  # fmt: skip
    """`schema` first, then `rules` against the effective schema (OD-478). While extensions
    are off (OD-481) the effective schema is the shipped one, whatever is stored."""
    rules_doc = doc["rules"] if "rules" in doc and "rules" not in out else None
    if "schema" in doc and "schema" not in out:
        if not extensions_enabled() and not imported:
            raise InvalidInputError(f"config: schema: {EXTENSIONS_OFF}")
        _refuse_local_presets(conn)
        out["schema"] = _schema(conn, doc["schema"], now["schema"],
                                rules_doc or _effective(out, now, "rules"))  # fmt: skip
    if doc.get("schema") == DEFAULT:
        _check_sender_categories(conn, now["schema"], None)
    schema = _effective_schema(_effective(out, now, "schema"))
    if rules_doc is not None:
        out["rules"] = _rules(rules_doc, folders, schema, imported=imported)
    elif "move_folders" in doc or "schema" in doc or doc.get("rules") == DEFAULT:
        # `rules: default` too: the starter rule ids must not be values the extension adds
        # the rules in force must still move only to allowed folders, and still compile
        # against the new schema: a field or value a rule uses can't go (OD-452 pattern)
        try:
            _rules(_effective(out, now, "rules") or _starter(), folders, schema)
        except InvalidInputError as exc:
            if "schema" not in doc or doc.get("rules") == DEFAULT:
                raise
            raise InvalidInputError(f"config: schema: the rules in force don't compile against"
                                    f" it: {exc.detail}; change the rules in the same"
                                    " file") from None  # fmt: skip


def _effective(out: dict[str, Any], now: dict[str, Any], section: str) -> Any:
    """The section's value after this document: None where the shipped default applies."""
    v = out.get(section, now[section])
    return None if v == DEFAULT else v


def _strings(v: Any, where: str) -> list[str]:
    if not isinstance(v, list) or not all(isinstance(x, str) for x in cast("list[Any]", v)):
        raise InvalidInputError(f"config: {where}: expected a list of strings")
    items = cast("list[str]", v)
    if len(items) > MAX_ENTRIES:
        raise InvalidInputError(f"config: {where}: at most {MAX_ENTRIES} entries")
    return items


def _org_domains(conn: sqlite3.Connection, domains: list[str]) -> list[str]:
    """Public domains refused; empty only when no watched address is at a non-public domain
    (OD-441)."""
    if domains:
        return addresses.check_org_domains(domains)
    own = sorted({
        str(r[0]).rsplit("@", 1)[-1].lower()
        for r in conn.execute("SELECT email FROM addresses WHERE removed_at IS NULL")
    } - PUBLIC_DOMAINS)  # fmt: skip
    if own:
        raise InvalidInputError(f"config: org_domains can't be empty while you watch an address"
                                f" at {', '.join(own)}")  # fmt: skip
    return []


def _org_addresses(v: Any) -> list[dict[str, str]]:
    shape = f"a list of at most {MAX_ENTRIES} {{address, name}} entries (name optional)"
    if not isinstance(v, list) or len(cast("list[Any]", v)) > MAX_ENTRIES:
        raise InvalidInputError(f"config: org_addresses: {shape}")
    out: list[dict[str, str]] = []
    accounts: set[str] = set()
    for e in cast("list[Any]", v):
        keys: set[str] = set(cast("dict[str, Any]", e)) if isinstance(e, dict) else set()
        if not keys or not keys <= {"address", "name"} or "address" not in keys:
            raise InvalidInputError(f"config: org_addresses: {shape}")
        entry = cast("dict[str, Any]", e)
        raw = entry["address"]
        if not isinstance(raw, str) or len(raw) > MAX_ADDRESS:
            raise InvalidInputError(f"config: org_addresses: not an address: {raw!r}")
        local, domain = addresses.parse_email(raw)
        email = f"{local}@{domain}".lower()
        if internal.fold(email) in accounts:
            raise InvalidInputError(f"config: org_addresses: {raw} is listed twice (Gmail ignores"
                                    " dots and +tags, so these are one account)")  # fmt: skip
        accounts.add(internal.fold(email))
        item = {"address": email}
        if "name" in entry:
            item["name"] = _name(entry["name"], raw)
        out.append(item)
    return out


def _name(v: Any, addr: str) -> str:
    if not isinstance(v, str) or len(v) > MAX_NAME or _CONTROL.search(v):
        raise InvalidInputError(f"config: org_addresses: {addr}: the name is text of at most"
                                f" {MAX_NAME} characters")  # fmt: skip
    name = " ".join(v.split())
    if len(internal.name_words(name)) < 2:  # OD-433
        raise InvalidInputError(f"config: org_addresses: {addr}: a name needs at least"
                                " two words (a first name alone matches too many)")  # fmt: skip
    return name


def _folders(v: Any) -> list[str]:
    out: list[str] = []
    for f in _strings(v, "move_folders"):
        if not f or f != f.strip() or len(f) > 200 or _CONTROL.search(f):
            raise InvalidInputError(f"config: move_folders: not a folder name: {f!r}")
        if f.upper() == "INBOX":
            raise InvalidInputError("config: move_folders: INBOX isn't a move target")
        if f in out:
            raise InvalidInputError(f"config: move_folders: {f!r} is listed twice")
        out.append(f)
    return out


def _forwards(
    conn: sqlite3.Connection, v: Any, org: list[str], listed: list[dict[str, str]]
) -> list[dict[str, str]]:
    if not isinstance(v, list) or len(cast("list[Any]", v)) > MAX_ENTRIES:
        raise InvalidInputError(f"config: forward_allow_list: a list of at most {MAX_ENTRIES}"
                                " {id, address} entries")  # fmt: skip
    monitored = {
        r[0].lower() for r in conn.execute("SELECT email FROM addresses WHERE removed_at IS NULL")
    }
    out: list[dict[str, str]] = []
    for e in cast("list[Any]", v):
        if not isinstance(e, dict) or set(cast("dict[str, Any]", e)) != {"id", "address"}:
            raise InvalidInputError("config: forward_allow_list: each entry is {id, address}")
        entry = cast("dict[str, Any]", e)
        eid, addr = entry["id"], entry["address"]
        if not isinstance(eid, str) or not _ENTRY_ID.fullmatch(eid):
            raise InvalidInputError(f"config: forward_allow_list: id {eid!r}: lowercase letters,"
                                    " digits and _, at most 40")  # fmt: skip
        if any(o["id"] == eid for o in out):
            raise InvalidInputError(f"config: forward_allow_list: id {eid!r} is listed twice")
        local, domain = addresses.parse_email(str(addr))
        email = f"{local}@{domain}"
        if domain not in org and email.lower() not in {x["address"] for x in listed}:
            raise InvalidInputError(f"config: forward_allow_list: {addr} must be in org_domains"
                                    " or exactly in org_addresses (forwards stay with people you"
                                    " work with; a Gmail address with other dots or a +tag"
                                    " doesn't match; to remove an org domain or address a"
                                    " forward needs, remove the forward too)")  # fmt: skip
        if email.lower() in monitored:
            raise InvalidInputError(f"config: forward_allow_list: {addr} is a monitored address"
                                    " (a forward there would be fetched again)")  # fmt: skip
        out.append({"id": eid, "address": email})
    return out


def _policy(v: Any) -> dict[str, dict[str, str]]:
    if not isinstance(v, dict):
        raise InvalidInputError(f"config: action_policy: expected {_POLICY_SHAPE}")
    doc = cast("dict[str, Any]", v)
    if "high" in doc:
        raise InvalidInputError("config: action_policy: high is a hard ceiling (§8.3): hide"
                                " actions always need approval there")  # fmt: skip
    if set(doc) != {"standard"} or not isinstance(doc["standard"], dict):
        raise InvalidInputError(f"config: action_policy: expected {_POLICY_SHAPE}")
    std = cast("dict[str, Any]", doc["standard"])
    for action, choice in std.items():
        if action not in DEFAULT_POLICY:
            raise InvalidInputError(
                f"config: action_policy: {action!r} can't be set; only the hide actions "
                f"({', '.join(DEFAULT_POLICY)}) have a choice, the rest are fixed (§8.3)"
            )
        if choice not in POLICY_CHOICES:
            raise InvalidInputError(f"config: action_policy: {action}: auto or approve")
    return {"standard": DEFAULT_POLICY | cast("dict[str, str]", std)}


def _refuse_local_presets(conn: sqlite3.Connection) -> None:
    """D8: your fields are for preset C only, so none while any address is preset A or B; P1:
    nor while a C address has the local fallback on."""
    local, held = at_odds(conn)
    if local:
        raise InvalidInputError(f"config: schema: your own fields are for preset C addresses only:"
                                f" {LOCAL_REASON}, and {', '.join(local[:5])}"
                                f" {'uses it' if len(local) == 1 else 'use them'}; remove the"
                                " `schema` section, or move those addresses to preset C (ecf"
                                " address remove, then add with --preset C)")  # fmt: skip
    # P1 (operator decision 2026-10-14): nor while a C address has the local fallback on, which
    # asks the local model, never your fields (`fallback.set_timeout` refuses turning it on)
    if held:
        raise InvalidInputError(f"config: schema: {', '.join(held[:5])} "
                                f"{'has' if len(held) == 1 else 'have'} the local fallback on"
                                " (claude_queue_timeout), and the local model isn't asked your"
                                " own fields; turn it off first (ecf settings set"
                                " claude_queue_timeout off --address <address>)")  # fmt: skip


def _rules(v: Any, folders: list[str], schema: CompiledSchema, *,
           imported: bool = False) -> Any:  # fmt: skip
    """The rules file, checked as mail runs it: composed with ecf's fixed and default rules
    (`rules.compose`, P2, P3), its layering (`rules.check_layers`, P17), move targets and labels
    from your fields (P12) across the whole set. `compose` refuses a rule id that is a value the
    extension adds (S8)."""
    checked = v
    if imported and rules.convert_v2(v).customised:
        # A2 (OD-500): a bundle's v2 rules with a changed fixed rule are kept, as at service
        # start (the starter rules apply, with a System Error); the rest must still be valid
        rest: list[Any] = [r for r in cast("list[Any]", v["rules"])
                if not (isinstance(r, dict) and cast("dict[str, Any]", r).get("id")
                        in rules.fixed_ids())]  # fmt: skip
        if not rest:
            return v
        checked = v | {"rules": rest}
    text = canonical_json(checked)
    rules.check_layers(text, schema, source="config: rules")
    compiled = rules.compose(schema, text, source="config: rules")
    for r in compiled.rules:
        for a in r.then:
            if a.action == "move" and (not isinstance(a.target, str) or a.target not in folders):
                raise InvalidInputError(f"config: rules: rule {r.id} moves to {a.target!r}, which"
                                        " isn't in move_folders")  # fmt: skip
    _check_label_from(compiled, schema)
    return v


def _check_label_from(compiled: rules.CompiledRules, schema: CompiledSchema) -> None:
    """P12: `{label: {field: x}}` on one of your fields makes its values label names
    (`policy.labels`), so it is refused when they repeat another field's value or a label name
    (a reused catch-all, `other`, would be written as a keyword another rule means otherwise)."""
    labels = set(policy.BUILTIN_LABELS) | {
        a.target for r in compiled.rules for a in r.then
        if a.action == "label" and isinstance(a.target, str)}  # fmt: skip
    for r in compiled.rules:
        for a in r.then:
            if not isinstance(a.target, rules.LabelFrom) or a.target.field not in (
                    schema.extension_fields):  # fmt: skip
                continue
            name = a.target.field
            others = {v for f in schema.fields.values() if f.name != name
                      and f.kind is FieldKind.ENUM for v in f.values}  # fmt: skip
            clash = sorted(set(schema.fields[name].values) & (others | labels))
            if clash:
                raise InvalidInputError(f"config: rules: rule {r.id} labels from {name}, whose"
                                        f" value{'s' if len(clash) > 1 else ''}"
                                        f" {', '.join(clash[:5])} also name another field's"
                                        " value or a label; label with fixed names instead"
                                        " ({label: name}, with `if`)")  # fmt: skip


def _schema(conn: sqlite3.Connection, v: Any, old: Any, rules_doc: Any) -> Any:
    """The extension, checked against the caps and content rules; every violation listed. Any
    value but `default` is checked (an empty mapping, a list or null too). An added category
    value a confirmed sender uses can't be removed."""
    if v is None:
        raise InvalidInputError("config: schema: a mapping with fields and/or category_values,"
                                " or `default` to remove the extension")  # fmt: skip
    # always: built-in labels, the fixed and default rule ids, and the applied file's (P2, C8)
    ids = _rule_ids(rules_doc) | set(rules.fixed_ids()) | set(rules.default_ids())
    try:
        extend_schema(load_schema(), v, policy.BUILTIN_LABELS | ids)
    except (InvalidInputError, SchemaLimitError) as exc:
        raise type(exc)(f"config: {exc.detail}", **exc.extra) from None
    _check_sender_categories(conn, old, v)
    return v


def _added_categories(ext: Any) -> set[str]:
    d = cast("dict[str, Any]", ext) if isinstance(ext, dict) else {}
    rc: Any = d.get("category_values")
    return set(cast("dict[str, Any]", rc)) if isinstance(rc, dict) else set()


def _check_sender_categories(conn: sqlite3.Connection, old: Any, new: Any) -> None:
    """Refuse removing an added category value that confirmed senders use, naming them by
    domain and address (sender records hold no sender address)."""
    gone = _added_categories(old) - _added_categories(new)
    if not gone:
        return
    rows = conn.execute(
        "SELECT s.confirmed_category, s.domain, a.address_id FROM senders s JOIN addresses a"
        " ON a.address_id = s.address_id WHERE s.confirmed_category IN (SELECT value FROM"
        " json_each(?)) ORDER BY s.confirmed_category, a.address_id, s.domain",
        (json.dumps(sorted(gone)),)).fetchall()  # fmt: skip
    if rows:
        named = ", ".join(f"{r[0]}: a sender at {r[1]} ({r[2]})" for r in rows[:5])
        more = f" (+{len(rows) - 5} more)" if len(rows) > 5 else ""
        raise InvalidInputError(f"config: schema: confirmed senders use a category value this"
                                f" removes: {named}{more}; confirm them as another category"
                                " first (ecf sender confirm)")  # fmt: skip


def _rule_ids(rules_doc: Any) -> frozenset[str]:
    d = cast("dict[str, Any]", rules_doc) if isinstance(rules_doc, dict) else {}
    rs: Any = d.get("rules")
    if not isinstance(rs, list):
        return frozenset()
    found = [cast("dict[str, Any]", r).get("id") for r in cast("list[Any]", rs)
             if isinstance(r, dict)]  # fmt: skip
    return frozenset(i for i in found if isinstance(i, str))


def schema_preview(before: dict[str, Any], doc: dict[str, Any]) -> dict[str, Any] | None:
    """For a document that sets `schema`: the budget line (with the bytes per classify read,
    P16), whether it is near a cap, and the prompt lines the extension adds or drops (what the
    dry run prints before step-up). An old extension that no longer compiles is tolerated, so
    `schema: default` can remove it."""
    if "schema" not in doc:
        return None
    new = None if doc["schema"] == DEFAULT else doc["schema"]
    base = load_schema()
    budget = extension_budget(base, new)
    old_lines = _ext_lines(before["schema"])
    new_lines = _ext_lines(new)
    diff_lines = ([f"- {x}" for x in old_lines if x not in new_lines]
                  + [f"+ {x}" for x in new_lines if x not in old_lines])  # fmt: skip
    return {"budget": budget["line"], "near": budget["near"], "prompt_diff": diff_lines,
            "read_bytes": budget["read_bytes"]}  # fmt: skip


def notes(conn: sqlite3.Connection, doc: dict[str, Any]) -> list[str]:
    """What the dry run and apply say beside the changes: your fields' warnings and the privacy
    line (D9b, D15, D10), how a new rules file layers (D14, P17), and the stored rules' warnings
    (P3: a pre-S4 file without a catch-all; an off id ecf skips)."""
    out: list[str] = []
    ext = doc.get("schema")
    if ext is not None and ext != DEFAULT:
        out += [f"schema: {w.removeprefix('schema.')}" for w in
                extension_warnings(load_schema(), ext)]  # fmt: skip
        out.append(PRIVACY_LINE)
    new_rules = doc.get("rules")
    if new_rules is not None and new_rules != DEFAULT:
        eff = None if ext == DEFAULT else ext if ext is not None else _stored(conn, "schema")
        # a bundle's v2 rules with a changed fixed rule (A2) don't compile: kept, refused at load
        with contextlib.suppress(InvalidInputError):
            out += rules.layer_lines(canonical_json(new_rules), _effective_schema(eff),
                                     source="config: rules")  # fmt: skip
    elif "rules" not in doc:
        out += rules_warnings(conn)
    return out


def _ext_lines(ext: Any) -> list[str]:
    if ext is None:
        return []
    try:
        text = extension_text(_extended(ordered_json(ext)))
    except (InvalidInputError, SchemaLimitError):
        return ["(the stored extension, which no longer compiles)"]
    return [x for x in text.splitlines() if x.strip()]


# ---------------------------------------------------------------------------- diff


def diff(before: dict[str, Any], doc: dict[str, Any]) -> list[dict[str, str]]:
    """One line per section that changes, in plain words."""
    out: list[dict[str, str]] = []
    for s in SECTIONS:
        new = None if doc.get(s) == DEFAULT else doc.get(s)
        same = ordered_json if s == "schema" else canonical_json  # order is the prompt's order
        if s not in doc or same(new) == same(before[s]):
            continue
        out.append({"section": s, "change": _describe_change(s, before[s], new)})
    return out


_SHIPPED_NAME = {"forward_allow_list": "none", "move_folders": "none", "org_addresses": "none",
                 "action_policy": "the default policy", "rules": "the starter rules",
                 "templates": "the shipped templates", "export_schedule": "daily",
                 "schema": "built-in schema"}  # fmt: skip


def _shipped_value(section: str) -> Any:
    if section == "rules":
        return _starter()
    if section == "templates":
        return _shipped_templates()
    if section == "action_policy":
        return {"standard": DEFAULT_POLICY}
    if section == "export_schedule":
        return "daily"
    if section == "schema":
        return {}
    return []


def _describe_change(section: str, old: Any, new: Any) -> str:  # noqa: PLR0911 - one per section
    if new is None:  # a reset; old is set, or there would be no change
        change = _describe_change(section, old, _shipped_value(section))
        return f"reset to {_SHIPPED_NAME[section]}: {change}"
    if section in ("org_domains", "move_folders"):
        return _set_change(old or [], new)
    if section == "export_schedule":
        return f"{old or 'daily'} to {new}"
    if section == "schema":
        if not extensions_enabled():  # only a removal gets here; the prompt doesn't change
            return f"unused while extensions are off: {_schema_change(old or {}, new)}"
        return f"{SCHEMA_LEAD}: {_schema_change(old or {}, new)}"
    if section == "org_addresses":
        was_addr: list[dict[str, str]] = old or []
        listed: list[dict[str, str]] = new
        return _keyed_change({e["address"]: e for e in was_addr},
                             {e["address"]: e for e in listed})  # fmt: skip
    if section == "forward_allow_list":
        was_list: list[dict[str, str]] = old or []
        entries: list[dict[str, str]] = new
        return _keyed_change({e["id"]: e for e in was_list}, {e["id"]: e for e in entries})
    if section == "action_policy":
        was: dict[str, str] = old["standard"] if old else DEFAULT_POLICY
        now: dict[str, str] = new["standard"]
        return ", ".join(f"{a}: {was.get(a, 'auto')} to {now[a]}" for a in now
                         if was.get(a, "auto") != now[a]) or "unchanged values"  # fmt: skip
    if section == "rules":
        was = {r["id"]: r for r in (old or _starter())["rules"]}
        change = _keyed_change(was, {r["id"]: r for r in new["rules"]})
        order = [r["id"] for r in new["rules"] if r["id"] in was]
        if order != [i for i in was if i in order]:
            change += "; order changed"
        return ("from the starter rules: " if old is None else "") + change
    was = {t["id"]: t for t in (old or _shipped_templates())["templates"]}
    return ("from the shipped templates: " if old is None else "") + _keyed_change(
        was, {t["id"]: t for t in new.get("templates", [])}
    )


def _schema_change(old: dict[str, Any], new: dict[str, Any]) -> str:
    """Field and category-value names added, removed or changed; short for the dialog."""
    parts: list[str] = []
    for key, what in (("fields", "fields"), ("category_values", "category")):
        change = _keyed_change(old.get(key) or {}, new.get(key) or {})
        if change != "unchanged entries":
            parts.append(f"{what} {change}")
    return "; ".join(parts) or "unchanged entries"


def _set_change(old: list[str], new: list[str]) -> str:
    added = [x for x in new if x not in old]
    removed = [x for x in old if x not in new]
    parts = [f"+{x}" for x in added] + [f"-{x}" for x in removed]
    return ", ".join(parts) or "reordered"


def _keyed_change(old: dict[str, Any], new: dict[str, Any]) -> str:
    added = [k for k in new if k not in old]
    removed = [k for k in old if k not in new]
    changed = [k for k in new if k in old and canonical_json(new[k]) != canonical_json(old[k])]
    parts = ([f"+{k}" for k in added] + [f"-{k}" for k in removed]
             + [f"~{k}" for k in changed])  # fmt: skip
    return ", ".join(parts) or "unchanged entries"


def _starter() -> dict[str, Any]:
    return _shipped("starter_rules.yaml")


def _shipped_templates() -> dict[str, Any]:
    return _shipped("templates.yaml")


def _shipped(name: str) -> dict[str, Any]:
    from importlib import resources  # noqa: PLC0415 - read only when a diff needs the default

    text = resources.files("ecf_server.data").joinpath(name).read_text("utf-8")
    return cast("dict[str, Any]", load_yaml(text, source=name))


# riskiest first, so a cut-off summary still names what matters most (V1.2 review, 2026-09-30)
RISK_ORDER = ("forward_allow_list", "schema", "rules", "action_policy", "org_domains",
              "org_addresses", "export_schedule", "templates", "move_folders")  # fmt: skip


def summary(changes: list[dict[str, str]], limit: int = 300) -> str:
    """The changes, riskiest first; whole sections that don't fit are counted, never dropped
    silently."""
    ordered = sorted(changes, key=lambda c: RISK_ORDER.index(c["section"]))
    parts = [f"{c['section']}: {c['change']}" for c in ordered]
    shown: list[str] = []
    for i, part in enumerate(parts):
        rest = len(parts) - i - 1
        tail = f"; +{rest} more section{'s' if rest != 1 else ''}" if rest else ""
        text = "; ".join([*shown, part])
        if len(text) + len(tail) <= limit:
            shown.append(part)
            continue
        if not shown:  # the riskiest alone is too long: cut it, and count the rest
            shown.append(part[: max(limit - len(tail) - 1, 1)] + "…")
            return "; ".join(shown) + tail
        more = len(parts) - i
        return "; ".join(shown) + f"; +{more} more section{'s' if more != 1 else ''}"
    return "; ".join(shown)


# ---------------------------------------------------------------------------- apply


@stepup.purpose("config_apply")
def _describe_apply(conn: sqlite3.Connection, target: dict[str, Any]) -> stepup.Bound:
    doc = target.get("document")
    if not isinstance(doc, dict):
        raise InvalidInputError("config_apply: the target is a config document")
    doc = validate(conn, cast("dict[str, Any]", doc))
    before = current(conn)
    changes = diff(before, doc)
    return stepup.Bound(
        stepup.digest("config_apply", canonical_json(doc), canonical_json(before),
                      ordered_json(doc.get("schema")), ordered_json(before["schema"])),
        f"ecf: apply config: {_lead(conn, changes)}{personal_targets(before, doc)}"
        f"{summary(changes, 200)}",
    )  # fmt: skip


def _lead(conn: sqlite3.Connection, changes: list[dict[str, str]]) -> str:
    """A schema change always leads, whatever the summary's length cuts (OD-478), with what live
    needs again (P15): a passing /ecf-eval run and the gate's reviews, per address
    (`review.GATE_COUNT`); not while extensions are off, when the stored one is unused and
    removing it changes no prompt."""
    if not (extensions_enabled() and any(c["section"] == "schema" for c in changes)):
        return ""
    from ecf_server import review  # noqa: PLC0415 - review imports this module

    rows = conn.execute("SELECT address_id, sensitivity FROM addresses WHERE removed_at IS NULL"
                        " ORDER BY address_id").fetchall()  # fmt: skip
    each = ", ".join(f"{r[0]} {review.GATE_COUNT[str(r[1])]}" for r in rows[:4])
    more = f" +{len(rows) - 4} more" if len(rows) > 4 else ""
    need = (f"a /ecf-eval run and reviews ({each}{more}) are needed before live again" if rows
            else "a /ecf-eval run and 100 reviews (200 on a high address) are needed before"
            " live")  # fmt: skip
    return f"{SCHEMA_LEAD}; {need}; "


def personal_targets(before: dict[str, Any], doc: dict[str, Any]) -> str:
    """New forward targets at a public provider, named first in the dialog (OD-437)."""
    new = doc.get("forward_allow_list")
    if not isinstance(new, list):
        return ""
    was: list[dict[str, str]] = before["forward_allow_list"] or []
    had = {e["address"].lower() for e in was}
    added = [str(e["address"]) for e in cast("list[dict[str, str]]", new)
             if e["address"].lower() not in had
             and e["address"].rsplit("@", 1)[-1].lower() in PUBLIC_DOMAINS]  # fmt: skip
    if not added:
        return ""
    more = f" (+{len(added) - 1} more)" if len(added) > 1 else ""
    return f"{added[0]}{more} is a personal account your organization doesn't control; "


@dataclass(frozen=True)
class Result:
    changed: bool
    applied: bool
    changes: list[dict[str, str]]
    sha256: str
    schema: dict[str, Any] | None = None  # schema_preview, when the document sets `schema`
    notes: tuple[str, ...] = ()  # warnings, the privacy line, rules layering (`notes`)

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"changed": self.changed, "applied": self.applied,
                               "changes": self.changes, "sha256": self.sha256,
                               "notes": list(self.notes)}  # fmt: skip
        if self.schema is not None:
            out["schema"] = self.schema
        return out


def apply(
    conn: sqlite3.Connection,
    clock: Clock,
    notifier: Notifier,
    text: str,
    *,
    dry_run: bool,
    nonce: str | None,
    actor: str = "os_user",
) -> Result:
    doc = parse(conn, text)
    before = current(conn)
    changes = diff(before, doc)
    sha = hashlib.sha256(canonical_json(doc).encode()).hexdigest()
    preview = schema_preview(before, doc)
    said = tuple(notes(conn, doc))
    if not changes and not dry_run and "rules" in doc and doc["rules"] != DEFAULT:
        _acknowledge_layers(conn, doc["rules"])  # the same file again: its new meaning, confirmed
    if not changes or dry_run:
        return Result(bool(changes), False, changes, sha, preview, said)
    from ecf_server import stages  # noqa: PLC0415 - stages imports modules that import this one

    stepup.consume(conn, clock, "config_apply", {"document": doc}, nonce)
    now = to_ts(clock.now())
    # while extensions are off the effective schema stays the shipped one (OD-481)
    schema_changed = extensions_enabled() and any(c["section"] == "schema" for c in changes)
    with write_tx(conn):
        for c in changes:
            s = c["section"]
            if doc[s] == DEFAULT:
                conn.execute("DELETE FROM settings WHERE key = ?", (KEY[s],))
                continue
            stored = ordered_json(doc[s]) if s == "schema" else canonical_json(doc[s])
            conn.execute(
                "INSERT INTO settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?)"
                " ON CONFLICT (key) DO UPDATE SET value = excluded.value,"
                " updated_at = excluded.updated_at, updated_by = excluded.updated_by",
                (KEY[s], stored, now, actor),
            )
        if "schema" in doc:  # this extension went through the dialog: in use (D11)
            _mark_activated(conn, None if doc["schema"] == DEFAULT else doc["schema"], now)
        if "rules" in doc:  # applied again: no longer the pre-S4 file (P3)
            conn.execute("DELETE FROM settings WHERE key = ?", (PRE_LAYERS,))
        # a new schema is a new gate key: live addresses drop to assist with it, not a tick later
        demoted = (
            stages.demote_live(conn, now, actor, stages.SCHEMA_REASON) if schema_changed else []
        )
        conn.execute(
            "INSERT INTO audit (ts, address_id, event, actor, outcome, data)"
            " VALUES (?, NULL, 'config.applied', ?, 'ok', ?)",
            (now, actor, json.dumps({"sha256": sha, "changes": changes})),
        )
    ident = slack_admin.identity(conn)
    text = f"Security-relevant config changed: {_lead(conn, changes)}{summary(changes)}"
    slack_admin.notice(conn, clock, notifier, text,
                       dms=[ident.member] if ident and ident.member else [])  # fmt: skip
    stages.announce_demoted(conn, clock, demoted, stages.SCHEMA_REASON)
    return Result(True, True, changes, sha, preview, said)


# ---------------------------------------------------------------------------- activation (D11)


def _ext_sha(ext: Any) -> str:
    return hashlib.sha256(ordered_json(ext).encode()).hexdigest()


def _mark_activated(conn: sqlite3.Connection, ext: Any, now: str) -> None:
    """Inside the caller's transaction: the stored extension (None: none) is the one in use."""
    if ext is None:
        conn.execute("DELETE FROM settings WHERE key = ?", (ACTIVATED,))
        return
    slack_admin.put_setting(conn, ACTIVATED, _ext_sha(ext), now, actor="service")


def pending_activation(conn: sqlite3.Connection) -> list[str]:
    """The live and other C addresses a stored extension not yet in use would move (it reaches
    the classifier at the next service start, `activate_stored`): for `ecf upgrade`'s pre-check
    (P11). Empty while extensions are off, with none stored, or with one already in use."""
    ext = _stored(conn, "schema")
    if ext is None or not extensions_enabled() or extension_problem(conn) is not None:
        return []
    if slack_admin.setting(conn, ACTIVATED) == _ext_sha(ext):
        return []
    rows = conn.execute("SELECT address_id FROM addresses WHERE removed_at IS NULL AND preset ="
                        " 'C' ORDER BY address_id")  # fmt: skip
    return [str(r[0]) for r in rows]


def at_odds(conn: sqlite3.Connection) -> tuple[list[str], list[str]]:
    """The addresses that sit badly with stored user fields (pre-S4 state; review R5): preset A or
    B (D8), and preset C with the local fallback on (P1). For the activation notice and doctor."""
    local = [str(r[0]) for r in conn.execute("SELECT address_id FROM addresses WHERE removed_at"
                                             " IS NULL AND preset IN ('A', 'B') ORDER BY"
                                             " address_id")]  # fmt: skip
    held = [str(r[0]) for r in conn.execute("SELECT address_id FROM addresses WHERE removed_at"
                                            " IS NULL AND preset = 'C' AND fallback_enabled = 1"
                                            " ORDER BY address_id")]  # fmt: skip
    return local, held


def activate_stored(conn: sqlite3.Connection, clock: Clock, notifier: Notifier) -> list[str]:
    """At service start (D11, P11): a stored extension this version uses for the first time (one
    applied before user fields were on, or by an earlier release) reaches the classifier without
    a dialog, so it is announced: an audit row (`config.schema_activated`), live addresses to
    assist (`SCHEMA_REASON`; a new C gate key) and a Security Notice naming the fields and the
    addresses moved. One that fails this version's checks falls back closed instead
    (`current_schema`, `schema_tick`). Returns the demoted ids."""
    from ecf_server import stages  # noqa: PLC0415 - stages imports modules that import this one

    ext = _stored(conn, "schema")
    if ext is None or not extensions_enabled() or extension_problem(conn) is not None:
        return []
    if slack_admin.setting(conn, ACTIVATED) == _ext_sha(ext):
        return []
    now = to_ts(clock.now())
    d = cast("dict[str, Any]", ext)
    fields = sorted(cast("dict[str, Any]", d.get("fields") or {}))
    cats = sorted(cast("dict[str, Any]", d.get("category_values") or {}))
    c_addresses = pending_activation(conn)
    local, fallback_on = at_odds(conn)  # review R5: the states D8 and P1 refuse, named
    with write_tx(conn):
        demoted = stages.demote_live(conn, now, "service", stages.SCHEMA_REASON)
        _mark_activated(conn, ext, now)
        conn.execute(
            "INSERT INTO audit (ts, address_id, event, actor, outcome, data)"
            " VALUES (?, NULL, 'config.schema_activated', 'service', 'ok', ?)",
            (now, json.dumps({"sha256": _ext_sha(ext), "fields": fields, "category_values": cats,
                              "c_addresses": c_addresses, "demoted": demoted,
                              "preset_a_b": local, "fallback_on": fallback_on})),
        )  # fmt: skip
    named = ", ".join(fields[:8]) or "none"
    added = f"; added category values: {', '.join(cats)}" if cats else ""
    moved = f"; back to assist: {', '.join(demoted)}" if demoted else ""
    if local:
        moved += (f"; preset A or B addresses, which the local model isn't asked them for:"
                  f" {', '.join(local[:5])}")  # fmt: skip
    if fallback_on:
        moved += (f"; C addresses with the local fallback on, which isn't asked them:"
                  f" {', '.join(fallback_on[:5])}")  # fmt: skip
    ident = slack_admin.identity(conn)
    slack_admin.notice(conn, clock, notifier,
                       f"Your own schema fields are now in use (stored before this version used"
                       f" them): fields {named}{added}; C addresses:"
                       f" {', '.join(c_addresses) or 'none'}{moved}. {SCHEMA_LEAD}. To stop it:"
                       " `schema: default` with ecf config apply.",
                       dms=[ident.member] if ident and ident.member else [])  # fmt: skip
    stages.announce_demoted(conn, clock, demoted, stages.SCHEMA_REASON)
    log.info("config.schema_activated", fields=len(fields), demoted=len(demoted))
    return demoted
