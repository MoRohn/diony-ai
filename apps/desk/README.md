# Desk

The customer Desk is a self-contained Mac or Windows application. It does not install Python or packages at first launch. It opens the startup lobby immediately, uses OS-owned application-data and log directories, semantically verifies Studio and Producer, refuses to kill unknown port owners, and exposes a secret-free diagnostics download when startup fails. Blueprint `17` is the distribution and acceptance contract.

The source-checkout fallback remains for engineers: open **START HERE.txt**, then double-click **Open Diony AI** in the repo folder. START HERE names the crate, or a lease from Runpod first (`https://www.runpod.io/`) then Enverge (`https://enverge.ai/`). The page installs the desk once, then Continue is Studio at `http://studio.diony.local`. Producer is `http://producer.diony.local` after Sign. The first open may ask for this computer’s password once so those names resolve. Approve it. If you cancel, the lobby Continue retries — it does not open a dead name.

## Local Mac application

On a Mac developer checkout, double-click the repository-root **Build Diony AI.command**. It creates or reuses the current native application, then opens `dist/desktop/{version}/development/Diony AI.app` as a new instance so the current payload can replace an older Desk and its services. It is deliberately marked `development`, `publishable: false`, and `ad-hoc-local-only`; it is not the notarized customer DMG.

```bash
# After the venv exists, engineers can also:
diony-desk
```

## Release engineering

Desktop releases are built natively on each target OS; they are never cross-compiled. Set the platform signing variables, then run the matching builder:

```bash
# macOS release host: Universal 2 Python 3.11+ plus Developer ID credentials
DIONY_RELEASE_PYTHON=/Library/Frameworks/Python.framework/Versions/3.11/bin/python3 \
DIONY_MAC_APP_IDENTITY="Developer ID Application: …" \
DIONY_NOTARY_PROFILE=diony-notary \
scripts/release/build-macos.sh
```

```powershell
# Windows release host: Python 3.11+, Windows SDK SignTool, and Inno Setup
$env:DIONY_RELEASE_PYTHON = "C:\Python311\python.exe"
$env:DIONY_WINDOWS_CERT_SHA1 = "…"
$env:DIONY_WINDOWS_TIMESTAMP_URL = "https://…"
.\scripts\release\build-windows.ps1
```

After both platform proof files exist, `python scripts/release/finalize-desktop-release.py` writes the stable `downloads.json`. Stable finalization fails if either artifact is missing, misnamed, unsigned, unnotarized/untimestamped, or unverified.
