# AGENTS.md — Diony AI

This file is the operating system for humans and coding agents working in this repository. Read it before changing product behavior, provider adapters, pricing, or quality gates. The numbered Blueprints are the constitution; code implements them and does not quietly contradict them.

## Project overview

Diony AI is a proprietary desktop film studio that shoots on cloud video models. The **Director** (chat-first, `apps/director`) turns one line into a priced, reviewed, narrated 1080p film on **Grok Imagine** (xAI) and **Google Flow** (Veo 3.1 and Omni Flash through useapi.net). Studio chooses the engine, Producer holds the Gallery. Cost is visible at every step: estimate before anything rolls, live while it rolls, receipt after. Everything generated is downloaded into a local vault the moment it exists.

The product pivoted to this on 2026-08-31 (Blueprint 25). The earlier local lane (Wan2.2 on DGX Spark and leased B200/B300 GPUs, a DeepSeek brain, the `diony_sr_v1` finish) was deleted from the tree on 2026-09-10; the changelog records what left. There is no automatic route: Studio requires the user to pick one camera, and that camera shoots the whole film.

North Star deliverable: a finished program, 1920×1080 (1080×1920 portrait), Rec.709, one narrator or on-camera dialogue by style, loudness-locked at −16 LUFS (streaming) or −24 LUFS (broadcast), delivered with `film.srt`, `film.vtt`, `edl.json`, `assembly-map.json`, `qc-packet.json`, and a receipt whose estimate reconciles to its actual.

## Repository structure

| Path | Role |
|---|---|
| `apps/director` | The Director service (`diony-director`, `:8790`, `director.diony.local`) and its console. Sessions, screenwriter, master prompt, splitter, compiler (three dialects), `providers/{grok,flow}`, capabilities matrix, review, revisions, assembly, narration, deliverables, ledger, rate cards, cast lock, likeness guard. `diony-director-check` is the readiness CLI. |
| `apps/wizard` | Studio (`diony-wizard`, `:8787`, `studio.diony.local`): the opening page (camera choice, key chips) and `gateway.py`, the `:80` name router that lays all three product names. |
| `apps/producer` | Producer (`diony-producer`, `:8788`, `producer.diony.local`): the Finished Clips gallery, the launch control plane API (Blueprint 16: the ledger's plumbing, no governance UI), and the services the Director imports: `control_plane`, `costs` (the ledger), `face_identity`, `quality`, `speech_transcript`, `semantic_qc`, `media_tools`. |
| `apps/desk` | First open: the lobby, service supervision, stale-build detection by UI stamp, the packaged-app payload (`release.py`). |
| `packages/contracts` | Pydantic v2 contracts and JSON Schema. Cloud core: `director.py` (DirectorOp, CostEstimate, EngineMode), `production.py` (Script, ClipUnit, AudioPacket, ContinuityLock, ClipReview, ProductionPackage), `launch.py` (control plane, cost ledger), `cluster.py` (engine choice, vault root). |
| `blueprints/` | The constitution. 25–33 are the cloud era; 00, 10, 15–17, and 24 are the foundations that still apply. Retired numbers (01–09, 11–14, 18–23) are not reused. |
| `packaging/`, `scripts/release/` | PyInstaller payload, Mac/Windows installers, signed release builders. |
| `pyproject.toml` (root) | Repo-wide ruff and pytest configuration only. Each app and package has its own manifest. |

## Tech stack

- Python 3.11+, FastAPI, uvicorn, httpx, Pydantic v2. Static HTML/CSS/JS consoles, no SPA framework.
- Providers over REST: xAI (`https://api.x.ai`), useapi.net Google Flow (`https://api.useapi.net/v1/google-flow`). Module-level `httpx` in each adapter is the seam tests monkeypatch.
- The brain is any OpenAI-compatible chat endpoint, resolved by `diony_director.brain`: Gemini 2.5 Pro on the Google Flow camera (`GEMINI_API_KEY`), Grok 4.6 on Grok Imagine (`XAI_API_KEY`), the desk's Brain pill or `DIONY_DIRECTOR_BRAIN_URL` overriding. The narrator follows the same house (Gemini speech or the Grok voice API) unless a voice id names the other.
- Host tools: ffmpeg/ffprobe (required; the desktop build bundles the release pinned in `apps/desk/src/diony_desk/ffmpeg_lock.json` and `diony_desk.media` installs it on open when a bundle lacks it — source checkouts bring their own); OpenCV Zoo YuNet + SFace (optional, staged by `diony-director-check --stage-face-models`); faster-whisper (optional, `DIONY_WHISPER_MODEL`).
- Ledger and control plane: SQLite under `{vault}/.control/`.

## Build and run

Customer first open: `START HERE.txt`. Keys live in `~/.diony/keys.env` (`XAI_API_KEY`, `USEAPI_TOKEN`, `DIONY_FLOW_EMAIL`, `DIONY_FLOW_PLAN`); explicit environment wins; nothing in the app writes keys.

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e packages/contracts -e apps/wizard -e apps/producer -e apps/director -e apps/desk
pip install ruff pytest

diony-desk               # lobby → Studio → Director → Producer; lays the names
diony-director           # :8790 alone
diony-director-check     # readiness; --live probes; --roll-one grok|flow --yes spends once
```

Mac packaged process: double-click **Build Diony AI.command** (`scripts/release/build-local-macos.sh`). Repo edits reach the app only after a rebuild; the desk replaces an older local build automatically. Ad-hoc, local-only, never publishable; signed releases come from `scripts/release/build-macos.sh` and `build-windows.ps1`. The release scripts pip-install a hard-coded package list; a package missing there is silently absent from the app even when `diony.spec` lists it (a regression test pins the list).

## Tests and lint

```bash
pytest                                   # whole repo, offline, ~4 minutes
pytest apps/director                     # one package
ruff check . && ruff format --check .    # the pinned rule set in pyproject.toml
```

- The root `conftest.py` blocks every non-loopback socket for the session; every package conftest registers the same idempotent guard. `DIONY_ALLOW_NETWORK=1` only for a deliberate live run.
- Never run two pytest invocations at once: the desk's instance-lock tests deadlock across processes.
- Tests that cut real media skip without ffmpeg.
- Package conftests' autouse fixtures bleed across packages in a multi-directory run; never set a shared-scope `DIONY_CONTROL_PLANE_DB`, and keep test basenames unique across packages.
- UI stamps are pinned: Producer `?v=p9` by tests, the Director `?v=p55` by presence only. Bump the Director stamp on every UI change; update the pins when you bump the others. The desk reads the stamp at runtime to detect a stale build.
- A test that pins a literal (a stamp, a boot copy line, a rate) is documentation; update it in the same change and say why.

## Human gates (do not automate away)

1. **Script lock and cast approval.** Nothing prices before the lock; no character clip rolls before its face is approved.
2. **Distribution.** Who gets the master, and how.

Retakes reopen the cut; the run does it again.

## Quality bar (non-negotiable)

Blueprint 10 stands. Cloud additions, Blueprints 24–33:

- Every completed clip is reviewed before it may enter a cut. Unmeasured is honest and never `pass`; on a character clip it caps the verdict at `warn`.
- Identity is scored against the approved sheet (SFace ≥ 0.363), never against the previous shot.
- A provider-invented voice never reaches the mix twice: measured with whisper, or muted with a named receipt.
- A real person is described, never named, in every prompt (the likeness guard).
- Prompts fit the provider limit (4096) before any request leaves; dialogue, camera, and the identity card are untouchable.
- The film ends when the last line ends; words are never trimmed or sped up.
- A stale rate card is reported on every estimate and never fatal.

## Key patterns

- **Contracts first.** If a field is not on a `DirectorOp` / `ClipUnit` / `ClipReview`, it does not exist for production.
- **No priced op without an estimate**, and lines reconcile to the total. Pricing lives in JSON rate cards, never in code.
- **Nothing slow rides a request.** Brain calls, submits, waves, and finishes run in worker threads and are polled; the name router's forward timeout is not a budget.
- **Every per-op error is named.** `failure`, `held`, `stalled` on the op; nothing swallowed.
- **Late-bind locks.** Plates and bridge frames do not exist at split time; recompute references at dispatch and read the live setting.
- **Flow is paced.** One process-wide gate, requests never overlap, a quiet gap between them. Never bypass it.
- **Provider URLs are ephemeral.** Download and hash immediately; refer to the local file forever after.
- **A room holds one film.** A new script retires the earlier chain.
- **Two-step armed confirm** in every UI; never `window.confirm` or `prompt`.
- **Patch by test, not by regex.** Never patch across function boundaries with a regex; pin the body with a test that exercises it.

## Adding a feature

1. Update the relevant Blueprint, or add the next number and link it from `blueprints/README.md`.
2. Update `packages/contracts` if the machine API changes; regenerate schemas (`diony-export-schemas`).
3. Update the rate card if a priced unit changes, with its source and fetch date.
4. Update the console and bump the Director stamp; update `START HERE.txt` and `README.md` if the user's path changes.
5. Update `NOTICE` if a new upstream license or third-party service appears.
6. Add a `CHANGELOG.md` entry under Unreleased.

## Common pitfalls

- Pricing an image op as video seconds (a `RateCardMissing` that used to leave stills "generating" forever).
- Sending a bare string where xAI wants `{"url": ...}`; more than 3 Grok references; frames and references together on any model.
- A name beside a picture on Flow (`PROMINENT_PERSON` fires on invented names too).
- Growing a prompt after the fit (describe before fit; the submit gate refits).
- Reading the split-time snapshot of a setting instead of the live one.
- A synchronous call over 55 s through the router.
- Hard-coded credentials of any kind; a hardcoded HF token was removed from `apps/wizard/src/diony_wizard/enterprise.py` on 2026-09-10 and must be rotated.
- Guessing a camera. `session.engine_policy()` raises `EngineNotChosen` (a 409) when Studio has not chosen; nothing defaults to Grok.

## History

The local GPU lane (Wan2.2 on DGX Spark and leased B200/B300, the DeepSeek brain, the `diony_sr_v1` finish, cluster profiles, the Studio hardware rail, the Producer's Idea → Production floor, the S2V voice lane) was removed on 2026-09-10 after a reachability audit showed the Director imported eight Producer modules and nothing else from it. Blueprints 01–09, 11–14, and 19–23 went with it; their numbers are retired. `git log` before that date holds the lane if it is ever wanted again.

## CI

`.github/workflows/ci.yml` runs ruff and the offline suite on a plain CPU runner with ffmpeg. No GPU runners; no live provider calls in CI.
