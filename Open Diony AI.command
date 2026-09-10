#!/bin/bash
# Diony AI — Mac launcher. Double-click in Finder.
#
# Opens the built Diony.app when this folder holds one (Build Diony AI.command
# makes it); otherwise runs the desk from source with this Mac's Python 3.11+.
# Either way a browser opens Studio at http://studio.diony.local.
cd "$(dirname "$0")" || exit 1
ROOT="$(pwd)"
BOOT="$ROOT/apps/desk/src/diony_desk/boot.py"

echo "Diony AI"
echo "A browser will open Studio at http://studio.diony.local"
echo

if [ ! -f "$BOOT" ]; then
  echo "This file must stay in the Diony AI folder."
  read -r -p "Press Return to close. "
  exit 1
fi

VERSION="$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$ROOT/packaging/desktop/version.json" | head -1)"
APP="$ROOT/dist/desktop/$VERSION/development/Diony.app"
if [ -n "$VERSION" ] && [ -d "$APP" ]; then
  echo "Opening the built Diony.app ($VERSION). ffmpeg and the runtime are inside it."
  echo "You can close this window."
  exec open -n "$APP"
fi

echo "No built app in this folder yet — running from source."
echo "(Double-click Build Diony AI.command once to build Diony.app.)"
echo "Leave this window open until it says you can close it."
echo

pick_python() {
  for name in python3.14 python3.13 python3.12 python3.11 python3; do
    if command -v "$name" >/dev/null 2>&1; then
      if "$name" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
        echo "$name"
        return 0
      fi
    fi
  done
  for path in \
    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 \
    /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 \
    /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 \
    /Library/Frameworks/Python.framework/Versions/3.11/bin/python3 \
    /opt/homebrew/bin/python3.11 \
    /usr/local/bin/python3.11; do
    if [ -x "$path" ]; then
      echo "$path"
      return 0
    fi
  done
  return 1
}

PY="$(pick_python)" || {
  echo "Diony AI needs Python 3.11 or newer to run from source."
  echo "Install it from https://www.python.org/downloads/macos/ then double-click this again,"
  echo "or double-click Build Diony AI.command to build the app, which needs no Python later."
  if command -v open >/dev/null 2>&1; then
    open "https://www.python.org/downloads/macos/"
  fi
  read -r -p "Press Return to close. "
  exit 1
}

exec "$PY" "$BOOT"
