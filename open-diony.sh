#!/bin/sh
# Linux / DGX OS. Double-click or run from the file manager. First open installs the desk.
cd "$(dirname "$0")" || exit 1
ROOT="$(pwd)"
BOOT="$ROOT/apps/desk/src/diony_desk/boot.py"

echo "Diony AI"
echo "A browser will open Studio at http://studio.diony.local"
echo "Leave this window open until it says you can close it."
echo

pause() {
  if [ -t 0 ]; then
    printf "Press Return to close. "
    read -r _
  fi
}

pick_python() {
  for name in python3.14 python3.13 python3.12 python3.11 python3; do
    if command -v "$name" >/dev/null 2>&1; then
      if "$name" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
        echo "$name"
        return 0
      fi
    fi
  done
  return 1
}

if [ ! -f "$BOOT" ]; then
  echo "This file must stay in the Diony AI folder."
  pause
  exit 1
fi

PY="$(pick_python)" || {
  echo "Diony AI needs Python 3.11 or newer."
  echo "Install it from https://www.python.org/downloads/ then open this again."
  if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "https://www.python.org/downloads/" >/dev/null 2>&1 || true
  fi
  pause
  exit 1
}

exec "$PY" "$BOOT"
