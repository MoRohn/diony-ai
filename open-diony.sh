#!/bin/sh
# Browser-only launcher for macOS and Linux.
cd "$(dirname "$0")" || exit 1
ROOT="$(pwd)"

echo "Diony AI"
echo "Studio will open at http://127.0.0.1:8787"
echo "Leave this terminal open while Diony is running."
echo

pause() {
  if [ -t 0 ]; then
    printf "Press Return to close. "
    read -r _
  fi
}

pick_python() {
  for name in python3.11 python3.12 python3.13 python3.14 python3; do
    if command -v "$name" >/dev/null 2>&1; then
      if "$name" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
        echo "$name"
        return 0
      fi
    fi
  done
  return 1
}

if [ ! -f "$ROOT/scripts/run_local.py" ]; then
  echo "This file must stay in the Diony AI folder."
  pause
  exit 1
fi

SYSTEM_PY="$(pick_python)" || {
  echo "Diony AI needs Python 3.11 or newer."
  echo "Install it from https://www.python.org/downloads/ then open this again."
  if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "https://www.python.org/downloads/" >/dev/null 2>&1 || true
  fi
  pause
  exit 1
}

if [ ! -x "$ROOT/.venv/bin/python" ]; then
  "$SYSTEM_PY" -m venv "$ROOT/.venv" || exit 1
fi

PY="$ROOT/.venv/bin/python"
if ! "$PY" -c 'import diony_contracts, diony_wizard, diony_producer, diony_director, uvicorn' >/dev/null 2>&1; then
  "$PY" -m pip install --disable-pip-version-check \
    -e "$ROOT/packages/contracts" \
    -e "$ROOT/apps/wizard" \
    -e "$ROOT/apps/producer" \
    -e "$ROOT/apps/director" || exit 1
fi

exec "$PY" "$ROOT/scripts/run_local.py"
