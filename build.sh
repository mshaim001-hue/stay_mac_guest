#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
APP_NAME="StayMacGuest"
APP="$ROOT/dist/${APP_NAME}.app"
BIN="$APP/Contents/MacOS/${APP_NAME}"
RES="$APP/Contents/Resources"

rm -rf "$ROOT/dist"
mkdir -p "$APP/Contents/MacOS" "$RES"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key>
  <string>en</string>
  <key>CFBundleExecutable</key>
  <string>StayMacGuest</string>
  <key>CFBundleIdentifier</key>
  <string>school.challenge.StayMacGuest</string>
  <key>CFBundleInfoDictionaryVersion</key>
  <string>6.0</string>
  <key>CFBundleName</key>
  <string>StayMacGuest</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>2.0</string>
  <key>CFBundleVersion</key>
  <string>2</string>
  <key>LSMinimumSystemVersion</key>
  <string>13.0</string>
  <key>LSUIElement</key>
  <true/>
</dict>
</plist>
PLIST

echo "→ компиляция меню-бара…"
swiftc -O \
  -framework AppKit \
  -framework IOKit \
  "$ROOT/Sources/main.swift" \
  -o "$BIN"
chmod +x "$BIN"

echo "→ компиляция school_probe_killer (A1)…"
cc -O2 -o "$RES/school_probe_killer" "$ROOT/tools/school_probe_killer.c"
cp "$RES/school_probe_killer" "$ROOT/tools/school_probe_killer"
cp "$RES/school_probe_killer" "$ROOT/dist/school_probe_killer"
chmod +x "$RES/school_probe_killer" "$ROOT/tools/school_probe_killer" "$ROOT/dist/school_probe_killer"

# Engine рядом с приложением (и копия в Resources)
cp "$ROOT/stay_via_firefox.py" "$RES/stay_via_firefox.py"
cp "$ROOT/stay_via_firefox.py" "$ROOT/dist/stay_via_firefox.py"
chmod +x "$RES/stay_via_firefox.py" "$ROOT/dist/stay_via_firefox.py"

codesign --force --deep --sign - "$APP" 2>/dev/null || true

cat > "$ROOT/dist/start.command" <<EOF
#!/bin/zsh
cd "\$(dirname "\$0")"
open "./${APP_NAME}.app"
EOF
chmod +x "$ROOT/dist/start.command"

echo
echo "✅ Готово: $APP"
echo
echo "Запуск:"
echo "  open \"$APP\""
echo
echo "В меню-баре ⏳⏸ — пауза (можно работать)."
echo "«Ушёл — включить защиту» / «На месте — пауза»."
echo "Quit — полностью выйти."
