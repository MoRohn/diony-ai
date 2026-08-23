#!/bin/bash
# Double-click in Finder. First open installs the desk. After that it opens Studio.
cd "$(dirname "$0")" || exit 1
ROOT="$(pwd)"

echo "Diony AI"
echo "A browser will open Studio at http://studio.diony.local"
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
  echo "Diony AI needs Python 3.11 or newer."
  echo "Install it from https://www.python.org/downloads/macos/ then double-click this again."
  if command -v open >/dev/null 2>&1; then
    open "https://www.python.org/downloads/macos/"
  fi
  read -r -p "Press Return to close. "
  exit 1
}

BOOT="$ROOT/apps/desk/src/diony_desk/boot.py"
if [ ! -f "$BOOT" ]; then
  echo "This file must stay in the Diony AI folder."
  read -r -p "Press Return to close. "
  exit 1
fi

exec "$PY" "$BOOT"
