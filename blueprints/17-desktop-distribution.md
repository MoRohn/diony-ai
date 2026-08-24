# 17 — Desktop Installation, Startup, and Distribution

Status: release constitution. This Blueprint governs the customer-facing Mac and Windows application path. It does **not** change Studio lessons, Producer objectives, signed sites, fleet profiles, model selection, human gates, or the 1080 finish mandate.

## Acquisition contract

The future Diony webpage presents exactly two primary choices:

- **Download for Mac** → `DionyAI-{version}-macOS-universal.dmg`
- **Download for Windows** → `DionyAI-Setup-{version}-windows-x64.exe`

The page reads `downloads.json`; filenames, byte counts, SHA-256 hashes, version, architecture, minimum OS, and release proof are data rather than duplicated webpage copy. A stable manifest is invalid unless both platform artifacts exist and carry complete trust proof.

Source-checkout launchers remain for engineers and DGX OS. They are not the customer download experience.

## Local developer application

`Build Diony AI.command` is the repository-level bridge to the installed-application process on macOS. It creates a deterministic development payload, builds a native app for the current Mac architecture, writes an explicit non-publishable development proof, and maintains the stable path `dist/desktop/{version}/development/Diony.app`. Re-running it reuses an identical payload build, opens that exact app as a new instance, and lets the current payload replace an older Desk and its bundled services. This path proves bundled startup locally; it never claims Universal 2, Developer ID, notarization, stapling, Gatekeeper distribution acceptance, a DMG, or hosted bytes.

## Installed application contract

The installed application is self-contained. First launch must not require system Python, `pip`, a terminal, a source checkout, administrator shell commands, or an editable environment. The signed Diony executable dispatches Desk, Studio, Producer, and the product-name gateway from the same packaged runtime.

Application resources are immutable. Mutable state belongs to the signed-in OS user:

| Platform | State root |
|---|---|
| macOS | `~/Library/Application Support/Diony AI` |
| Windows | `%LOCALAPPDATA%\Diony AI` |

Vault state, control-plane data, logs, and diagnostics live below that root unless the user explicitly selects a local or leased cluster vault. Upgrades replace application files without deleting projects or user state. Uninstall does not erase the vault.

## Startup contract

Opening Diony AI immediately opens the Desk lobby. The lobby remains the visible source of truth until the studio is ready or an actionable error is recorded.

Startup performs these checks in order:

1. Verify the bundled product payload and required cluster profiles.
2. Resolve the user-owned state and vault directories.
3. Probe Studio semantically (`/api/state` plus the shipped UI stamp), not only port 8787.
4. Probe Producer semantically (`/api/project` plus the shipped UI stamp), not only port 8788.
5. Restart a stale Diony-owned process once.
6. Never terminate an unknown process occupying a product port. Explain the collision and leave it untouched.
7. Lay `studio.diony.local` and `producer.diony.local`; request OS elevation only for that machine-level name boundary.
8. Open Studio only after both services and the product-name gateway are healthy.

Failures write an atomic, secret-free `diagnostics.json` plus persistent Studio and Producer logs. The Desk offers a diagnostics download suitable for support. Environment variables, BYOK values, tokens, and media contents never enter that report.

## Release payload

`diony_desk.release stage` builds a deterministic product payload. It includes application source needed for signed-site deployment, contracts, infrastructure profiles, Blueprints, license, notice, and operator instructions. It excludes tests, caches, local environments, runtime data, SQLite files, logs, and `.env` secrets. `desktop-release.json` records the version, file count, and payload SHA-256.

PyInstaller packages the Python runtime and all application dependencies per operating system. The payload is included as read-only application data so Studio can still deploy the exact pinned source and profiles to a signed cluster.

## Mac release gate

The Mac artifact is a Universal 2 `.app` inside a compressed DMG. Release automation must:

1. Build on macOS from the versioned payload with a Universal 2 Python 3.11+ runtime. Apple’s older `/usr/bin/python3` is not a release runtime.
2. Sign nested Mach-O code individually, then sign the app with Developer ID and Hardened Runtime. Do not use `codesign --deep` as a signing shortcut.
3. Verify the application signature strictly.
4. Create and sign the DMG.
5. Submit the DMG with `notarytool`, wait for acceptance, staple the ticket, and validate the staple.
6. Pass Gatekeeper assessment and emit SHA-256 plus `macos-proof.json`.

An unsigned, unstapled, unnotarized, or Gatekeeper-rejected DMG is not publishable.

## Windows release gate

The Windows artifact is a 64-bit installer with Start Menu integration, optional desktop shortcut, standard uninstall registration, and a post-install Open action. Release automation must:

1. Build the self-contained onedir application on Windows with Python 3.11+; every native build command must return success.
2. Authenticode-sign and RFC 3161 timestamp every executable DLL/PYD boundary.
3. Verify every inner signature.
4. Compile the installer, then sign, timestamp, and verify the installer itself.
5. Emit SHA-256 plus `windows-proof.json`.

An unsigned, untimestamped, or signature-verification-failing installer is not publishable.

## Publish gate and proof layers

`finalize-desktop-release.py` accepts both platform artifacts and their proof files, then writes the stable `downloads.json`. Website publication comes later and consumes that file without inventing platform state.

Keep proof layers separate:

- source tests and payload determinism;
- packaged application startup on each OS;
- Mac signature, notarization, staple, Gatekeeper, and installed launch;
- Windows inner signatures, installer signature, install, launch, repair, and uninstall;
- hosted artifact byte/hash comparison;
- webpage button and download response verification.

A local PyInstaller build is not notarization proof. A checksum is not installation proof. A webpage link is not hosted-byte proof.
