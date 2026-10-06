# ADR 0008: One package, one lockstep version

- **Status:** accepted (OD-122, 2026-09-26/27; OD-123, OD-129, 2026-09-27; OD-271, OD-283,
  2026-10-02; OD-374 to OD-382, 2026-10-03); implemented in V1.0, the plugin part in V1.4, upgrade
  checks in V1.5; amended by OD-458 (2026-10-06): GitHub Releases only, no PyPI
- **Context source:** SPEC §11.10, §17.1, §17.4; design plan
  (`docs/history/design-plan-2026-09-27.md`) "Versioning" and "Units"; `pyproject.toml`,
  `ecf_server/data/release.json`, `ecf/upgrade_check.py`

## Context

ecf has three parts that talk to each other: the CLI and MCP server (`ecf`), the local service
(`ecf_server`), and the Claude Code plugin (skills and agents) that `ecf claude` loads. The service
holds all rules and state; the clients only call its socket API. If these parts could be installed
at different versions, every pair would need a compatibility promise, and a stale plugin or client
could send requests the service no longer understands. M1 moves the service to Lambdas, so the
client/service split has to stay clean.

## Decision

- **One PyPI distribution, `email-classify-filter`** (OD-122, OD-123), with two import packages:
  `ecf` (client) and `ecf_server` (service). import-linter keeps `ecf` from importing `ecf_server`,
  so the split survives into M1.
- **One product version for everything** (design plan "Versioning"): the package, the service and
  the plugin share it. The plugin ships inside the wheel and `ecf claude` renders it from there at
  each start (OD-271, OD-283), so it can't drift; the plugin's MCP server is the installed
  `ecf-mcp`, never resolved at runtime (OD-129).
- **Integer contract versions beside it**: `api_version` (socket API), `data_format` (export
  bundles) and `schema_version` (newest migration), with `min_client`, all in
  `ecf_server/data/release.json`; a test keeps them equal to the code (OD-375).
- **One install method**: `uv tool install email-classify-filter` (§17.1). `ecf upgrade` checks the
  new wheel's `release.json` and model pins before stopping anything, and works only on a `uv tool`
  install (OD-375, OD-380).
- **Releases** are `vX.Y.Z` tags only, built by CI with PyPI trusted publishing, reproducible and
  hash-verified (§17.4, ADR 0003). Publishing waits for `v1.0.0` (OD-310).

## Alternatives considered

- **Separate client and service distributions:** rejected (OD-122); two versions to match on one
  computer, for no v1 gain. M1 builds the service for Lambda from the same source.
- **The plugin from a git marketplace pinned to commit SHAs:** the design plan's choice, replaced
  by OD-271; it was a second release channel to keep in step with the package.
- **`uvx` resolving `ecf-mcp` at runtime:** rejected (OD-129); it would fetch code at each start,
  outside the hash-verified install.
- **Semantic-version ranges between parts:** not needed while all parts ship together; the
  integer contract versions cover what persists across versions (data and exports).

## Consequences

- Any change to any part is a new product version; there are no client-only releases.
- Upgrading one part upgrades all of them, and `ecf upgrade` stops the service to do it.
- `ecf doctor` checks that the package, `ecf-mcp` and the plugin are one version (§13.2, OD-305).
- Install methods other than `uv tool` (pip, an editable checkout) work for development but
  aren't upgraded by `ecf upgrade`.
