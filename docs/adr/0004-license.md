# ADR 0004: Apache-2.0 with the Commons Clause

- **Status:** accepted (operator decision 2026-09-26, OD-145; `LICENSE` committed 2026-09-27)
- **Context source:** SPEC §1.6; `LICENSE`; design plan §17 (commercial licensing, commercialization)

## Context

The operator wants others to be able to read, use and modify ecf, but not to sell it or sell
hosting or support for it, and wants to keep the option of selling commercial licenses.

## Decision

`LICENSE` is the Apache License 2.0 with the Commons Clause restriction, modeled on the operator's
ParallelClusterMaker license, "Copyright 2026 Rodney Marable", Software: email-classify-filter. A
licensor clarification narrows the restriction: fees for a person's time (installation,
configuration, training, support on the customer's behalf) are allowed; fees for access to the
software are not.

## Alternatives considered

- **Plain Apache-2.0 (OSI open source):** rejected; allows anyone to sell or host it.
- **A copyleft license (GPL/AGPL):** not chosen; it doesn't stop selling, and it complicates a
  later commercial license.

## Consequences

- ecf is source-available, not OSI open source, which may reduce community trust.
- Accepting outside contributions would need a contributor agreement to keep relicensing rights.
- Every dependency must permit this (SPEC §17.5; the license check in CI).
- Commercial licensing and commercialization are roadmap items.
