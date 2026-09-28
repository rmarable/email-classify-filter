# Changelog

One entry per tag, newest first, kept up to date as changes are committed (rule: `CLAUDE.md`,
Changelog). Milestone tags (`ms-…`) record internal progress and are not releases (ADR 0003).

## ms-v1.0-foundations (2026-09-27)

Foundations for v1 single-user local mode. Nothing here processes mail yet.

- Packaging: one distribution, `email-classify-filter`, with the client (`ecf`) and the service
  (`ecf_server`); commands `ecf`, `ecf-server`, `ecf-mcp` (a stub until V1.4).
- CI on Linux (ruff, strict pyright, import-linter, tests, license allow-list, build); macOS tests
  run locally before merging (merge gate).
- IDs, the error table (HTTP status, exit code, Slack text), and no-content logging.
- The item state machine as data, with `transition()` as the only status writer.
- SQLite state (21 tables, STRICT, WAL, synchronous=FULL) and the per-address FIFO job queue.
- Schema v1 compiler, rules engine with the starter rules, reply templates.
- Secret stores: Keychain (prompts off in the service), Secret Service and `systemd-creds`
  (Linux, unverified until V1.6); interpreter tracking for re-grants.
- The local service: instance lock, 0600 Unix socket, timer tick, watchdog, crash-loop breaker;
  launchd and systemd units; `ecf service install|uninstall|start|stop|restart|status`.
- `ecf status` and `ecf doctor`.
- `ecf claude` wrapper skeleton and session profile tokens.
- `ecf-server dev`: a throwaway dev service with a fake clock and fake chat.
- Synthetic eval tooling: case cards, a byte-identical `.eml` builder, fake PDF invoices, the
  hygiene scan, metrics, and `ecf eval new-case|build|show|compare`; 8 starter cards.
- Documents: SPEC, CLAUDE.md, CONTRIBUTING, GENERATE-FAKE-TESTING-EMAILS, ADRs 0001-0004;
  the changelog rule.
- Real-service test: the V1.0 Keychain gate (ADR 0001).
