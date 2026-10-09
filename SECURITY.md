# Security

This file covers reporting vulnerabilities, supported versions, and a summary of ecf's security
model. `SPEC.md` §12 owns the threat model, the stated limits and the privacy statement; where this
file and SPEC differ, SPEC is right.

## Reporting a vulnerability

Email reports to **rodney.marable@gmail.com**. Don't open a public issue, pull request or
discussion for a vulnerability. Include:

- the ecf version (`ecf version`) and macOS version;
- what an attacker can do, and what they need first (for example a Slack account, a sent email,
  or code running as you);
- steps or a proof of concept that reproduce it;
- whether it is already public, and how you'd like to be credited.

Check SPEC §12.2 (summarized under "Stated limits" below) first: those are known limits, not
vulnerabilities.

## Supported versions

The latest release only; fixes ship as a new release. `v1.0.0` is supported until `v2.0.0` ships,
and stops being supported then. Milestone tags (`ms-…`) and release candidates (`vX.Y.Z-rcN`, such as
`v2.0.0-rc1`) record progress and are not supported. Versioning: SPEC §1.4 (OD-480).

## The security model in brief

- **One computer, one person.** ecf's service, state and secrets share your user account, so
  whoever controls that account controls ecf. Full-disk encryption and a screen lock are required;
  `ecf doctor` checks FileVault on macOS and LUKS on Linux where it can.
- **The service decides; models and clients don't.** Fraud and regulator rules, policy, approvals
  and state changes run only in the local service. Model output is checked and can raise risk, but
  never alone hides mail or approves anything. The MCP server has no approval or admin tools (ADR
  0009).
- **Risky actions need you at the computer.** Every send, every irreversible action, hiding fraud
  or regulator mail, and answers on payment or fraud emails need step-up: Touch ID or your
  password, checked by the service and bound to that one action (ADR 0015). Sends on `high`
  addresses then wait 10 minutes, with Cancel.
- **Sending is off by default** per address (ADR 0007).
- **Credentials** (app passwords, Slack tokens) live only in the OS secret store, written only by
  the service; never in files, environment variables or the repository (ADR 0013).
- **Sender authentication** is ecf's own DKIM and DMARC check; your provider's verdict is ignored
  (ADR 0011).

SPEC §12.1 lists each threat with its controls.

## If your Slack account is compromised

ecf accepts clicks only from your Slack member ID. Someone using your Slack account can:

- approve an action that needs no step-up (reversible ones: labels, flags, archive, drafts);
- reject any approval, including one queued for step-up;
- cancel a send in its 10-minute delay;
- answer ecf's questions on emails that aren't about payment or fraud;
- undo unverified-sender labels;
- dismiss an item with no payment, fraud or regulator signal;
- pause and resume addresses;
- see short excerpts (shown only to the person who clicks).

They can't send mail, take an irreversible action, hide fraud or regulator email, answer on
payment or fraud emails, or confirm a sender: those wait for step-up at your computer. What's left
is denial (rejecting, cancelling, pausing) and step-up requests you didn't start, each of which
names its action in the dialog. Source: SPEC §9.9.

## Stated limits

ecf doesn't protect against these (SPEC §12.2 has the details and measurements):

- Any process that runs ecf's Python interpreter can read ecf's secrets from the macOS Keychain
  without a prompt, for example your own scripts or a coding agent using the same uv-managed
  Python.
- Anyone who controls your OS account controls ecf, including a coding agent running as you.
- Step-up confirms your intent inside one OS account; it isn't a boundary against malware running
  as you.
- MCP profiles are hygiene, not a boundary.
- Any process on this computer that can bind Ollama's port (127.0.0.1:11434) or write `~/.ollama`
  can answer as the local model; ecf checks the model's digest and that Ollama listens only on
  loopback, not the server itself.
- Local web pages can call Ollama, which by default allows local origins; ecf's digest check stops
  a swapped model from being used.
- DKIM key lookups use ordinary DNS, which can be forged on a hostile network. Opt-in DNS over
  HTTPS is in the design (OD-050) but not built yet.
- Time Machine or another live copy of the data folder is not a backup; the scheduled export is.
- Building or reading a backup needs memory a few times its size.
- Slack keeps what ecf posts under Slack's own retention.
- Gmail: ecf reads INBOX only, so mail Gmail files in Spam isn't checked; it can't check that the
  owner of another person's Google account agreed to it being watched; impersonation detection
  knows only the names and addresses listed in `org_addresses`. Setup: `docs/gmail-setup.md`.

## Linux

Linux isn't supported in `v1.0.0`, which is macOS-only (OD-422, ADR 0020). The Linux code (Secret
Service on desktops, `systemd-creds` on headless machines, PAM for step-up) is in the repo and CI runs
its unit tests with fakes, but no real-service test has run on Linux. Roadmap milestone M5 verifies
it, adds polkit step-up and fixes the known Linux defects; this section is finalized then.
