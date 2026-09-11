# 17 — Browser-Local Runtime

Status: in force since 2026-09-10; supersedes the retired desktop installation and distribution contract

## Product decision

Diony is a local web application, not an installed desktop application. Python
runs Studio, Director, and Producer on loopback; the system browser is the UI.
Generation, brain, still, and narration inference use explicitly configured
cloud APIs. Local host tools perform download, inspection, assembly, subtitles,
and packaging, but no local model is a production dependency.

The former Desk, PyInstaller bundle, committed `Diony.app`, Mac DMG, Windows
installer, privileged name router, and signed release pipeline are retired.
Their implementation and proof artifacts live in Git history, not the current
tree.

## Acquisition contract

A checkout contains two thin convenience launchers and one portable launcher:

- `open-diony.sh` for macOS and Linux;
- `Open Diony AI.command`, which delegates to the shell launcher;
- `Open Diony AI.bat` for Windows.

The launchers create `.venv` when absent, install the editable cloud packages
when imports are absent, and run `scripts/run_local.py`. They do not build,
download, install, sign, notarize, or copy an application bundle.

## Runtime contract

The supervisor starts exactly three services:

| Surface | Port | Readiness |
|---|---:|---|
| Studio | 8787 | `/api/health` |
| Director | 8790 | `/api/director/status` |
| Producer | 8788 | `/api/engine` |

The defaults bind to `127.0.0.1`. Navigation uses explicit public URL helpers,
defaulting to loopback and allowing `DIONY_STUDIO_URL`,
`DIONY_DIRECTOR_URL`, and `DIONY_PRODUCER_URL` overrides. No `/etc/hosts`
change, administrator elevation, port-80 gateway, or `.diony.local` name is
part of startup.

Startup refuses to overwrite a foreign listener. Every service must pass its
semantic health endpoint before Studio opens. If one child exits, the launcher
reports the failure and terminates the remaining children. `SIGINT` and
`SIGTERM` stop the service group cleanly.

## State and secrets

The source launcher defaults both `DIONY_VAULT` and
`DIONY_PRODUCER_VAULT` to `<checkout>/.diony-vault`. Explicit environment
values win. SQLite, clips, films, receipts, and generated sidecars stay beneath
the vault.

Provider keys live in `~/.diony/keys.env` or the explicit
`DIONY_KEYS_FILE`. Environment variables override file values. The UI may
report presence but never returns or writes a secret.

## Host dependencies

Python 3.11+ and ffmpeg/ffprobe are host dependencies. The launchers do not
silently install OS packages. Missing media tools produce an actionable
platform-specific hint and block only the functionality that requires them.

Optional YuNet, SFace, and faster-whisper assets remain quality instruments;
they are not generation models and their absence must remain honestly
`unmeasured`, never a false pass.

## Pass criteria

- One command opens Studio and all three health endpoints answer on loopback.
- No desktop package, installer, bundled interpreter, bundled ffmpeg, hosts-file
  mutation, or privileged listener exists in the product tree.
- No local generation or reasoning model is started or required.
- Camera selection remains explicit; missing provider credentials prevent the
  corresponding camera from being selected.
- The existing pricing, human gates, provider submit/poll/download behavior,
  review, assembly, narration, deliverables, and local receipts remain intact.
- The whole test suite remains offline unless a deliberate live flag is set.
