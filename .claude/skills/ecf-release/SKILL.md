---
name: ecf-release
description: Release path for ecf (OD-492; SPEC §1.4). Use before any release, version bump, release candidate (rc) or release tag vX.Y.Z, to choose the fast or checked path and follow its steps.
---

# ecf release path

SPEC §1.4 is authoritative; this restates it. CLAUDE.md "Tags" still applies: create or push a tag only after the operator confirms and approves both the tag and the push.

- **Release path** (OD-492; SPEC §1.4), chosen by what the release changes, not by its version level. **Fast** unless it has a risky change: a migration; a gate-key change (pins, `prompt_digest`, classifier schema); a change to upgrade, rollback or Keychain code; a `data_format` or `api_version` change. Fast: one bump commit with the version, the CHANGELOG heading already dated, and README.md's Status line naming the new release, its date and a one-line summary from its CHANGELOG section (the README's install command takes the latest release, so it needs no edit; `test_readme_status_names_this_release` fails until Status is updated), and any README text that release's CHANGELOG section contradicts fixed; the bump runs `ecf models install` and the version tests (`uv run pytest tests/test_release*.py`), not the full macOS gate (the merges it releases passed it; name them in the bump's merge message); push `main` and the annotated tag together and watch the tag's release run, which runs the Linux checks before it builds (no separate wait for `main`'s CI); then one `ecf upgrade --to vX.Y.Z` on the test install (ecf1009). **Checked** (any risky change): the same, except that before tagging the wheel is built locally (`scripts/release_build.py`, reproducible, so byte-identical to the release's) and the test install runs `ecf upgrade --wheel <it>`, `ecf upgrade --to <previous>` and `--wheel` again; then the tag, its release run, and one `ecf upgrade --to vX.Y.Z` from the published release.
