# ADR 0012: One local service, SQLite, HTTP over a Unix socket

- **Status:** accepted (OD-093, OD-094, OD-155, 2026-09-26; OD-095, OD-101, OD-104, OD-166,
  OD-173, OD-183, 2026-09-27); implemented in V1.0
- **Context source:** SPEC §3.1, §11.1 to §11.5, §11.7; design plan
  (`docs/history/design-plan-2026-09-27.md`) §10a "Local mode"; `ecf_server/service.py`,
  `api.py`, `db.py`, `jobs.py`

## Context

v1 runs on one computer with no AWS (ADR 0002), but the same server code moves to Lambdas,
DynamoDB and SQS FIFO queues in M1. Several things must run whether or not anyone has a terminal
open: the 1-minute timer, mail checks with their leases, the Slack Socket Mode connection, step-up
dialogs, delayed sends and the job queues. The CLI, `ecf-mcp` and Claude Code sessions all need to
reach that state, and nothing else on the computer should.

## Decision

- **One process, `ecf-server local`** (OD-094), run by a launchd LaunchAgent on macOS or a systemd
  user unit on Linux, restarting on failure, with a crash-loop breaker and a single-instance
  `flock` (§11.1). It stands in for M1's Lambdas, timers and queues (design plan §10a). While the
  secret store is locked it waits rather than exiting (OD-095).
- **State in SQLite** in WAL mode, `synchronous=FULL` so a `sent` or consumed-grant record isn't
  lost on power loss; `BEGIN IMMEDIATE` for writes and no transaction held across a network or
  model call; normalized tables per entity rather than DynamoDB's key layout; SQLite 3.37 or later
  (OD-104, OD-166).
- **A job queue in SQLite** with SQS FIFO semantics: per (queue, address) only the oldest
  unfinished job runs, retries with backoff, dead-letter after 5 attempts (§11.3, OD-183).
- **HTTP over a Unix socket**: one Starlette app served by uvicorn on a socket ecf creates itself,
  0600 in a 0700 folder; clients use httpx over that socket (OD-101, §11.4). M1 wraps the same app
  with Mangum, so only the transport changes. The run folder holds the socket, CLI token and lock
  (OD-173).
- **Never on TCP**: the Claude Code telemetry receiver is a separate two-route app on loopback, so
  the main API is never reachable over TCP (§11.5).

## Alternatives considered

From the design plan:

- **AWS in v1** (Lambdas, DynamoDB, SQS, API Gateway): moved to M1 (ADR 0002); v1 maps each to a
  local equivalent (design plan §10a).
- **The Powertools router** for the routes: dropped (OD-101) for one Starlette app that runs both
  locally and on Lambda.
- **uvicorn's own Unix-socket setup:** not used; it applies 0o666 (verified 2026-09-27, uvicorn
  `server.py`), so ecf binds the socket itself.
- **A CLI with no background process, a TCP API, or a server database:** none recorded in the
  design plan or SPEC beyond the reasons above (the timers, Socket Mode and leases need a running
  process; the API stays off TCP).

## Consequences

- ecf runs only while the computer is on and awake; after a reboot the LaunchAgent starts only
  once you log in (§11.1).
- The socket's 0600 mode keeps out other OS users, not processes running as you (§12.2).
- A live copy of the database (Time Machine) isn't a consistent backup; the scheduled export is
  (§12.2, OD-311).
- M1 changes the transport and the storage adapter, not the routes or the rules.
- `ecf watch` runs the same service in the foreground and stops the unit while it does.
