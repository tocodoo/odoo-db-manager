#!/usr/bin/env bash
# Build Odoo Database Manager: .app (py2app) + .pkg installer. No DMG.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -d venv ]]; then
  echo "Créez d'abord le venv: python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt"
  exit 1
fi

# shellcheck source=/dev/null
source venv/bin/activate

APP_DIST="dist/Odoo Database Manager.app"
if [[ -e "$APP_DIST" ]] && [[ ! -w "$APP_DIST" ]]; then
  echo "Erreur: ${APP_DIST} n'est pas modifiable (souvent propriétaire root après un .pkg)."
  echo "Corrigez puis relancez le build :"
  echo "  sudo chown -R \"\$(whoami)\" \"${APP_DIST}\" && rm -rf \"${APP_DIST}\""
  echo "ou :"
  echo "  sudo rm -rf \"${APP_DIST}\""
  exit 1
fi
rm -rf "$APP_DIST"

pip install -q -r requirements.txt
python setup.py py2app

APP_VERSION="$(python -c "from version import APP_VERSION; print(APP_VERSION)")"
PKG_ROOT="$(mktemp -d)"
trap 'rm -rf "$PKG_ROOT"' EXIT

# Structure standard : Payload/Applications/… + install-location /
mkdir -p "$PKG_ROOT/Applications"
cp -R "dist/Odoo Database Manager.app" "$PKG_ROOT/Applications/"

pkgbuild \
  --root "$PKG_ROOT" \
  --identifier com.odoo.dbmanager \
  --version "$APP_VERSION" \
  --install-location / \
  --scripts build/pkg-expanded/Scripts \
  "dist/Odoo-Database-Manager.pkg"

echo "OK: dist/Odoo Database Manager.app"
echo "OK: dist/Odoo-Database-Manager.pkg (v${APP_VERSION}) → /Applications/Odoo Database Manager.app"
