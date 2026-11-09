#!/usr/bin/env bash
# Odoo Database Manager – one-command install (macOS).
#
#   curl -fsSL https://raw.githubusercontent.com/tocodoo/odoo-db-manager/main/install.sh | bash
#   or, from a clone: ./install.sh
#
# Checks every prerequisite and installs what is missing (Command Line Tools,
# Python 3.10+), always installs from a git clone so the app can update itself
# from GitHub (clones the repo to ~/odoodbmanager when run from anywhere else),
# then installs:
#   - the `odoodb` command and the Claude skill / MCP server (agents use them),
#   - the native macOS app (SwiftUI, built here), a monitor driving `odoodb`.
# Running it again updates everything (`--update` after a git pull).
set -euo pipefail

REPO_URL="${ODOO_DB_MANAGER_REPO:-https://github.com/tocodoo/odoo-db-manager.git}"
INSTALL_DIR="${ODOO_DB_MANAGER_DIR:-$HOME/odoodbmanager}"
APP_NAME="Odoo Database Manager"
BUNDLE_ID="com.odoo.dbmanager"
UPDATE=0
[[ "${1:-}" == "--update" ]] && UPDATE=1

ok() { printf '\033[32m✓\033[0m %s\n' "$1"; }
warn() { printf '\033[33m⚠\033[0m %s\n' "$1"; }
fail() { printf '\033[31m✗ %s\033[0m\n' "$1"; exit 1; }

echo "Odoo Database Manager – $([[ $UPDATE == 1 ]] && echo update || echo install)"
echo

[[ "$(uname)" == "Darwin" ]] || fail "This script is for macOS."

# 1. Git and Swift (ship with Apple's Command Line Tools)
if ! git --version >/dev/null 2>&1 || ! swift --version >/dev/null 2>&1; then
  echo "… installing Git: click Install in the macOS window that opens, then wait here"
  xcode-select --install >/dev/null 2>&1 || true
  for _ in $(seq 1 720); do xcode-select -p >/dev/null 2>&1 && break; sleep 5; done
  git --version >/dev/null 2>&1 || fail "Git is still missing. Finish the Command Line Tools install, then run this command again."
  swift --version >/dev/null 2>&1 || fail "Swift is missing: run 'xcode-select --install', then this command again."
fi
ok "$(git --version)"

# 2. Run from a git clone, or create one
HERE=""
[[ -n "${BASH_SOURCE[0]:-}" && -f "${BASH_SOURCE[0]}" ]] && HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -z "$HERE" || ! -e "$HERE/.git" ]]; then
  if [[ -d "$INSTALL_DIR/.git" ]]; then
    echo "… updating $INSTALL_DIR"
    git -C "$INSTALL_DIR" pull --ff-only --quiet
  else
    [[ -e "$INSTALL_DIR" ]] && fail "$INSTALL_DIR already exists and is not a git clone. Move it away, then run this command again."
    echo "… downloading Odoo Database Manager into $INSTALL_DIR"
    git clone --quiet --depth 1 "$REPO_URL" "$INSTALL_DIR"
  fi
  ok "Git clone: $INSTALL_DIR"
  exec bash "$INSTALL_DIR/install.sh" "$@"
fi
cd "$HERE"
git rev-parse --abbrev-ref --symbolic-full-name '@{u}' >/dev/null 2>&1 \
  || warn "This branch does not track a GitHub branch: updates are off."
ok "Git clone: $HERE"

# 3. Python 3.10+ (runs odoodb)
py_ok() {
  [[ -x "$1" ]] && "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null
}
find_python() {
  local v c
  for v in 3.12 3.13 3.11 3.10; do
    for c in "/opt/homebrew/bin/python$v" "/usr/local/bin/python$v" "/Library/Frameworks/Python.framework/Versions/$v/bin/python$v" "$HOME/.pyenv/versions/$v"*/bin/python"$v"; do
      py_ok "$c" && { echo "$c"; return 0; }
    done
  done
  c="$(command -v python3 || true)"
  py_ok "$c" && { echo "$c"; return 0; }
  return 1
}

VENV="$HERE/venv"
if py_ok "$VENV/bin/python"; then
  PYTHON="$VENV/bin/python"
else
  PYTHON="$(find_python || true)"
  if [[ -z "$PYTHON" ]]; then
    [[ $UPDATE == 1 ]] && fail "No usable Python 3.10+ found. Run the install command again in the Terminal."
    if command -v brew >/dev/null 2>&1; then
      echo "… installing Python 3.12 (Homebrew)"
      brew install --quiet python@3.12
    else
      echo "… installing Python 3.12 (python.org): macOS asks for your password"
      TMP="$(mktemp -d)"
      trap 'rm -rf "$TMP"' EXIT
      curl -fL# "https://www.python.org/ftp/python/3.12.10/python-3.12.10-macos11.pkg" -o "$TMP/python.pkg"
      pkgutil --check-signature "$TMP/python.pkg" | grep -q "Python Software Foundation" || fail "The Python download is not signed by python.org. Run this command again."
      sudo installer -pkg "$TMP/python.pkg" -target / >/dev/null
    fi
    PYTHON="$(find_python || true)"
    [[ -n "$PYTHON" ]] || fail "Python 3.10+ is still missing. Install it from python.org, then run this command again."
  fi
  echo "… creating the Python environment"
  rm -rf "$VENV"
  "$PYTHON" -m venv "$VENV"
fi
ok "$("$VENV/bin/python" --version)"

# 4. Dependencies
echo "… installing dependencies"
"$VENV/bin/python" -m pip install --quiet --disable-pip-version-check --upgrade pip
"$VENV/bin/python" -m pip install --quiet --disable-pip-version-check -r requirements.txt
ok "Dependencies installed"

# 5. odoodb for agents: command, Claude Code skill, Claude Desktop MCP server
bash "$HERE/agent/install-agent.sh"

# 6. The native app (SwiftUI), built from macos/
echo "… building the app (about a minute)"
BUILD="$HERE/macos/build"
LOG_DIR="$HOME/Library/Logs/$APP_NAME"
mkdir -p "$LOG_DIR"
bash "$HERE/macos/build.sh" "$BUILD" >"$LOG_DIR/build.log" 2>&1 \
  || { tail -20 "$LOG_DIR/build.log"; fail "The app build failed. Full log: $LOG_DIR/build.log"; }

# Quit the running app (the update is started from it)
[[ $UPDATE == 1 ]] && sleep 1
osascript -e "tell application id \"$BUNDLE_ID\" to quit" >/dev/null 2>&1 || true
for _ in $(seq 1 10); do pgrep -f "$APP_NAME.app/Contents/MacOS" >/dev/null || break; sleep 0.5; done
pkill -f "$APP_NAME.app/Contents/MacOS" >/dev/null 2>&1 || true

DEST="/Applications"
[[ -w "$DEST" ]] || { DEST="$HOME/Applications"; mkdir -p "$DEST"; }
if [[ -e "$DEST/$APP_NAME.app" ]] && ! rm -rf "$DEST/$APP_NAME.app" 2>/dev/null; then
  # Left by the former .pkg installer (owned by root)
  if [[ $UPDATE == 0 ]]; then
    echo "… replacing the former app: macOS asks for your password"
    sudo rm -rf "$DEST/$APP_NAME.app"
  else
    DEST="$HOME/Applications"; mkdir -p "$DEST"; rm -rf "$DEST/$APP_NAME.app"
  fi
fi
ditto "$BUILD/$APP_NAME.app" "$DEST/$APP_NAME.app"
ok "App installed: $DEST/$APP_NAME.app"

# 7. What the app drives (not installed here: Odoo developers already have them)
command -v psql >/dev/null 2>&1 || [[ -d "/Applications/Postgres.app" ]] \
  || warn "PostgreSQL (psql) not found: the app needs it to create databases."
[[ -d "${PYENV_ROOT:-$HOME/.pyenv}/versions" ]] \
  || warn "pyenv not found: the app runs Odoo with the pyenv environments odoo-18 / odoo-19."

open "$DEST/$APP_NAME.app"

[[ $UPDATE == 1 ]] && { ok "Updated to $(git rev-parse --short HEAD)"; exit 0; }
cat <<'EOF'

────────────────────────────────────────────────────────────
Done. Odoo Database Manager is open (icon in the menu bar).
Agents: any Claude Code session now knows the `odoodb` command
(try: "create an Odoo 19 database with sale and start it").
Updates: run this command again, or `odoodb self-update`.
────────────────────────────────────────────────────────────
EOF
