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

# --component : installe le .app directement dans /Applications (comportement attendu par macOS)
pkgbuild \
  --component "$APP_DIST" \
  --install-location /Applications \
  --identifier com.odoo.dbmanager \
  --version "$APP_VERSION" \
  --scripts build/pkg-expanded/Scripts \
  "dist/Odoo-Database-Manager.pkg"

echo "OK: dist/Odoo Database Manager.app"
echo "OK: dist/Odoo-Database-Manager.pkg (v${APP_VERSION}) → /Applications/Odoo Database Manager.app"
echo "En cas d'échec d'install, voir : /var/log/Odoo-Database-Manager-install.log"
echo "  et ~/Library/Logs/Odoo Database Manager/install.log"
