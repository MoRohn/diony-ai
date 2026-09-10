#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
"$ROOT/scripts/release/build-local-macos.sh"
VERSION="$(/usr/bin/python3 -c 'import json, pathlib; print(json.loads((pathlib.Path("'"$ROOT"'") / "packaging/desktop/version.json").read_text())["version"])')"
APP="$ROOT/dist/Diony.app"
if [[ ! -d "$APP" ]]; then
  echo "Diony.app was not created: $APP" >&2
  exit 1
fi
open -n "$APP"
echo
echo "The current Diony.app is open. An older local build is replaced automatically."
