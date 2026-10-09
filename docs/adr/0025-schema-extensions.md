# ADR 0025: schema extensions through `ecf config apply`, with fixed caps

- **Status:** draft (2026-10-08; OD-478); built on branch `ext-core`; ships in
  `v2.0.0` (planned for `v2.1.0`; moved by operator decision 2026-10-08)
- **Context source:** SPEC §7.1, §7.4, §9.3, §9.7, §13.2, §15.1, §15.3;
  `planning-docs/SCHEMA-V2-PLAN.md` step 4 (draft 1, after Phase R)

## Context

Schema v2 (ADR 0024) is one fixed schema for work and personal mail. The operator wants users to
add what their own mail needs (a contract stage, a region, a legal-notice category) without a new
ecf release. Every field and value the classifier is asked about costs prompt tokens in Gemma's
small context, and an added value may become an IMAP label, so an extension has to be bounded and
can't collide with names ecf already writes.

## Decision

- **A `schema` config section** (OD-478): `{fields, category_values}` in `ecf config apply`, with
  step-up, audit and a Security Notice like every other section; `schema: default` removes it. It
  adds fields and `category` values only: no shipped field or value can change, so the safety core
  (ADR 0024) is untouched.
- **The effective schema** is the shipped one plus the extension: extension fields after the
  shipped ones, added values after `category`'s, the extension's prompt text after the shipped
  text (the prefix doesn't change). The service classifies, plans and keys the gate with it; its
  digest is the gate key, so any extension change drops live addresses to assist until the gates
  pass again.
- **Rules** may use extension fields with every action; I1 still gates hiding. Validation order is
  `schema`, then `rules`: removing a field or value a rule uses is refused naming the rule.
- **Fixed caps:** 8 fields, 16 values per field, 4 added category values, 180 characters per
  description, 4,000 characters of prompt text. A file over a cap is refused before step-up with
  every violation listed (`schema_limit`); the dry run always prints the budget line, with "near
  the limit" from 80%; `ecf doctor` has a `schema` row.
- **Content rules:** one printable line per description, not starting with `-` or `:`; an added
  value can't equal any enum value, built-in label or rule ID in force; `rule` and `safety` are
  reserved field names (eval results use them). Every bad name, level and description is listed,
  not only the first. Only `schema: default` removes the extension: an empty mapping, a list,
  `null` or any other value is checked and refused.
- **The operator's order is kept:** the extension is stored, cached and compared in its own key
  order (its order is the prompt's), so a reorder is a change; the digest is deterministic for
  a given order.
- **Old mail and changed levels:** a comparison on an extension field an item lacks (classified
  before the extension), or on a level the ordinal no longer has, is unknown; unknown propagates
  through `not`, `and` and `or` and never matches, so `not` can't make a rule meant for new mail
  hide old mail.
- **Applying a change:** the step-up dialog and the Security Notice lead with "changes the
  classifier prompt; live addresses go back to assist", and live addresses drop to assist in the
  same transaction that stores the extension. An answer to the old schema (Gemma during the
  call, or a Claude classification held for telemetry) isn't recorded: the item is asked again.
  An added `category` value that confirmed senders use can't be removed. Starter rule IDs are
  checked against added values on `rules: default`, and every rules compile (including `ecf rules
  test`) refuses a rule ID that is an added value.
- **A release that clashes with a stored extension** (it ships a name the extension adds): the
  service classifies with the shipped schema under a key of its own (no earlier gate carries
  over), raises a System Error, and `ecf doctor` fails the `schema` row; `schema: default` still
  applies. Imported bundles over a cap are bad bundles.
- **Claude, Slack and evals use the effective schema too:** `/ecf-review` and `/ecf-eval` give
  Claude `schema_text` (ecf's description of each field and value, outside the email) and check
  its answers against the effective schema; the Slack Fix form offers the extension fields after
  the shipped ones and review lines show their values after `ext:`; corpus labels need every
  shipped field and may leave out extension fields; eval results record `schema_digest`, `ecf
  eval compare` warns when two runs' digests differ, and preset B uses Gemma's run only on the
  same schema.

## Alternatives considered

- **Replacing the schema file wholesale:** would let a user drop the safety core; rejected.
- **Caps as settings:** a larger extension can overflow the classifier's context silently;
  fixed in code instead, changed only by a release.
- **Extensions on any enum field** (for example `sender_type`): policy and the starter rules read
  those values; only `category` grows.

## Consequences

- Each extension change is a new gate: the synthetic eval has to run again on the effective
  schema before an address goes live.
- **An added `category` value can pull answers away from the safety categories** (for example a
  `legal_notice` value taking mail the model would otherwise call `regulatory`, or a payment
  request it would call `vendor_change_request`). The fraud and regulator triggers are
  deterministic and don't read the category, but category-based rules do. The control is the
  gate restart: no address goes live on a new extension until the safety gates pass on an eval
  asked with the effective schema (synthetic cards score only shipped fields, so a labelled
  corpus is the way to score extension fields).
- Operator-written descriptions go to Anthropic in presets B and C, and their names to Slack;
  neither is email content (SPEC §12.4).
- Whether a full extension fits Gemma's context: by the measured token counts (SPEC §21.2) it
  fits `num_ctx` 6,144 (OD-477), but no run has used a full extension; unverified, confirm in
  `v2.0.0`.
