#!/bin/bash
# Builds "Odoo Database Manager.app" (SwiftUI) from this package.
#   macos/build.sh [destination-folder]      default: macos/build
# Only needs the Command Line Tools (no Xcode).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(dirname "$HERE")"
DEST="${1:-$HERE/build}"
APP_NAME="Odoo Database Manager"
APP="$DEST/$APP_NAME.app"
VERSION="$(sed -n 's/^APP_VERSION = "\(.*\)"/\1/p' "$REPO/version.py")"

cd "$HERE"
swift build -c release --arch "$(uname -m)" 2>&1 | grep -Ev "^\[|^Building|^Compiling|^Write" || true
BIN="$(swift build -c release --arch "$(uname -m)" --show-bin-path)/OdooDBManager"
[[ -x "$BIN" ]] || { echo "Build failed"; exit 1; }

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN" "$APP/Contents/MacOS/OdooDBManager"
cp "$REPO/Odoo.icns" "$APP/Contents/Resources/AppIcon.icns"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>$APP_NAME</string>
  <key>CFBundleDisplayName</key><string>$APP_NAME</string>
  <key>CFBundleIdentifier</key><string>com.odoo.dbmanager</string>
  <key>CFBundleExecutable</key><string>OdooDBManager</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>LSApplicationCategoryType</key><string>public.app-category.developer-tools</string>
  <key>DBMRepoPath</key><string>$REPO</string>
</dict>
</plist>
PLIST
codesign --force --deep --sign - "$APP" >/dev/null 2>&1 || true
echo "$APP"
