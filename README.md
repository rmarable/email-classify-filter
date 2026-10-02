# email-classify-filter (ecf)

ecf watches business mailboxes (for example `billing@` or `accounts-payable@`) over IMAP and classifies each message. It flags likely invoice and payment fraud with deterministic rules, and asks you to approve actions in Slack before anything risky happens. v1 runs on a single computer (macOS or Linux) with a local model or Claude, and needs no cloud infrastructure.

## Status

Milestones V1.0 (foundations), V1.1 (reading mail and fraud checks) and V1.2 (Slack and approvals) are done; V1.3 (the local model) is being finished. **Not ready for anyone else to use yet:** milestone tags record internal progress, not releases. The full README, with install and usage instructions, arrives in milestone V1.5.

## Where things are

- [`SPEC.md`](SPEC.md): the v1 specification (authoritative).
- [`docs/CURRENT-DESIGN-PLAN.md`](docs/CURRENT-DESIGN-PLAN.md): the latest design plan, including the roadmap (AWS mode, teams, remote access, always-on).
- [`docs/history/`](docs/history/): earlier design documents, kept for history.

## License

Apache License 2.0 with the Commons Clause restriction; see [`LICENSE`](LICENSE). This makes ecf source-available, not OSI open source: you may use and modify it, but not sell it or sell hosting or support for it. Fees for your own time, such as installation or training, are allowed.
