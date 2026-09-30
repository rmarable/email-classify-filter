# Contributing

How to work on ecf. `SPEC.md` owns the design; this file is how-to only. The documentation style
rule and the commit rules are in `CLAUDE.md`.

## Requirements

- macOS or Linux (Linux is unverified until milestone V1.6).
- Python 3.12 or newer, managed by [uv](https://docs.astral.sh/uv/) (`.python-version` pins 3.12).
- v1 needs no AWS and no CDK. `infra/` (arriving in M1) is the AWS infrastructure source.
- A container engine for the IMAP tests (a Dovecot container, OD-186): on macOS
  `brew install colima docker && brew services start colima`; on Linux, Docker Engine. Without one,
  the `imap` tests are skipped locally (they always run in CI).
- On macOS, GNU sed as `gsed` (`brew install gnu-sed`) for scripted edits: the built-in BSD sed
  doesn't accept GNU syntax such as `\|` alternation or `sed -i` without a suffix argument.
  Nothing in the build or tests calls sed; Linux's sed is already GNU sed.

## Set up

```sh
uv sync            # creates .venv from uv.lock, including dev tools and the [eval] extra
```

## Commands

```sh
uv run ruff check . && uv run ruff format --check .
uv run pyright                                  # strict everywhere (src, tests, scripts)
uv run lint-imports                             # the client (ecf) never imports ecf_server
uv run pytest                                   # everything this OS can run
uv run pytest -m "not macos"                    # what CI runs (Linux)
uv run pytest -m imap                           # the IMAP adapter against Dovecot
uv run pytest tests/test_state_machine.py::test_edges_match_spec   # one test
uv run python scripts/check_licenses.py         # dependency license allow-list
uv run python scripts/check_licenses.py --markdown   # regenerate the table below
uv build                                        # wheel + sdist
```

## Layout

| Path | What |
|---|---|
| `src/ecf/` | the client: CLI (`ecf`), socket client, `ecf-mcp` (V1.4), shared models, schema compiler, error codes, eval tooling. **No rules, policy, facts or state transitions.** |
| `src/ecf_server/` | the local service (`ecf-server`): state, job queue, rules, secret stores, API, service process |
| `src/ecf_server/migrations/` | SQLite migrations, applied in order |
| `tests/` | pytest; `conftest.py` has the shared fixtures, including real service processes |
| `tests/eval/synthetic/` | the synthetic eval set (see `GENERATE-FAKE-TESTING-EMAILS.md`) |
| `scripts/` | maintenance scripts |
| `docs/adr/` | decision records; `docs/history/` holds dated design plans |

## Rules of the code

- **Trust boundary:** facts, triggers, rules, policy and state transitions live only in
  `ecf_server`. import-linter enforces that `ecf` never imports `ecf_server`.
- **One status writer:** only `ecf_server.items.transition()` writes `items.status`, and only
  `create_item()` inserts items. SQLite's authorizer refuses anything else at runtime (OD-181).
- **No email content in logs.** Log IDs and counts. The no-content processor (`ecf/log.py`)
  redacts known content fields and cuts long strings, but don't rely on it.
- **All YAML** goes through `ecf.yamlio` (safe loader, YAML 1.2, duplicate keys rejected).
- **Strict typing everywhere.** Untyped libraries get a small typed wrapper (e.g.
  `ecf_server/secretstore/macos_interaction.py`); a suppression names the exact pyright rule.
- **Errors** are `ecf.errors` classes; each code maps to an HTTP status, CLI exit code and Slack text.
- **Tests:** one fake per port. Tests that need macOS are marked `macos`; tests that touch the real
  Keychain or launchd use `ecf-test-*` names and remove what they create. Service-process tests
  use a short `/tmp` folder because socket paths are limited to 104 bytes on macOS, and start
  services only through `spawn`/`start_service` and stop them with `stop` (`tests/conftest.py`);
  the run fails if any service outlives its test.

## Workflow

1. Work on a branch (or worktree), never directly on `main`.
2. Run the checks above. Push: GitHub CI runs the Linux checks (free minutes; no macOS runners).
3. **macOS merge gate:** before any merge to `main`, run the full suite on a Mac
   (`uv run pytest`) and put the result in the merge commit message, e.g.
   `macOS tests: 212 passed (macOS 27.0, 2026-10-02)`.
4. Commit messages end with exactly one trailer: `Co-Authored-By: Claude <noreply@anthropic.com>`
   when Claude contributed (see `CLAUDE.md`).
5. A decision made while building goes in an ADR (`docs/adr/`), or, for a small one, in the relevant
   SPEC section marked with its date and "operator decision" and an `OD-nnn` number.

## Environments

- **Dev:** `uv run ecf-server dev` runs a throwaway service (a `/tmp` folder, a fake clock you move
  with `POST /v1/dev/clock?advance=<seconds>`, a fake chat, secrets in memory only). It prints the
  `ECF_SOCKET=...` line that points the CLI at it. The mail containers (Dovecot, Postfix +
  OpenDMARC) join in V1.1.
- **Test install:** a real local install with `--install test` on a test Slack workspace and a
  test mailbox that receives only synthetic mail (from V1.2).
- **Prod:** your real install; it only ever receives tagged releases.

Real-service tests (Keychain, mail, Slack, step-up) run only with the operator's go-ahead; their
code is throwaway and stays out of the repo, and anything they create is removed afterwards.
Results are recorded in SPEC §21.1.

## Eval tooling

```sh
uv run ecf eval new-case bec-002 --template bec   # also: injection, header, control
uv run ecf eval build                             # hygiene scan, then .eml + labels.jsonl
uv run ecf eval show bec-002                      # headers, text, attachments
uv run ecf eval compare a.json b.json             # paired comparison of two result files
```

Details, rules and the card format: `GENERATE-FAKE-TESTING-EMAILS.md`.

## Releases and tags

- **Milestone tags** `ms-v1.0-foundations` … record internal progress; they don't mean the software
  is ready for anyone else. **Release tags** `vX.Y.Z` are the only published ones.
- A tag is created and pushed only after the operator approves both.
- `CHANGELOG.md` is kept as you go: each behavior-changing commit adds a line under the next
  tag's heading; at tagging the heading gets the tag's date. The full rule is in `CLAUDE.md`.
- Publishing (PyPI trusted publishing, the plugin marketplace, `THIRD_PARTY_NOTICES`) is built with
  the first release.

## Third-party licenses

This table is owned here (SPEC §17.5) and generated from `uv.lock` with
`uv run python scripts/check_licenses.py --markdown`. The allow-list is MIT, MIT-0, BSD, ISC,
Apache-2.0, zlib, MIT-CMU and PSF-2.0; MPL-2.0 only for development tools, plus the named runtime
exception for unmodified `certifi` (OD-128); Unicode-3.0 only for the shipped `confusables.txt`
data file (OD-188; `src/ecf_server/data/unicode/`, with its license and provenance). A package that exists on only one platform, or whose metadata the script can't
read, needs a reviewed entry in the script, checked at its locked version (readable metadata always
wins). Development-only tools
(pytest, ruff, pyright, import-linter, hypothesis) never ship and aren't listed.

Runtime dependencies (generated 2026-09-28):

| Package | Version | License | Installed on |
|---|---|---|---|
| `annotated-doc` | 0.0.5 | MIT | all platforms |
| `annotated-types` | 0.8.0 | MIT | all platforms |
| `anyio` | 4.15.1 | MIT | all platforms |
| `certifi` | 2026.7.22 | MPL-2.0 | all platforms |
| `cffi` | 2.1.1 | MIT-0 | all platforms |
| `charset-normalizer` | 3.5.1 | MIT | `[eval]` extra only |
| `click` | 8.5.0 | BSD-3-Clause | all platforms |
| `colorama` | 0.4.6 | BSD | Windows |
| `cryptography` | 50.0.1 | Apache-2.0 OR BSD-3-Clause | Linux |
| `dkimpy` | 1.1.8 | Zlib | all platforms |
| `dnspython` | 2.8.0 | ISC | all platforms |
| `h11` | 0.16.0 | MIT | all platforms |
| `httpcore` | 1.0.9 | BSD-3-Clause | all platforms |
| `httpx` | 0.28.1 | BSD-3-Clause | all platforms |
| `idna` | 3.20 | BSD-3-Clause | all platforms |
| `imapclient` | 4.1.0 | BSD-3-Clause | all platforms |
| `jaraco-classes` | 3.4.0 | MIT | all platforms |
| `jaraco-context` | 6.1.2 | MIT | all platforms |
| `jaraco-functools` | 4.6.0 | MIT | all platforms |
| `jeepney` | 0.9.0 | MIT | Linux (via `keyring`) |
| `keyring` | 25.7.0 | MIT | all platforms |
| `markdown-it-py` | 4.2.0 | MIT | all platforms |
| `mdurl` | 0.1.2 | MIT | all platforms |
| `more-itertools` | 11.1.0 | MIT | all platforms |
| `pillow` | 12.3.0 | MIT-CMU | `[eval]` extra only |
| `pycparser` | 3.0 | BSD-3-Clause | all platforms |
| `pydantic` | 2.13.5 | MIT | all platforms |
| `pydantic-core` | 2.46.5 | MIT | all platforms |
| `pygments` | 2.21.0 | BSD-2-Clause | all platforms |
| `pynacl` | 1.6.2 | Apache-2.0 | all platforms |
| `pyobjc-core` | 12.2.2 | MIT | macOS |
| `pyobjc-framework-cocoa` | 12.2.2 | MIT | macOS |
| `pyobjc-framework-security` | 12.2.2 | MIT | macOS |
| `pywin32-ctypes` | 0.2.3 | BSD-3-Clause | Windows |
| `reportlab` | 5.0.1 | BSD | `[eval]` extra only |
| `rich` | 15.0.0 | MIT | all platforms |
| `ruamel-yaml` | 0.19.1 | MIT | all platforms |
| `secretstorage` | 3.5.0 | BSD-3-Clause | Linux |
| `shellingham` | 1.5.4 | ISC | all platforms |
| `starlette` | 1.7.0 | BSD-3-Clause | all platforms |
| `structlog` | 26.1.0 | MIT OR Apache-2.0 | all platforms |
| `typer` | 0.27.2 | MIT | all platforms |
| `typing-extensions` | 4.16.0 | PSF-2.0 | all platforms |
| `typing-inspection` | 0.4.4 | MIT | all platforms |
| `uvicorn` | 0.54.0 | BSD-3-Clause | all platforms |
