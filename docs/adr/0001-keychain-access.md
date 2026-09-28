# ADR 0001: Keychain access for the local service (macOS)

- **Status:** accepted (operator decision 2026-09-27, OD-163)
- **Context source:** SPEC §11.6, §12.2, §21.1 (V1.0 Keychain gate test, 2026-09-27)

## Context

The v1 local service keeps IMAP app passwords and Slack tokens in the macOS Keychain via `keyring`, and is the only writer of those items. The design assumed the Keychain would keep other processes from reading them silently, and named two mitigations in case it didn't: a private interpreter copy, or items with `kSecAttrAccessControl` user presence.

The gate test (macOS 27.0, uv 0.12.15, uv-managed CPython 3.12.14, keyring 25.7.0, a LaunchAgent stand-in) found:

- The service's write, its read after a restart and its read after `uv tool upgrade` are silent.
- A different script run with the **same interpreter binary** reads silently, and so does a **copy of that binary at another path**. The item's access rule trusts the interpreter by its code hash; uv's and Homebrew's Pythons are ad-hoc signed.
- A different interpreter (Homebrew's 3.12) and the `security` command are prompted and must enter the login keychain password.
- After the service's interpreter changes (3.12.14 → 3.12.13), the unattended service blocks on a dialog (139 s, then denied).
- With `SecKeychainSetUserInteractionAllowed(False)`, the same untrusted read fails in 0.02 s (-25293) instead of blocking.
- Creating a user-presence item fails with -34018 (missing entitlement) in both the data-protection and classic keychains.

## Decision

1. Accept, as a stated limit, that any process running ecf's Python interpreter binary can read ecf's Keychain secrets without a prompt.
2. The service turns Keychain user interaction off for its own process, so a read it isn't trusted for fails immediately; it then waits with "secret store needs you" and never blocks on a dialog.
3. The service records the interpreter's hash; `ecf doctor` and `ecf status` report when it has changed (built in V1.0). The foreground re-grant (prompts on, same interpreter binary; the operator enters the login password and chooses Always Allow) arrives with `ecf upgrade` in V1.5. Fallback if Always Allow doesn't persist: the service re-writes each secret after the operator re-enters it.

## Alternatives considered

- **Private interpreter copy:** rejected. A copy has the same code hash and is trusted like the original.
- **User-presence items (`kSecAttrAccessControl`):** not possible. They need a keychain entitlement that only a code-signed app can carry.
- **A code-signed helper app as the only Keychain user:** not pursued for v1. It needs an Apple Developer Program membership, adds Swift code and notarization, and its benefit is unverified (any process could still ask the helper).

## Consequences

- SPEC §12.2 and SECURITY.md state the limit plainly.
- Updating the Python under ecf requires one foreground re-grant; the service alerts rather than stalls.
- `SecKeychainSetUserInteractionAllowed` is a legacy API; if Apple removes it, this decision is revisited.
