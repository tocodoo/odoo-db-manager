#!/usr/bin/env bash
# Odoo Database Manager – one-command install (macOS).
#
#   curl -fsSL https://raw.githubusercontent.com/tocodoo/odoo-db-manager/main/install.sh | bash
#   or, from a clone: ./install.sh
#
# Checks every prerequisite and installs what is missing (Git, Python 3.10+),
# always installs from a git clone so the app can update itself from GitHub
# (clones the repo to ~/odoodbmanager when run from anywhere else), then installs
# the dependencies and a light app in /Applications that runs straight from the
# clone. Running it again updates everything. The app's "Update" button runs it
# with --update after a git pull.
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

# 1. Git (ships with Apple's Command Line Tools)
if ! git --version >/dev/null 2>&1; then
  echo "… installing Git: click Install in the macOS window that opens, then wait here"
  xcode-select --install >/dev/null 2>&1 || true
  for _ in $(seq 1 720); do xcode-select -p >/dev/null 2>&1 && break; sleep 5; done
  git --version >/dev/null 2>&1 || fail "Git is still missing. Finish the Command Line Tools install, then run this command again."
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

# 3. Python 3.10+ with a shared libpython (needed by the macOS app)
py_ok() {
  [[ -x "$1" ]] && "$1" -c 'import sys, sysconfig
sys.exit(0 if sys.version_info >= (3, 10) and (sysconfig.get_config_var("PYTHONFRAMEWORK") or sysconfig.get_config_var("Py_ENABLE_SHARED")) else 1)' 2>/dev/null
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

# 5. The app: a light bundle (py2app alias) that runs the code of this clone,
#    so an update is a git pull and a restart.
echo "… building the app"
BUILD="$HERE/build/alias"
rm -rf "$BUILD"
mkdir -p "$BUILD"
"$VENV/bin/python" setup.py py2app -A --dist-dir "$BUILD/dist" --bdist-base "$BUILD/py2app" >"$BUILD.log" 2>&1 \
  || { tail -20 "$BUILD.log"; fail "The app build failed. Full log: $BUILD.log"; }

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
ditto "$BUILD/dist/$APP_NAME.app" "$DEST/$APP_NAME.app"
ok "App installed: $DEST/$APP_NAME.app"

# 6. What the app drives (not installed here: Odoo developers already have them)
command -v psql >/dev/null 2>&1 || [[ -d "/Applications/Postgres.app" ]] \
  || warn "PostgreSQL (psql) not found: the app needs it to create databases."
[[ -d "${PYENV_ROOT:-$HOME/.pyenv}/versions" ]] \
  || warn "pyenv not found: the app runs Odoo with the pyenv environments odoo-18 / odoo-19."

open "$DEST/$APP_NAME.app"

[[ $UPDATE == 1 ]] && { ok "Updated to $(git rev-parse --short HEAD)"; exit 0; }
cat <<EOF

────────────────────────────────────────────────────────────
Done. Odoo Database Manager is open (icon in the menu bar).
Next time, open it from Applications or Spotlight.
Updates: click "Mettre à jour" in the app when it shows one.
────────────────────────────────────────────────────────────
EOF
