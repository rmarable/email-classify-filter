#!/bin/sh
# Claude Code SessionStart hook: list Codex branches (codex/*) not yet merged into main, each with
# the "Handoff:" block from its newest commit, so Claude can tell the operator what's waiting.
# Reads local refs only (Codex worktrees share this repository's refs); no fetch, no writes.
# AGENTS.md "Claude Code and Codex"; ADR 0033.
set -eu

cd "${CLAUDE_PROJECT_DIR:-.}"
git rev-parse --verify -q main >/dev/null || exit 0

out=""
for b in $(git for-each-ref --format='%(refname:short)' refs/heads/codex/); do
    if git merge-base --is-ancestor "$b" main; then
        continue
    fi
    handoff=$(git log -1 --format=%B "$b" | sed -n '/^Handoff:/,$p' | grep -v '^Co-Authored-By:' || true)
    [ -n "$handoff" ] || handoff="(no Handoff block in the newest commit: work in progress)"
    out="$out
- $b ($(git log -1 --format='%h %cs' "$b"), $(git rev-list --count "main..$b") commits ahead of main)
$handoff
"
done

[ -n "$out" ] || exit 0
printf 'Codex branches not yet merged into main (AGENTS.md "Claude Code and Codex"). Tell the operator about each one marked "Handoff: ready" and ask before acting on any of them.\n%s' "$out"
