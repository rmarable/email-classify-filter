# ADR 0020: v1.0.0 is macOS-only; Linux moves to milestone M5

- **Status:** accepted (operator decision 2026-10-04, OD-421, OD-422); amends ADR 0002
- **Context source:** SPEC §1.1, §1.2, §1.3, §1.5; `docs/roadmap/m5-linux.md` on branch `m5-linux`

## Context

v1 was planned for macOS and Linux (ADR 0002, OD-001), with Linux labelled unverified until
milestone V1.6 ran its real-service tests (OD-011). V1.6 needs Linux VMs, a desktop VM for polkit,
polkit itself (OD-224), and fixes for the Linux defects its plan review found. Nobody runs ecf on
Linux now, and V1.6 gated `v1.0.0`.

## Decision

- `v1.0.0` supports **macOS only**. Its milestones are V1.0-V1.5.
- V1.6 Linux verification becomes roadmap milestone **M5 Linux**, after M4. Its plan, decisions
  (OD-411 to OD-420) and review findings are kept on branch `m5-linux`, not merged.
- The Linux code (systemd user unit, Secret Service, `systemd-creds`, PAM) stays in the repo and in
  CI's unit tests, but is unsupported: `ecf doctor` warns on Linux, and the documents say so.

## Alternatives considered

- **Keep V1.6 before `v1.0.0`:** rejected for now; weeks of work with no current Linux user.
- **A reduced V1.6** (headless fixes only, no polkit or desktop): rejected; it would still delay
  `v1.0.0` and leave Linux half-verified.
- **Remove the Linux code:** rejected; it is built and tested with fakes, and M5 starts from it.

## Consequences

- ADR 0002's "macOS only: rejected" no longer holds for `v1.0.0`.
- Known Linux defects (`docs/roadmap/m5-linux.md`, R1-R11) stay unfixed until M5.
- `ms-v1.6-linux` is not used; M5's tag is `ms-m5-linux`.
- The branch `m5-linux` needs a rebase, and a check of its OD numbers, when M5 starts.
