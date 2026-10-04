# ADR 0018: `ecf-mcp` is a stdio server on MCP Python SDK v2, pinned exactly

- **Status:** accepted (design plan, reviewed by the operator 2026-09-26/27; OD-088, 2026-09-26;
  OD-129, 2026-09-27); implemented in V1.4
- **Context source:** SPEC §10.4, §17.1, §19; design plan
  (`docs/history/design-plan-2026-09-27.md`) MCP notes and the superseded list ("Packaging");
  `ecf/mcp_server.py`, `ecf/mcp_stdio.py`, `pyproject.toml`

## Context

Claude reaches ecf through MCP: in v1 the `ecf claude` session's main session and its subagents
call `ecf-mcp`, which forwards each call to the local service over the Unix socket (§10.4). M3 adds
a remote MCP server for claude.ai on Lambda. The Python SDK had a major release while v1 was being
designed: v2.0.0 on 2026-07-28 (verified 2026-09-26, github.com/modelcontextprotocol/python-sdk),
which renamed `FastMCP` to `MCPServer`, moved transport options to `run()` and
`streamable_http_app()`, and still serves 2025-era clients (G1-28).

## Decision

- **SDK v2, pinned exactly** (§10.4): `mcp==2.2.0` (released 2026-09-07, the latest on PyPI when
  checked 2026-10-02). `tools/list` snapshot tests cover spec revisions 2025-11-25 and 2026-07-28,
  and a canary runs against both (§19).
- **stdio transport in v1:** `ecf-mcp --stdio` sends all logging to stderr before importing
  anything else, and a test asserts stdout carries only JSON-RPC (§10.4). It reaches the service
  with async httpx over the Unix socket; it holds no rules, policy or state.
- **The plugin runs the already-installed `ecf-mcp`** from the hash-verified package, with no `uvx`
  resolution at runtime (OD-129; ADR 0008).
- **Each WORK call has a hard deadline of 115 s**, a 10 s timeout per service request, and stops
  starting requests at 100 s (OD-088).
- Tool names match `^[a-z_]{1,64}$`, carry `readOnlyHint`/`destructiveHint`, and return
  email-derived fields inside a labelled untrusted-data wrapper (§10.4; profiles: ADR 0019).

## Alternatives considered

- **SDK v1 idioms:** listed as superseded in the design plan ("Packaging"); v2 was current when
  v1 was designed and is what M3's Lambda pattern was verified against (design plan, python-sdk
  #3121).
- **A version range instead of an exact pin:** not recorded as considered; the exact pin follows
  from the snapshot tests, which fix the advertised tool list per SDK release (§19).
- **`uvx` resolving the server at each start:** rejected (OD-129).

## Consequences

- Every SDK upgrade is a deliberate change: the pin moves, the `tools/list` snapshots are
  re-checked against both spec revisions, and it ships in an ecf release.
- M3's remote server is planned on the same SDK major version, over Streamable HTTP (design
  plan, M3).
- A stray `print` to stdout would corrupt the protocol; the stdout-only test guards it.
