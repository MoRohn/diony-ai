#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "The local Mac application must be built on macOS." >&2
  exit 2
fi

LOCAL_PYTHON="${DIONY_LOCAL_PYTHON:-}"
if [[ -z "$LOCAL_PYTHON" && -x "$ROOT/.venv/bin/python" ]]; then
  LOCAL_PYTHON="$ROOT/.venv/bin/python"
fi
if [[ -z "$LOCAL_PYTHON" ]]; then
  for candidate in python3.14 python3.13 python3.12 python3.11 python3; do
    resolved="$(command -v "$candidate" 2>/dev/null || true)"
    if [[ -n "$resolved" ]]; then
      LOCAL_PYTHON="$resolved"
      break
    fi
  done
fi
if [[ -z "$LOCAL_PYTHON" ]] || ! "$LOCAL_PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'; then
  echo "Diony AI local builds require Python 3.11 or newer." >&2
  exit 2
fi

VERSION="$("$LOCAL_PYTHON" -c 'import json, pathlib; print(json.loads((pathlib.Path("'"$ROOT"'") / "packaging/desktop/version.json").read_text())["version"])')"
BUILD_RECIPE="macos-development-v4"
ARCH="$(uname -m)"
if [[ "$ARCH" != "arm64" && "$ARCH" != "x86_64" ]]; then
  echo "Unsupported local Mac architecture: $ARCH" >&2
  exit 2
fi

OUTPUT="$ROOT/dist"
BUILDS="$OUTPUT/builds"
STABLE_APP="$OUTPUT/Diony.app"
LEGACY_STABLE_APP="$OUTPUT/Diony AI.app"
STABLE_REPORT="$OUTPUT/build.json"
LEGACY_DEV="$ROOT/dist/desktop/$VERSION/development"  # the pre-2026-09-10 layout
if [[ -e "$STABLE_APP" && ! -L "$STABLE_APP" ]]; then
  echo "Refusing to replace a non-managed app: $STABLE_APP" >&2
  exit 2
fi
if [[ -e "$STABLE_REPORT" && ! -L "$STABLE_REPORT" ]]; then
  echo "Refusing to replace a non-managed report: $STABLE_REPORT" >&2
  exit 2
fi
if [[ -e "$LEGACY_STABLE_APP" && ! -L "$LEGACY_STABLE_APP" ]]; then
  echo "Refusing to remove a non-managed legacy app: $LEGACY_STABLE_APP" >&2
  exit 2
fi

BUILD_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/diony-local-mac.XXXXXX")"
cleanup() { rm -rf "$BUILD_ROOT"; }
trap cleanup EXIT
PAYLOAD="$BUILD_ROOT/payload"
PYTHONPATH="$ROOT/apps/desk/src" "$LOCAL_PYTHON" -m diony_desk.release \
  stage "$ROOT" "$PAYLOAD" --version "$VERSION"
PAYLOAD_SHA="$("$LOCAL_PYTHON" -c 'import json, pathlib, sys; print(json.loads(pathlib.Path(sys.argv[1]).read_text())["payload_sha256"])' "$PAYLOAD/desktop-release.json")"

if [[ -L "$STABLE_APP" && -f "$STABLE_REPORT" ]]; then
  if "$LOCAL_PYTHON" -c 'import json, pathlib, sys; r=json.loads(pathlib.Path(sys.argv[1]).read_text()); raise SystemExit(0 if r.get("version") == sys.argv[2] and r.get("architecture") == sys.argv[3] and r.get("payload_sha256") == sys.argv[4] and r.get("build_recipe") == sys.argv[5] else 1)' \
    "$STABLE_REPORT" "$VERSION" "$ARCH" "$PAYLOAD_SHA" "$BUILD_RECIPE"; then
    if [[ -L "$LEGACY_STABLE_APP" ]]; then
      /bin/unlink "$LEGACY_STABLE_APP"
    fi
    echo "Local Diony AI $VERSION is already current."
    echo "$STABLE_APP"
    exit 0
  fi
fi

PY="$BUILD_ROOT/venv/bin/python"
INSTALL_PAYLOAD="$BUILD_ROOT/install-payload"
/bin/cp -R "$PAYLOAD" "$INSTALL_PAYLOAD"
"$LOCAL_PYTHON" -m venv "$BUILD_ROOT/venv"
"$PY" -m pip install --disable-pip-version-check \
  -r "$ROOT/packaging/desktop/requirements-release.txt"
"$PY" -m pip install --disable-pip-version-check \
  "$INSTALL_PAYLOAD/packages/contracts" "$INSTALL_PAYLOAD/apps/wizard" \
  "$INSTALL_PAYLOAD/apps/producer" "$INSTALL_PAYLOAD/apps/director" \
  "$INSTALL_PAYLOAD/apps/desk"

# ffmpeg comes with Diony: stage the pinned static release for this
# architecture (cached under ~/.diony-build/ffmpeg, SHA-256 verified) so the
# app carries ffmpeg and ffprobe beside its executable.
FFMPEG_KEY="macos-$([[ "$ARCH" == "arm64" ]] && echo arm64 || echo amd64)"
FFMPEG_STAGE="$BUILD_ROOT/ffmpeg"
PYTHONPATH="$ROOT/apps/desk/src" "$LOCAL_PYTHON" -m diony_desk.media fetch "$FFMPEG_KEY" "$FFMPEG_STAGE"
[[ -x "$FFMPEG_STAGE/ffmpeg" && -x "$FFMPEG_STAGE/ffprobe" ]] || {
  echo "The pinned ffmpeg release did not stage for $FFMPEG_KEY." >&2
  exit 1
}
export DIONY_FFMPEG_BUNDLE_DIR="$FFMPEG_STAGE"
export DIONY_DESKTOP_PAYLOAD="$PAYLOAD"
export DIONY_DESKTOP_VERSION="$VERSION"
export DIONY_MAC_ARCH="$ARCH"
"$PY" -m PyInstaller --clean --noconfirm \
  --distpath "$BUILD_ROOT/dist" --workpath "$BUILD_ROOT/work" \
  "$ROOT/packaging/desktop/diony.spec"
BUILT_APP="$BUILD_ROOT/dist/Diony.app"
[[ -x "$BUILT_APP/Contents/MacOS/Diony AI" ]] || {
  echo "PyInstaller did not produce an executable Diony.app." >&2
  exit 1
}
# PyInstaller lays binaries in Contents/Frameworks (the runtime folder the
# desk and Producer look in first); older layouts kept them in Contents/MacOS.
FFMPEG_HOME=""
for candidate in "$BUILT_APP/Contents/Frameworks" "$BUILT_APP/Contents/MacOS"; do
  if [[ -x "$candidate/ffmpeg" && -x "$candidate/ffprobe" ]]; then FFMPEG_HOME="$candidate"; break; fi
done
[[ -n "$FFMPEG_HOME" ]] || {
  echo "Diony.app was built without ffmpeg in its runtime folder." >&2
  exit 1
}
"$FFMPEG_HOME/ffmpeg" -version | head -1

BUILT_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
BUILD_ID="$(date -u +%Y%m%dT%H%M%SZ)-${PAYLOAD_SHA:0:12}-$ARCH"
FINAL_DIR="$BUILDS/$BUILD_ID"
if [[ -e "$FINAL_DIR" ]]; then
  echo "Local build already exists: $FINAL_DIR" >&2
  exit 2
fi
/bin/mkdir -p "$FINAL_DIR"
/bin/mv "$BUILT_APP" "$FINAL_DIR/Diony.app"
"$PY" -m diony_desk.release development-report \
  --app "$FINAL_DIR/Diony.app" \
  --payload-metadata "$PAYLOAD/desktop-release.json" \
  --version "$VERSION" --architecture "$ARCH" --built-at "$BUILT_AT" \
  --output "$FINAL_DIR/build.json"

/bin/mkdir -p "$OUTPUT"
/bin/ln -sfn "builds/$BUILD_ID/Diony.app" "$STABLE_APP"
/bin/ln -sfn "builds/$BUILD_ID/build.json" "$STABLE_REPORT"
# Git keeps only the current build under dist/builds/ (the root .gitignore opens
# dist/Diony.app, dist/build.json, dist/current-build.txt and dist/builds/);
# builds/.gitignore names the one build to keep, current-build.txt names it for
# the launchers.
/bin/cat > "$BUILDS/.gitignore" <<GITIGNORE
# Written by the local build script: only the current build is committed.
*
!.gitignore
!$BUILD_ID/
!$BUILD_ID/**
GITIGNORE
printf '%s\n' "$BUILD_ID" > "$OUTPUT/current-build.txt"
# A superseded build leaves the index too: `git add -A` never untracks a file
# a later .gitignore excludes, and every tracked build is ~70 MB compressed
# (three builds rode one push before this step existed).
if command -v git >/dev/null 2>&1 && git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  git -C "$ROOT" ls-files -z -- dist/builds | while IFS= read -r -d '' tracked; do
    case "$tracked" in
      dist/builds/.gitignore|dist/builds/"$BUILD_ID"/*) ;;
      *) git -C "$ROOT" rm -q --cached -- "$tracked" >/dev/null 2>&1 || true ;;
    esac
  done
fi
for legacy in "$LEGACY_DEV/Diony.app" "$LEGACY_DEV/development-build.json"; do
  if [[ -L "$legacy" ]]; then /bin/unlink "$legacy"; fi
done
if [[ -L "$LEGACY_STABLE_APP" ]]; then
  /bin/unlink "$LEGACY_STABLE_APP"
fi

# Prune old local builds, keeping the newest KEEP_BUILDS (build IDs sort by timestamp).
KEEP_BUILDS="${DIONY_KEEP_LOCAL_BUILDS:-5}"
CURRENT_BUILD="$(basename "$(dirname "$(readlink "$STABLE_APP")")")"
for old in $(ls "$BUILDS" | sort -r | tail -n +"$((KEEP_BUILDS + 1))"); do
  [[ "$old" == "$CURRENT_BUILD" ]] && continue
  /bin/rm -rf "$BUILDS/$old"
done

echo "Built local-only Diony AI $VERSION for $ARCH."
echo "This app is not Developer ID-signed, notarized, or publishable."
echo "$STABLE_APP"
