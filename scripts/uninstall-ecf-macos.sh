#!/bin/sh
# Remove ecf (email-classify-filter) from a Mac: `ecf destroy` for each install, then ecf itself,
# then a list of what's left; offers to remove what install-ecf-macos.sh installed (uv, gh,
# Claude Code), default no. SPEC §17.4; operator decision 2026-10-16, OD-526.
#
#   sh uninstall-ecf-macos.sh [--dry-run]
#
# Your mailboxes, and the mail in them, are never touched.
set -eu

dry=0 failed=0
while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) dry=1; shift ;;
        -h|--help) sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $1 (see --help)" >&2; exit 2 ;;
    esac
done

BIN="$HOME/.local/bin"
ROOT="${ECF_HOME:-$HOME/Library/Application Support/ecf}"
case "$ROOT" in /*) ;; *) ROOT="$PWD/$ROOT" ;; esac
KEEP="$ROOT/_installer"
RECORD="$KEEP/install-record.json"
PATH="$BIN:$PATH"
export PATH

say() { printf '%s\n' "$*"; }
step() { printf '\n== %s\n' "$*"; }

ask() {
    if [ "$2" = y ]; then printf '%s [Y/n] ' "$1"; else printf '%s [y/N] ' "$1"; fi
    ans=""
    read -r ans || true
    case "$ans" in
        [Yy]*) return 0 ;;
        [Nn]*) return 1 ;;
        *) [ "$2" = y ] ;;
    esac
}

run() {
    if [ "$dry" = 1 ]; then say "  (dry run) $*"; else "$@"; fi
}

# record KEY: a string value from install-record.json, empty when absent
record() {
    [ -f "$RECORD" ] || return 0
    sed -n "s/^  \"$1\": \"\{0,1\}\([^\",]*\)\"\{0,1\},\{0,1\}\$/\1/p" "$RECORD"
}

# installs: data folders holding an ecf database, as `ecf destroy` counts them
installs() {
    [ -d "$ROOT" ] || return 0
    for d in "$ROOT"/*/; do
        [ -f "$d/ecf.db" ] && basename "$d"
    done
    return 0
}

# Read the record now: deleting the leftover folder below removes it.
rec_uv=$(record uv) rec_gh=$(record gh) rec_claude=$(record claude_code)
rec_path=$(record zprofile_path_line) had_record=0
[ -f "$RECORD" ] && had_record=1

say "This removes ecf (email-classify-filter) from this Mac for $(id -un)."
say "Your mailboxes and the mail in them are not touched."
[ "$dry" = 1 ] && say "Dry run: nothing is changed."

step "ecf installs"
list=$(installs)
if [ -z "$list" ]; then
    say "No ecf install found in $ROOT."
else
    say "Found: $(printf '%s' "$list" | tr '\n' ' ')"
    if ! command -v ecf >/dev/null 2>&1; then
        say "ecf itself is missing, so these can't be destroyed properly (their secrets stay in the"
        say "Keychain). Reinstall ecf with install-ecf-macos.sh, then run this again."
        exit 1
    fi
    for name in $list; do
        say ""
        say "Destroying $name: ecf stops its service, archives its Slack channels and deletes its"
        say "secrets and data. It asks you to type the name and confirm with Touch ID or your password."
        say "With a Slack configuration token it also deletes the Slack app (otherwise its bot is"
        say "only turned off; you can delete the app at api.slack.com/apps later)."
        if ! ask "Destroy $name now?" y; then
            say "Kept $name. ecf itself stays while any install is left."
            continue
        fi
        if ask "Do you have a Slack configuration token to delete the Slack app too?" n; then
            run ecf --install "$name" destroy --config-token || {
                say "ecf destroy didn't finish for $name."; failed=1; }
        else
            run ecf --install "$name" destroy || { say "ecf destroy didn't finish for $name."; failed=1; }
        fi
    done
fi

step "ecf itself"
left=$(installs)
if [ -n "$left" ] && [ "$dry" = 0 ]; then
    say "Still installed: $(printf '%s' "$left" | tr '\n' ' '). ecf itself stays."
    say "Run this again when you want to remove those too."
    exit "$failed"
fi
if command -v uv >/dev/null 2>&1 && uv tool list 2>/dev/null | grep -q "^email-classify-filter "; then
    run uv tool uninstall email-classify-filter
else
    say "ecf isn't installed with uv tool (nothing to remove)."
fi

step "What's left"
say "These stay until you remove them yourself:"
if [ -d "$ROOT/destroyed" ]; then
    for f in "$ROOT"/destroyed/*.json; do
        [ -f "$f" ] && say "  - the record of a destroy (no secrets): $f"
    done
fi
say "  - your ecf backup folder, if you set one up (backups are encrypted with your backup key)"
say "  - any .ecfcorpus files you made, and their labels files"
say "  - each mailbox's app password at your email provider: revoke it there"
say "    (Gmail: https://myaccount.google.com/apppasswords)"
say "  - the Slack app, unless you gave a configuration token (api.slack.com/apps)"
if [ -d "$ROOT" ] && [ "$ROOT" != / ] && [ "$ROOT" != "$HOME" ] && [ "$ROOT" != "$HOME/" ]; then
    if ask "Delete ecf's leftover folder $ROOT (destroy records and kept release files)?" n; then
        run rm -rf "$ROOT"
    fi
fi

step "Tools install-ecf-macos.sh may have installed"
remove_tool() { # name how
    case "$2" in
        homebrew)
            case "$1" in
                claude) run brew uninstall --cask claude-code ;;
                *) run brew uninstall "$1" ;;
            esac ;;
        release)
            case "$1" in
                uv) run rm -f "$BIN/uv" "$BIN/uvx"
                    say "uv's download cache stays (other uv projects share it); \`uv cache dir\` shows where." ;;
                gh) run rm -f "$BIN/gh" ;;
            esac ;;
        official) run rm -f "$BIN/claude"; run rm -rf "$HOME/.local/share/claude" ;;
    esac
}
if [ "$had_record" = 0 ]; then
    say "No install record, so nothing here was installed by install-ecf-macos.sh (or it's gone)."
    say "uv, gh and Claude Code stay; each one's own docs say how to remove it."
fi
for tool in uv gh claude; do
    case "$tool" in uv) how=$rec_uv ;; gh) how=$rec_gh ;; *) how=$rec_claude ;; esac
    case "$how" in
        homebrew|release|official)
            say "$tool was installed by install-ecf-macos.sh ($how). Other programs may use it."
            if ask "Remove $tool?" n; then remove_tool "$tool" "$how"; fi ;;
        already) say "$tool was already here before ecf; leaving it." ;;
        *) ;;
    esac
done
if [ "$rec_path" = true ]; then
    say "install-ecf-macos.sh added a PATH line (marked \"added by install-ecf-macos.sh\") to"
    say "$HOME/.zprofile; remove it by hand if nothing else needs $BIN."
fi
say ""
say "Done."
