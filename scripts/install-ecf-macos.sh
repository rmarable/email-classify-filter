#!/bin/sh
# Install ecf (email-classify-filter) on a Mac: check the Mac, install what ecf needs (uv, gh,
# Claude Code), download an ecf release, verify it, install it with `uv tool`, then hand over to
# `ecf init`. Removing it again: uninstall-ecf-macos.sh. SPEC §17.4; operator decision
# 2026-10-16, OD-526.
#
#   sh install-ecf-macos.sh [--version vX.Y.Z] [--homebrew] [--python 3.12|3.13] [--yes]
#                           [--dry-run] [--allow-no-filevault]
#
# Nothing here needs an administrator password, except Homebrew itself with --homebrew.
set -eu

REPO="rmarable/email-classify-filter"
DIST="email_classify_filter"
MIN_CLAUDE="2.1.295"            # src/ecf/claude_wrapper.py MIN_CLAUDE
ANTHROPIC_TEAM="Q6L2SF6YDW"     # Developer ID Application: Anthropic PBC
GITHUB="https://github.com"

version_arg="" homebrew=0 python="3.12" yes=0 dry=0 allow_no_fv=0
while [ $# -gt 0 ]; do
    case "$1" in
        --version) version_arg="${2:?--version needs a tag, e.g. v3.0.2}"; shift 2 ;;
        --homebrew) homebrew=1; shift ;;
        --python) python="${2:?--python needs 3.12 or 3.13}"; shift 2 ;;
        --yes) yes=1; shift ;;
        --dry-run) dry=1; shift ;;
        --allow-no-filevault) allow_no_fv=1; shift ;;
        -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $1 (see --help)" >&2; exit 2 ;;
    esac
done
case "$python" in
    3.12|3.13) ;;
    *) echo "--python takes 3.12 or 3.13; ecf isn't tested on $python" >&2; exit 2 ;;
esac

BIN="$HOME/.local/bin"
ROOT="${ECF_HOME:-$HOME/Library/Application Support/ecf}"
KEEP="$ROOT/_installer"         # not a valid install name, so ecf never takes it for one
RECORD="$KEEP/install-record.json"
ORIG_PATH="$PATH"
PATH="$BIN:$PATH"
export PATH

say() { printf '%s\n' "$*"; }
step() { printf '\n== %s\n' "$*"; }
die() { printf 'stopped: %s\n' "$*" >&2; exit 1; }

# ask "question" y|n: the answer, or the default with --yes or at the end of input
ask() {
    if [ "$yes" = 1 ]; then [ "$2" = y ]; return; fi
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

# ver_ge A B: version A >= version B (dotted numbers)
ver_ge() {
    awk -v a="$1" -v b="$2" 'BEGIN {
        na = split(a, x, "."); nb = split(b, y, ".")
        for (i = 1; i <= (na > nb ? na : nb); i++) {
            if ((x[i] + 0) > (y[i] + 0)) exit 0
            if ((x[i] + 0) < (y[i] + 0)) exit 1
        }
        exit 0 }'
}

sha256_of() { shasum -a 256 "$1" | awk '{print $1}'; }

# latest_tag OWNER/REPO: the tag GitHub's "latest release" page redirects to (no API, no login)
latest_tag() {
    url=$(curl -fsSLo /dev/null -w '%{url_effective}' "$GITHUB/$1/releases/latest") ||
        die "couldn't reach GitHub to find the latest release of $1"
    tag=${url##*/tag/}
    [ -n "$tag" ] && [ "$tag" != "$url" ] || die "GitHub didn't say which release of $1 is latest"
    printf '%s' "$tag"
}

TMP=$(mktemp -d "${TMPDIR:-/tmp}/install-ecf.XXXXXX")
trap 'rm -rf "$TMP"' EXIT INT TERM

# How each prerequisite got here, for install-record.json: release, homebrew, official or "already"
how_uv="already" how_gh="already" how_claude="already" path_line=0

say "This installs ecf (email-classify-filter) for $(id -un) on this Mac."
[ "$dry" = 1 ] && say "Dry run: nothing is downloaded or changed."

step "Checking this Mac"
[ "$(uname -s)" = Darwin ] || die "this script is for macOS; ecf doesn't support $(uname -s) yet"
case "$(uname -m)" in
    arm64) arch_uv="aarch64" arch_gh="arm64" ;;
    x86_64) arch_uv="x86_64" arch_gh="amd64" ;;
    *) die "unsupported processor $(uname -m)" ;;
esac
[ "$(id -u)" != 0 ] || die "run this as yourself, not as root or with sudo"
say "macOS $(sw_vers -productVersion), $(uname -m)"

if fdesetup status 2>/dev/null | grep -q "FileVault is On"; then
    say "FileVault (disk encryption): on"
elif [ "$allow_no_fv" = 1 ]; then
    say "FileVault (disk encryption): OFF; continuing because of --allow-no-filevault"
else
    die "FileVault (disk encryption) is off. ecf keeps your mail settings and history on this
Mac, so it needs the disk encrypted. Turn it on in System Settings > Privacy & Security >
FileVault, then run this again (or pass --allow-no-filevault to go on anyway)."
fi

# sysadminctl prints "screenLock delay is immediate" (or a number of seconds) on stderr
lock=$(sysadminctl -screenLock status 2>&1 || true)
case "$lock" in
    *"screenLock delay is"*) say "Screen lock: on (${lock##*screenLock delay is })" ;;
    *) say "Screen lock: couldn't confirm it's on. Turn it on in System Settings > Lock Screen
(\"Require password after screen saver begins or display is turned off\")." ;;
esac

step "What ecf needs"
if [ "$homebrew" = 1 ]; then
    command -v brew >/dev/null 2>&1 ||
        die "--homebrew needs Homebrew, which isn't installed. See https://brew.sh, or run this
again without --homebrew to install from each tool's own release files."
    say "Using Homebrew for uv, gh and Claude Code."
fi

install_uv() {
    if [ "$homebrew" = 1 ]; then run brew install uv; how_uv="homebrew"; return; fi
    tag=$(latest_tag astral-sh/uv)
    name="uv-$arch_uv-apple-darwin.tar.gz"
    base="$GITHUB/astral-sh/uv/releases/download/$tag"
    say "Downloading uv $tag from astral-sh/uv's GitHub releases"
    if [ "$dry" = 1 ]; then say "  (dry run) download $name, check it, put uv in $BIN"; return; fi
    curl -fsSL -o "$TMP/$name" "$base/$name" || die "couldn't download $name"
    curl -fsSL -o "$TMP/$name.sha256" "$base/$name.sha256" || die "couldn't download $name.sha256"
    want=$(awk '{print $1}' "$TMP/$name.sha256")
    [ "$(sha256_of "$TMP/$name")" = "$want" ] ||
        die "$name doesn't match its published sha256; nothing was installed"
    mkdir -p "$TMP/uv" "$BIN"
    tar -xzf "$TMP/$name" -C "$TMP/uv"
    for f in uv uvx; do
        install -m 0755 "$TMP/uv/uv-$arch_uv-apple-darwin/$f" "$BIN/$f"
    done
    how_uv="release"
}

install_gh() {
    if [ "$homebrew" = 1 ]; then run brew install gh; how_gh="homebrew"; return; fi
    tag=$(latest_tag cli/cli)
    v=${tag#v}
    name="gh_${v}_macOS_${arch_gh}.zip"
    base="$GITHUB/cli/cli/releases/download/$tag"
    say "Downloading gh $tag from cli/cli's GitHub releases"
    if [ "$dry" = 1 ]; then say "  (dry run) download $name, check it, put gh in $BIN"; return; fi
    curl -fsSL -o "$TMP/$name" "$base/$name" || die "couldn't download $name"
    curl -fsSL -o "$TMP/gh-sums" "$base/gh_${v}_checksums.txt" || die "couldn't download gh's checksums"
    want=$(awk -v n="$name" '$2 == n {print $1}' "$TMP/gh-sums")
    [ -n "$want" ] || die "gh's checksums don't list $name"
    [ "$(sha256_of "$TMP/$name")" = "$want" ] ||
        die "$name doesn't match its published sha256; nothing was installed"
    mkdir -p "$TMP/gh" "$BIN"
    unzip -q "$TMP/$name" -d "$TMP/gh"
    install -m 0755 "$TMP/gh/gh_${v}_macOS_${arch_gh}/bin/gh" "$BIN/gh"
    how_gh="release"
}

claude_version() { claude --version 2>/dev/null | awk '{print $1}'; }

install_claude() {
    if [ "$homebrew" = 1 ]; then
        run brew install --cask claude-code
        how_claude="homebrew"
    else
        say "Downloading Claude Code's installer from claude.ai"
        if [ "$dry" = 1 ]; then say "  (dry run) run Anthropic's installer (claude.ai/install.sh)"; return; fi
        curl -fsSL -o "$TMP/claude-install.sh" https://claude.ai/install.sh ||
            die "couldn't download Claude Code's installer"
        bash "$TMP/claude-install.sh" || die "Claude Code's installer failed"
        how_claude="official"
    fi
    [ "$dry" = 1 ] && return
    bin=$(command -v claude) || die "Claude Code installed, but \`claude\` isn't on PATH"
    real=$bin
    while [ -L "$real" ]; do
        link=$(readlink "$real")
        case "$link" in /*) real=$link ;; *) real="$(dirname "$real")/$link" ;; esac
    done
    if ! codesign --verify --strict "$real" 2>/dev/null ||
        ! codesign -dvv "$real" 2>&1 | grep -q "^TeamIdentifier=$ANTHROPIC_TEAM\$"; then
        die "$real isn't signed by Anthropic (team $ANTHROPIC_TEAM). Don't run it; remove it
(Claude Code's uninstall steps: https://code.claude.com/docs/en/setup) and ask for help."
    fi
    say "Claude Code is signed by Anthropic PBC ($ANTHROPIC_TEAM)"
}

if command -v uv >/dev/null 2>&1; then say "uv: already here ($(uv --version))"
else say "uv: not found; installing"; install_uv; fi

if command -v gh >/dev/null 2>&1; then say "gh: already here ($(gh --version | head -n 1))"
else say "gh: not found; installing"; install_gh; fi

cv=$(claude_version || true)
if [ -n "$cv" ] && ver_ge "$cv" "$MIN_CLAUDE"; then
    say "Claude Code: already here ($cv)"
elif [ -n "$cv" ]; then
    say "Claude Code: $cv is older than ecf needs ($MIN_CLAUDE); updating"
    run claude update
    how_claude="already"
else
    say "Claude Code: not found; installing"
    install_claude
fi
if [ "$dry" = 0 ]; then
    cv=$(claude_version || true)
    if [ -z "$cv" ] || ! ver_ge "$cv" "$MIN_CLAUDE"; then
        die "Claude Code ${cv:-is not working}; ecf needs $MIN_CLAUDE or later"
    fi
fi

case ":$ORIG_PATH:" in
    *":$BIN:"*) ;;
    *)
        say "$BIN isn't on your PATH, so Terminal won't find uv, gh, claude or ecf there."
        if ask "Add it to ~/.zprofile?" y; then
            run sh -c "printf '\n# added by install-ecf-macos.sh\nexport PATH=\"\$HOME/.local/bin:\$PATH\"\n' >> \"\$HOME/.zprofile\""
            path_line=1
        else
            say "Add this line to your shell's profile yourself: export PATH=\"\$HOME/.local/bin:\$PATH\""
        fi ;;
esac

step "Downloading ecf"
if command -v ecf >/dev/null 2>&1 || uv tool list 2>/dev/null | grep -q "^email-classify-filter "; then
    die "ecf is already installed ($(ecf --version 2>/dev/null || echo "version unknown")). To move
to a newer release, use \`ecf upgrade\`; this script doesn't replace a working install."
fi
tag=${version_arg:-$(latest_tag "$REPO")}
case "$tag" in
    v[0-9]*.[0-9]*.[0-9]*) ;;
    *) die "$tag isn't a release tag (vX.Y.Z)" ;;
esac
version=$(printf '%s' "${tag#v}" | sed 's/-rc/rc/')
whl="$DIST-$version-py3-none-any.whl"
base="$GITHUB/$REPO/releases/download/$tag"
say "ecf $tag from $GITHUB/$REPO/releases"
if [ "$dry" = 0 ]; then
    for f in "$whl" SHA256SUMS release-manifest.json; do
        curl -fsSL -o "$TMP/$f" "$base/$f" || die "couldn't download $f from release $tag"
    done
    have=$(sha256_of "$TMP/$whl")
    [ "$(awk -v n="$whl" '$2 == n {print $1}' "$TMP/SHA256SUMS")" = "$have" ] ||
        die "$whl doesn't match its line in SHA256SUMS; it may be damaged or replaced, so
nothing was installed"
    if ! grep -q "^  \"tag\": \"$tag\",\$" "$TMP/release-manifest.json" ||
        ! grep -q "^  \"version\": \"$version\"\$" "$TMP/release-manifest.json"; then
        die "release-manifest.json isn't for $tag; nothing was installed"
    fi
    # The manifest's files and SHA256SUMS must list the same files with the same hashes.
    sed -n '/^  "files": {/,/^  }/s/^    "\([^"]*\)": "\([0-9a-f]\{64\}\)",\{0,1\}$/\2  \1/p' \
        "$TMP/release-manifest.json" | sort >"$TMP/manifest-sums"
    grep -v '^$' "$TMP/SHA256SUMS" | sort >"$TMP/sums-sorted"
    cmp -s "$TMP/manifest-sums" "$TMP/sums-sorted" ||
        die "release-manifest.json and SHA256SUMS of $tag don't agree; nothing was installed"
    say "Verified: $whl matches SHA256SUMS and release-manifest.json (sha256 $have)."
    say "This shows the files weren't damaged or swapped; it can't show who published them."
fi

step "Installing ecf"
# uv tool remembers the wheel it installed from, and `ecf upgrade` needs that file to stay put.
dest="$KEEP/releases/$tag"
if [ "$dry" = 1 ]; then
    say "  (dry run) keep $whl in $dest; uv tool install --python $python <that wheel>"
else
    mkdir -p "$ROOT" && chmod 0700 "$ROOT"
    mkdir -p "$dest"
    cp "$TMP/$whl" "$dest/$whl"
    [ "$(sha256_of "$dest/$whl")" = "$have" ] || die "the copy in $dest doesn't match"
    [ "$python" = 3.13 ] && say "Python 3.13: ecf's tests run on it on Linux, not on macOS."
    uv tool install --python "$python" "$dest/$whl" || die "uv tool install failed"
    say "Installed: $(ecf --version)"
    now=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    cat >"$RECORD" <<EOF
{
  "installed_at": "$now",
  "ecf_tag": "$tag",
  "python": "$python",
  "bin_dir": "$BIN",
  "uv": "$how_uv",
  "gh": "$how_gh",
  "claude_code": "$how_claude",
  "zprofile_path_line": $([ "$path_line" = 1 ] && echo true || echo false)
}
EOF
fi

step "Signing in to GitHub (for upgrades)"
if [ "$dry" = 1 ]; then
    say "  (dry run) offer gh auth login"
elif gh auth status >/dev/null 2>&1; then
    say "gh is signed in."
else
    say "\`ecf upgrade\` downloads new releases with gh, and gh needs you signed in to GitHub."
    if [ -t 0 ] && ask "Sign in now (gh auth login)?" y; then
        gh auth login || say "gh auth login didn't finish; run it before your first upgrade."
    else
        say "Run \`gh auth login\` before your first \`ecf upgrade\`."
    fi
fi

step "Next: ecf init"
say "ecf init sets up Slack, your first mailbox, backups and Claude, one step at a time."
if [ "$dry" = 0 ] && [ -t 0 ] && ask "Start ecf init now?" y; then
    exec ecf init
fi
say "When you're ready, open a new Terminal window and run: ecf init"
