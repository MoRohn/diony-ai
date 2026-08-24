#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
RELEASE_PYTHON="${DIONY_RELEASE_PYTHON:-}"
if [[ -z "$RELEASE_PYTHON" ]]; then
  for candidate in \
    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 \
    /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 \
    /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 \
    /Library/Frameworks/Python.framework/Versions/3.11/bin/python3; do
    if [[ -x "$candidate" ]]; then
      RELEASE_PYTHON="$candidate"
      break
    fi
  done
fi
if [[ -z "$RELEASE_PYTHON" || ! -x "$RELEASE_PYTHON" ]]; then
  echo "Set DIONY_RELEASE_PYTHON to a Universal 2 Python 3.11+ executable." >&2
  exit 2
fi
if ! "$RELEASE_PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'; then
  echo "DIONY_RELEASE_PYTHON must be Python 3.11 or newer." >&2
  exit 2
fi
if ! /usr/bin/file "$RELEASE_PYTHON" | /usr/bin/grep -q "universal binary"; then
  echo "The Mac release Python must be Universal 2 (arm64 + x86_64)." >&2
  exit 2
fi
VERSION="$("$RELEASE_PYTHON" -c 'import json, pathlib; print(json.loads((pathlib.Path("'"$ROOT"'") / "packaging/desktop/version.json").read_text())["version"])')"
APP_IDENTITY="${DIONY_MAC_APP_IDENTITY:-}"
NOTARY_PROFILE="${DIONY_NOTARY_PROFILE:-}"
OUTPUT="$ROOT/dist/desktop/$VERSION"
ARTIFACT="$OUTPUT/DionyAI-$VERSION-macOS-universal.dmg"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "Mac releases must be built on macOS." >&2
  exit 2
fi
if [[ -z "$APP_IDENTITY" || -z "$NOTARY_PROFILE" ]]; then
  echo "Set DIONY_MAC_APP_IDENTITY and DIONY_NOTARY_PROFILE." >&2
  exit 2
fi
for tool in /usr/bin/codesign /usr/bin/hdiutil /usr/sbin/spctl /usr/bin/xcrun /usr/bin/file; do
  [[ -x "$tool" ]] || { echo "Missing release tool: $tool" >&2; exit 2; }
done
if [[ -e "$ARTIFACT" ]]; then
  echo "Refusing to overwrite existing release: $ARTIFACT" >&2
  exit 2
fi

BUILD_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/diony-mac-release.XXXXXX")"
cleanup() { rm -rf "$BUILD_ROOT"; }
trap cleanup EXIT
PY="$BUILD_ROOT/venv/bin/python"
PAYLOAD="$BUILD_ROOT/payload"
INSTALL_PAYLOAD="$BUILD_ROOT/install-payload"
PYTHONPATH="$ROOT/apps/desk/src" "$RELEASE_PYTHON" -m diony_desk.release \
  stage "$ROOT" "$PAYLOAD" --version "$VERSION"
/bin/cp -R "$PAYLOAD" "$INSTALL_PAYLOAD"
"$RELEASE_PYTHON" -m venv "$BUILD_ROOT/venv"
"$PY" -m pip install --disable-pip-version-check -r "$ROOT/packaging/desktop/requirements-release.txt"
"$PY" -m pip install --disable-pip-version-check \
  "$INSTALL_PAYLOAD/packages/contracts" "$INSTALL_PAYLOAD/apps/wizard" "$INSTALL_PAYLOAD/apps/wan" \
  "$INSTALL_PAYLOAD/apps/finish" "$INSTALL_PAYLOAD/apps/producer" "$INSTALL_PAYLOAD/apps/conductor" \
  "$INSTALL_PAYLOAD/apps/desk"

export DIONY_DESKTOP_PAYLOAD="$PAYLOAD"
export DIONY_DESKTOP_VERSION="$VERSION"
export DIONY_MAC_ARCH="universal2"
"$PY" -m PyInstaller --clean --noconfirm \
  --distpath "$BUILD_ROOT/dist" --workpath "$BUILD_ROOT/work" \
  "$ROOT/packaging/desktop/diony.spec"
APP="$BUILD_ROOT/dist/Diony.app"
[[ -d "$APP" ]] || { echo "PyInstaller did not produce Diony.app" >&2; exit 1; }

while IFS= read -r -d '' item; do
  if /usr/bin/file "$item" | /usr/bin/grep -q "Mach-O"; then
    /usr/bin/codesign --force --options runtime --timestamp --sign "$APP_IDENTITY" "$item"
  fi
done < <(/usr/bin/find "$APP/Contents" -type f -print0)
/usr/bin/codesign --force --options runtime --timestamp --sign "$APP_IDENTITY" "$APP"
/usr/bin/codesign --verify --strict --verbose=4 "$APP"

DMG_STAGE="$BUILD_ROOT/dmg"
/bin/mkdir -p "$DMG_STAGE"
/bin/cp -R "$APP" "$DMG_STAGE/Diony.app"
/bin/ln -s /Applications "$DMG_STAGE/Applications"
/bin/mkdir -p "$OUTPUT"
/usr/bin/hdiutil create -volname "Diony AI" -srcfolder "$DMG_STAGE" \
  -ov -format UDZO "$ARTIFACT"
/usr/bin/codesign --force --timestamp --sign "$APP_IDENTITY" "$ARTIFACT"
/usr/bin/xcrun notarytool submit "$ARTIFACT" --keychain-profile "$NOTARY_PROFILE" \
  --wait --output-format json > "$OUTPUT/macos-notarization.json"
/usr/bin/xcrun stapler staple "$ARTIFACT"
/usr/bin/xcrun stapler validate "$ARTIFACT"
/usr/sbin/spctl --assess --verbose=4 --type install "$ARTIFACT"
"$PY" -m pip freeze --all > "$OUTPUT/macos-python-lock.txt"
/usr/bin/shasum -a 256 "$ARTIFACT" > "$ARTIFACT.sha256"
printf '%s\n' '{"signed":true,"notarized":true,"stapled":true,"gatekeeper_verified":true}' \
  > "$OUTPUT/macos-proof.json"
echo "$ARTIFACT"
