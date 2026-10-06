#!/bin/sh
# Install browserd on macOS, or update it in place:
#
#     curl -fsSL https://raw.githubusercontent.com/jjenkins2004/browserd/main/install.sh | sh
#
# It puts the release named by VERSION on main (or BROWSERD_REF: a tag, a branch or a commit) in
# ~/.local/share/browserd/app, runs npm ci there for chrome-devtools-mcp, and links ~/.local/bin/browserd to it, adding
# ~/.local/bin to the shell's PATH if it is missing. A server already running is restarted on the new version, and every
# Chrome and session kept. Its records stay in ~/Library/Application Support/browserd, whichever version runs.
#
#     BROWSERD_REF       what to install: v0.2.0, main, ...; the latest release otherwise
#     BROWSERD_PREFIX    where the code goes, ~/.local/share/browserd otherwise
#     BROWSERD_BIN       where the browserd command goes, ~/.local/bin otherwise
#     BROWSERD_PYTHON    the Python 3.10 or later to run it with, python3 otherwise
#     BROWSERD_ARCHIVE   a .tar.gz of browserd to install instead of downloading one, as git archive makes
#     BROWSERD_NO_PATH   set to leave the shell's PATH alone
#
# To uninstall: browserd uninstall. It keeps the records in ~/Library/Application Support/browserd; delete that folder
# too to remove them.
set -eu

REPO=jjenkins2004/browserd
PREFIX=${BROWSERD_PREFIX:-$HOME/.local/share/browserd}
BIN=${BROWSERD_BIN:-$HOME/.local/bin}
PY=${BROWSERD_PYTHON:-python3}

say() { printf '%s\n' "$*"; }
fail() { printf 'browserd: %s\n' "$*" >&2; exit 1; }

[ "$(uname -s)" = Darwin ] || fail "install.sh is for macOS; on Windows run install.ps1 (see the README)"
for tool in curl tar; do
  command -v "$tool" >/dev/null 2>&1 || fail "$tool is not installed"
done

# Python 3.10 or later: the one macOS ships in /usr/bin is 3.9.
command -v "$PY" >/dev/null 2>&1 || fail "$PY is not installed; browserd needs Python 3.10 or later (brew install python)"
"$PY" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null ||
  fail "$("$PY" --version 2>&1) is too old; browserd needs Python 3.10 or later (brew install python, then run this again, or set BROWSERD_PYTHON)"
PY_PATH=$(command -v "$PY")

# Node 20.19 or later, for chrome-devtools-mcp.
command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1 ||
  fail "node and npm are not installed; browserd needs Node 20.19 or later (brew install node)"
node -e 'const [a, b] = process.versions.node.split(".").map(Number); process.exit(a > 20 || (a === 20 && b >= 19) ? 0 : 1)' ||
  fail "node $(node --version) is too old; browserd needs 20.19 or later (brew upgrade node)"

[ -d "/Applications/Google Chrome.app" ] ||
  say "note: Google Chrome is not in /Applications; browserd needs it (brew install --cask google-chrome)"

if [ -n "${BROWSERD_ARCHIVE:-}" ]; then
  BROWSERD_REF=local
elif [ -z "${BROWSERD_REF:-}" ]; then
  latest=$(curl -fsSL "https://raw.githubusercontent.com/$REPO/main/VERSION") || fail "could not ask GitHub for the latest version"
  BROWSERD_REF=v$(printf '%s' "$latest" | tr -d '[:space:]')
fi

mkdir -p "$PREFIX" "$BIN"
staging=$(mktemp -d "$PREFIX/.staging.XXXXXX")
trap 'rm -rf "$staging"' EXIT
if [ -n "${BROWSERD_ARCHIVE:-}" ]; then
  tar -xzf "$BROWSERD_ARCHIVE" -C "$staging" --strip-components 1 || fail "could not unpack $BROWSERD_ARCHIVE"
else
  say "downloading browserd $BROWSERD_REF"
  curl -fsSL "https://github.com/$REPO/archive/$BROWSERD_REF.tar.gz" | tar -xz -C "$staging" --strip-components 1 ||
    fail "could not download $BROWSERD_REF from github.com/$REPO"
fi
say "installing chrome-devtools-mcp"
(cd "$staging" && npm ci --omit=dev --no-audit --no-fund --loglevel=error) || fail "npm ci failed in $staging"
# This Python, whatever python3 is on the PATH later.
printf '%s\n' "$PY_PATH" > "$staging/.python"

# Asked of the version already installed, so its server can be restarted on this one.
running=
if [ -x "$BIN/browserd" ] && "$BIN/browserd" status 2>/dev/null | grep -q '^running'; then
  running=1
fi

rm -rf "$PREFIX/app"
mv "$staging" "$PREFIX/app"
trap - EXIT
ln -sfn "$PREFIX/app/browserd" "$BIN/browserd"
say "installed $("$BIN/browserd" version)"

if [ -n "$running" ]; then
  "$BIN/browserd" restart
fi

case "${BROWSERD_NO_PATH:+skip}:$PATH:" in
  skip:*|*":$BIN:"*) ;;
  *)
    case "${SHELL:-}" in
      */zsh) rc=$HOME/.zshrc ;;
      */bash) rc=$HOME/.bash_profile ;;
      *) rc=$HOME/.profile ;;
    esac
    line="export PATH=\"$BIN:\$PATH\""
    if ! grep -qsF "$line" "$rc"; then
      printf '\n# browserd\n%s\n' "$line" >> "$rc"
      say "added $BIN to the PATH in $rc; open a new terminal, or run: $line"
    fi
    ;;
esac

say ""
say "To connect Claude Code, run:"
say "  claude mcp add --scope user browserd -- browserd mcp"
say "browserd then starts whenever an agent needs it; browserd setup changes its ports."
