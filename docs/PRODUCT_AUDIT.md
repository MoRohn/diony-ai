# Diony AI — product audit

**Date:** 13 October 2026
**Tree:** `main` at `c2047c6` (“The current Diony.app carries the rebuilt Finished Clips Info modal”)
**Scope:** README, `START HERE.txt`, `AGENTS.md`, `CHANGELOG.md`, `docs/`, blueprints, `apps/`, `packages/`, packaging, and a local install. No product code was changed for this audit.
**Tagline the founder asked about:** “Own your Studio | Produce at Cost.” That line is not in the product today. The shipped lines are “From idea to moving image. Create with control.” and “ONE LINE IN · A PRICED, FINISHED FILM OUT.”

## How this was checked

| Check | Result |
|---|---|
| Environment | Linux x86_64, Python 3.12.3 (CI pins 3.11), ffmpeg and ffprobe on `PATH`, no `XAI_API_KEY` / `USEAPI_TOKEN` / `GEMINI_API_KEY` |
| Install | `python3 -m venv .venv` needed `python3.12-venv` first (`ensurepip` was missing). Then editable installs of contracts, wizard, producer, director, desk, plus ruff and pytest |
| `ruff check .` | Passed |
| `ruff format --check .` | Passed (170 files already formatted) |
| `pytest -q` | **879 passed, 1 failed, 0 skipped**, 311.87s. One warning: Starlette’s TestClient wants `httpx2` |
| `diony-director-check` | Exit 0. Engine cloud, all three keys absent, brain off, both rate cards fresh, vault defaulted to `apps/producer/data` |
| Headless launch | Studio `:8787`, Director `:8790`, and Producer `:8788` answered on `127.0.0.1` with a throwaway vault. The committed `dist/Diony.app` is a Mach-O arm64 binary and cannot start on this machine. The `:80` name router did not bind |

The failing test is `apps/desk/tests/test_boot.py::test_ensure_running_restarts_unhealthy_wizard`. Re-running it alone failed the same way in 0.06s. The sibling test `test_ensure_running_restarts_stale_ui` passed. Cause: `wizard_is_current()` now requires both `GET /api/opening` and the HTML marker `data-diony="studio-opening"`, and this test only stubs the JSON probe. The real HTML probe then misses, and `ensure_running` raises “Studio is not serving this desk’s UI after restart.” The log line still says “`/api/state` is not 200”; Studio has not served `/api/state` since the cloud pivot. CI on Python 3.11 should fail this test the same way. Current `main` is red.

Live provider calls were not made. Generation, narration, upscale, and receipts-after-spend are judged from code and the offline suite, which mocks those providers.

---

## 1. What the product is, and who it is for

Diony AI is a desktop film studio. A person types one idea. The **Director** writes a script, shows an itemised price, and — after the person locks the script — shoots the clips on a cloud video model they already pay for. It reviews each clip, cuts the film, adds one narrator (or lets characters speak on camera), and saves the master on the person’s own machine with subtitles, an edit list, a QC packet, and a receipt.

There are two cameras, and the person picks one for the whole film:

- **Grok Imagine** (xAI), billed per second on the person’s xAI key. Takes up to 15 seconds. Default model `grok-imagine-video-1.5`.
- **Google Flow** (Veo 3.1 and Omni Flash, reached through useapi.net), billed in the person’s Google AI plan credits. Takes up to 10 seconds on Omni Flash, with a server-side 1080p finish on a paid plan.

No GPU is required. Keys live in `~/.diony/keys.env` and are not typed into the app. Finished media is downloaded into a local vault the moment a provider returns it. Provider URLs are not kept.

**Who it is for.** A filmmaker, a small studio, or a creator who wants a finished short — picture, voice, subtitles, and a cost record — without standing up a render farm. They already have, or are willing to open, an xAI account or a Google AI plan plus a useapi.net subscription. They work on a Mac or a Windows PC they control. Linux runs from source only.

**Who it is not for.** It is not a hosted website, not a multi-user studio with a sign-in, and not a local GPU pipeline. The earlier DGX Spark / leased B200 lane was removed on 10 September 2026. It is also not yet something a stranger can buy from diony.ai: there is no published installer (see the website brief).

The three rooms are Studio (pick the camera), Director (make the film), and Producer (the gallery of finished work). A small Desk process starts them and, once, asks for an administrator password to add `studio.diony.local`, `director.diony.local`, and `producer.diony.local` to the hosts file.

---

## 2. Feature inventory

Status means: **working** (behaved correctly in this audit, or the offline tests that cover it passed and the code path is complete), **partial** (the surface exists and some of it works; a promised gate, output, or platform does not), **broken** (a path this audit executed failed).

### Opening and desk

| Feature | Status | Evidence |
|---|---|---|
| Studio opening: two cameras, key chips, Continue | **Working** | `GET /api/opening` returned both providers `false` and `flow_plan: pro`. `POST /api/opening/engine` with `grok` returned 200 and wrote the vault. `wan` returned 422: `unknown production engine 'wan'`. HTML carries `data-diony="studio-opening"`. |
| One camera for the whole film | **Working** | `ENGINE_CHOICES = ("grok", "google-flow")`. Director status on the shared vault reported `engine_choice: grok`. |
| Platform copy (Mac / Windows toggle) | **Partial** | On this Linux host, `detected` was `linux` and `chosen` was `mac`. Hints then told the user to `brew install ffmpeg`, open `Diony.app`, and approve a macOS password. `read_platform_choice()` documents that Linux reads as Mac. There is no Linux card. `ffmpeg_lock.json` has macOS and Windows pins only. |
| Product names (`*.diony.local` on port 80) | **Broken** on an unprivileged Linux desk | `python -m diony_wizard.gateway serve` exited 1 with no message; nothing listened on port 80, and `studio.diony.local` was not in the hosts file. `diony-wizard` with a TTY called `ensure_product_urls()`, which ran `pkexec` and died with `FileNotFoundError` before uvicorn bound `:8787`. `DIONY_NAMES_SKIP=1` let Studio start. The numeric ports still work once the processes are up. |
| Desk lobby, stale-build restart, ffmpeg install-on-open | **Partial** | Lobby and supervision code is in `diony_desk.boot`. The full desk was not launched here (it opens a browser and requests root). One of its tests fails on current `main` (above). User-facing restart copy still names `/api/state` and `/api/project`, which the cloud apps do not serve. Blueprint 17’s startup list still names those paths. |
| Packaged Mac app in the repo | **Partial** | `dist/build.json`: version 0.22.0, arm64, built 2026-09-13, `publishable: false`, `trust: ad-hoc-local-only`, `apple_notarized: false`, bundle about 195 MB. `file` reports Mach-O arm64. It did not launch on this x86_64 Linux host. |
| Windows installer and signed Mac DMG | **Partial** | Named in `packaging/desktop/version.json` (`DionyAI-0.22.0-macOS-universal.dmg`, `DionyAI-Setup-0.22.0-windows-x64.exe`). No `downloads.json` and no installer bytes in the tree. Blueprint 17 says website publication comes later. `START HERE.txt` already tells a customer “When the Diony webpage is live, choose Download for Mac or Download for Windows.” |

### Making a film (Director)

Exercised offline against a Grok choice and no keys, unless noted.

| Feature | Status | Evidence |
|---|---|---|
| New session | **Working** | `POST /api/director/sessions` returned `s-…` ids. |
| One-shot proposal with a price | **Working** | A 6s 480p harbor shot replied with a derived master prompt and “about $0.48”, which is 6 × $0.08, the `grok-imagine-video-1.5` 480p card. Op stayed `proposed`. |
| Run refused until a provider key exists | **Working** | `POST /api/director/ops/{id}/run` returned **409** `provider grok is not configured`. |
| Draft script without a brain | **Working** | `POST …/produce` for a 30s idea returned `status: ready` immediately: “Template draft (no brain configured)”, 2 scenes, 10 lines, characters `LEAD` and `SECOND`. The reply says nothing rolls before the lock. |
| Screenwriter on Grok 4.6 / Gemini 3.1 | **Working** in tests, not live here | `diony-director-check` reports models `grok-4.6` and `gemini-3.1-pro-preview`, `reason: brain off`. README’s “How a film is made” still says Gemini 2.5 Pro. The retirement follower in `brain.py` exists because Google closed 2.5 Pro to new keys. |
| Lock script and itemised price | **Working** | Lock returned 200: “2 clips across 2 scenes, about 0:21 of picture… Locked estimate $4.21” split into clips $2.98, stills $0.03, revision allowance $1.19. Ledger stayed at $0. |
| Bible / character-sheet pricing when no key is loaded | **Partial** | With neither adapter configured, `sheet_lane()` falls through to Google Flow `nano-banana-2`. The lock then labelled 11 stills `provider_id: google-flow`, `model: bible-stills`, `rate_card_id: ""`, $0.033 total. A desk that actually holds an xAI key prices `grok-imagine-image-2.0` instead. A Grok quote made before keys are present can show Flow stills. |
| Duration must be set | **Working** | `duration_s: 0` on a message returned 422 (`greater_than`). |
| Cast dock and face approval | **Partial** | Approve routes exist (`…/bible/approve/{char_id}`, `approve_all`). After the lock, both characters had `face_approved: false`. `POST …/run_batch` still returned **200** and queued 10 ops (`queued: 10`, `submitting: true`). `ready_ops()` does not read `face_approved`. The console shows “approve by eye” and does not disable Run & Assemble until a face is approved. AGENTS.md says no character clip rolls before its face is approved. The record exists; the gate does not. |
| Likeness guard (describe a real person, never name them) | **Working** in tests | `likeness.py` plus `test_likeness.py`. Not triggered by the generic template draft. |
| Look bible, style frames, chain-on-last-frame | **Working** in tests | `look.py`, compiler look block, chain tests (`test_oneshot_chain.py`, `test_look_bible.py`). The lock reply said each clip chains from the previous last frame. |
| Production styles: Narrated, Dialogue, Mix | **Working** in tests | `styles.py`, `test_production_style.py`. Default in this draft was the narrated path (template copy). |
| Voice cast (one voice per character; narrator roster) | **Working** in tests | Template assigned distinct Omni preset refs (`Achird`, `Achernar`). Changelog records the “one voice for everyone” fix. Live provider voices were not heard. |
| Grok adapter: video, image, image edit, video edit, extend | **Working** in tests | Functions exist in `providers/grok.py`. The module docstring still says “edits, extensions later”; the functions are already there. |
| Flow adapter: video, stills, extend, upscale, characters, pacing, captcha errors | **Working** in tests | `providers/flow.py`. `GET /api/director/flow/characters` with no token returned **503** `Google Flow is not configured` — a named error, not a hang. |
| 429 pauses a run | **Working** in tests | Adapters raise `CloudAdapterQuotaError`; the run path maps that to 503 with `Retry-After` and leaves the op proposed. Not observed live. |
| Review before a clip may enter the cut | **Working** in tests | `review.py`. Unmeasured identity is `warn`, not `pass`. This desk had no YuNet/SFace and no whisper model staged, so a real character clip here would cap at warn. |
| Retake, skip shot, retry all | **Working** in tests | `test_retry_ladder.py`, `test_skip_shot.py`. |
| Assembly, loudness, room tone | **Working** in tests, **partial** against the North Star | `assembly.py` defaults `WIDTH, HEIGHT = 1280, 720` and `FPS = 24`, loudness −16 LUFS streaming / −24 broadcast. The North Star in Blueprint 00 and the README is 1920×1080, 23.976 fps, Rec.709, ProRes or DNxHR plus an H.265 proxy. The cloud cut is an H.264 `film.mp4`. Flow 1080p is a separate upscale op (Blueprint 30). Grok is scaled at the cut. |
| Narration in post | **Working** in tests | `narration.py` (Grok voice, Gemini speech, or a local CLI). The draft reply states brain token spend “is not yet on the receipt.” |
| Deliverables | **Working** in tests | `film.mp4`, `film.srt`, `film.vtt`, `edl.json`, `assembly-map.json`, `qc-packet.json` via `deliverables.py`. None were produced in this no-key run, so the gallery stayed empty (`count: 0`). |
| Rate cards and “stale is never fatal” | **Partial** | Cards: Grok fetched **2026-08-31** (43 days old; stale after 45 days, so stale once the clock passes **2026-10-15 00:00 UTC**), Flow fetched **2026-09-10** (stale after **2026-10-25**). `diony-director-check` reported both `stale: false` today. Blueprint 25 says a stale card is reported and never fatal. `_run_approved()` returns **409** and refuses to spend when `rate_cards_stale` is set. On 15 October a Grok desk with an otherwise healthy key will refuse every run until the JSON card is refreshed. |
| Ledger and receipt | **Working** for provider spend; **partial** for the brain | `GET /api/director/ledger` returned zeros and `currency: USD` before any submit. Estimates commit on submit in `ledger.py`. Brain tokens are called out in the draft reply as not on the receipt. |
| Flow account reconnect from a browser session | **Partial** | `flow_reconnect.py` and `diony-director-check --reconnect`. Not a console button. |
| Distribution (who receives the master) | **Partial** | Still a human gate in Blueprint 00. There is no distribution step in the Director or the gallery. The person has the files in the vault. |
| Dry run | **Working** as `DIONY_DIRECTOR_DRY_RUN=1` (409). The desk also `setdefault`s `DIONY_DRY_RUN=1` on every child process. Nothing in `apps/` reads `DIONY_DRY_RUN`. It does not stop a real roll. |

### Producer gallery and leftover control plane

| Feature | Status | Evidence |
|---|---|---|
| Finished Clips gallery | **Working** | `GET /api/gallery` returned an empty inventory (`films: 0`, `clips: 0`, `join_minimum: 2`). UI stamp `app.js?v=p18`. Tests in `test_gallery.py` and `test_ui_copy.py` cover one card per production, ZIP, stitch, and delete. No finished film existed to click. |
| Studio governance UI (teams, invites, cost centers) | **Removed from the UI, still on the API** | Changelog records the Team button’s removal. `GET /api/v1/teams` on loopback, with no token, returned **200** and a synthetic `local-admin-team`. `DIONY_AUTH_MODE` defaults to `local`. |
| Control-plane worker | **Partial** | `diony-control-worker` is a script. The desk does not start it. Cloud generation is driven by the Director. |

---

## 3. Architecture

Four Python processes, plus a tiny hosts-file router. Static HTML/CSS/JS consoles. No SPA framework. Pydantic v2 contracts in `packages/contracts` are the machine API (`DirectorOp`, `CostEstimate`, `Script`, `ClipUnit`, `ClipReview`, launch-control models, engine choice).

```text
Browser
  │  Host: *.diony.local          (only after /etc/hosts + a listener on :80)
  ▼
gateway.py          127.0.0.1:80     forwards by Host header, 300s timeout
  ├─ studio.*     →  Studio    diony-wizard     :8787   camera choice, key chips
  ├─ director.*   →  Director  diony-director   :8790   script, price, shoot, cut
  └─ producer.*   →  Producer  diony-producer   :8788   gallery, ledger DB, QC libs

Desk  diony-desk  :8765   starts the three, watches UI stamps, installs ffmpeg
```

Default bind is `127.0.0.1` (`bind_host()`). `DIONY_BIND` can open that up.

**Vault** (`DIONY_VAULT`, else `/diony/vault` if that directory exists, else `apps/producer/data`):

- `director/{session}/` — `state.json`, clips, `film.mp4`, subtitles, `edl.json`, `assembly-map.json`, `qc-packet.json`
- `.cluster/engine.json` — the camera Studio chose
- `.control/` — SQLite ledger and control plane; BYOK master key when token mode is used

**Request rule.** Brain calls, provider submits, and “advance the job” run on worker threads. The browser polls. A 429 pauses the run. A failed job that spent money stays on the receipt.

**Providers.** `httpx` to `https://api.x.ai` and `https://api.useapi.net/v1/google-flow`. Flow has one process-wide pacing gate (default 5 seconds between calls). Media is downloaded and hashed immediately.

**What the Director imports from Producer.** The cost ledger, control plane, face identity (YuNet + SFace, optional), frame QC, speech transcript (faster-whisper, optional), semantic QC (CLIP, optional), and ffmpeg discovery. Producer does not render frames.

**Brain.** OpenAI-compatible chat. Gemini 3.1 Pro preview when the camera is Flow and `GEMINI_API_KEY` is set; Grok 4.6 when the camera is Grok and `XAI_API_KEY` is set. No key means the deterministic template writer, which this audit used.

**Packaging.** PyInstaller (`packaging/desktop/diony.spec`) plus local builders (`Build Diony AI.command` / `.bat`) and signed builders (`scripts/release/build-macos.sh`, `build-windows.ps1`). Product version on the desk and in `version.json` is **0.22.0**. Package versions disagree: director `0.1.0`, producer `0.9.0`, wizard `0.20.0`, desk `0.22.0`.

**Tests.** Root `conftest.py` blocks every non-loopback socket. About 67 test modules under `apps/` and `packages/contracts`. `pyproject.toml` still lists `apps/wan` and `apps/finish` as test paths; those trees were deleted on 10 September 2026. Pytest did not fail on the missing directories.

---

## 4. Risks

### Security

The threat model is a single user on loopback. That holds only while the ports stay on `127.0.0.1`.

- Director, Studio, and the gallery have no login. Anyone who can reach the port can change the camera, price a film, delete gallery items, or read prompts and media.
- `GET /api/v1/teams` bootstraps a local admin team with no credentials when `DIONY_AUTH_MODE=local` (the default). Token mode exists (`security.py`, HMAC tokens, AES-256-GCM for BYOK) and is what a shared machine would need. The shipped UI does not set a token.
- `DIONY_BIND=0.0.0.0` turns the above into a network service. Nothing in the UI warns about that.
- Keys are read from `~/.diony/keys.env` with set-default semantics (a real environment variable wins). The app does not write the file. It also does not enforce mode `0600` on read; the docs tell the user to chmod it.
- The hosts-file edit is a real privilege boundary (osascript admin, Windows `RunAs`, or `pkexec`/`sudo`). A missing `pkexec` currently crashes Studio instead of falling back. Approving the prompt only adds three loopback names.
- No hardcoded provider token was found under `apps/`. `enterprise.py` (the old Hugging Face token) is gone. AGENTS.md still says that token must be rotated if it was ever real.
- Provider prompts and reference stills leave the machine. That is required to render. The likeness guard is a best-effort control, and NOTICE says so. It is not a licence to depict a real person.
- Diagnostics are intended to be secret-free. This audit did not dump a failure report from a full desk launch.

### Licensing

- Diony source, blueprints, and consoles are proprietary (`LICENSE`, 2026). The license grants use only to “authorized licensees” under a written license, and forbids copy, modification, and distribution otherwise. A public GitHub-style checkout and a committed `Diony.app` sit awkwardly against that text: the repo is the distribution channel today.
- FFmpeg is shipped as separate GPL-3.0-or-later executables (Martin Riedl 9.0.1 on macOS, Gyan 8.1.2 on Windows), pinned by SHA-256 in `ffmpeg_lock.json`. NOTICE is clear that the GPL covers those binaries and not the Python app. A desktop product that bundles GPL ffmpeg still has to offer the corresponding source and keep the binaries unmodified. The lock file records the build URLs. There is no Linux pin, so a Linux source checkout uses the system ffmpeg (this machine’s `/usr/bin/ffmpeg`).
- Optional models (faster-whisper, CLIP, OpenCV Zoo YuNet/SFace) are MIT or Apache and are downloaded by the user, not bundled.
- OpenMontage is cited as a studied reference. NOTICE says no AGPL code is included. That claim was not re-audited line by line.
- Generated pictures and audio are the licensee’s problem under xAI, Google, and useapi.net terms. useapi.net is an unofficial relay in front of Google Flow. If Google or useapi.net changes or closes that API, the Flow camera stops. Captcha solving is a third-party dependency on that path.

### Packaging and distribution

- There is no customer download. Blueprint 17’s acquisition contract (two buttons, `downloads.json`, notarized Universal 2 DMG, signed Inno installer) is unimplemented. `START HERE.txt` already speaks as if the webpage exists.
- The only app in the repo is an ad-hoc-signed arm64 Mac build from 13 September 2026, marked not publishable. Gatekeeper will warn. It will not run on Intel Macs or on Windows. It is a month behind `main` if later commits did not rebuild it (the latest commit message says this build includes the gallery Info modal; the audit did not diff the bundle against source).
- A Windows user must run `Build Diony AI.bat` once, which needs Python 3.11+, PyInstaller, and a network fetch of the pinned ffmpeg. That is an engineer setup, not an install.
- Linux is `./open-diony.sh`, which creates a venv and starts the desk. This audit’s direct `diony-wizard` died on `pkexec`.
- Version skew (0.1.0 through 0.22.0) will confuse a future signed release: `version.json` says 0.22.0, the Director package says 0.1.0.
- Committing `dist/` (~187 MB in this checkout) makes every clone heavy and mixes a non-publishable binary with proprietary source.

### Tech debt

- **Blueprint 00 still describes the deleted product** (Wan2.2, DeepSeek, DGX Spark, “never cloud-by-default”) under the 31 August amendment that says the opposite. Blueprint 16 still talks about signed GPU sites. Blueprint 17’s probe list is stale. A new agent that trusts the top of Blueprint 00 will rebuild the wrong product. The amendment says numbered blueprints 25+ win; the body underneath is still easy to follow by mistake.
- **Copy contradicts the code in front of customers.** `START HERE.txt` says that with both keys “the Director chooses per shot.” README, Studio, and Blueprint 25 say one camera shoots the whole film. README’s film steps say Gemini 2.5 Pro; the running default is `gemini-3.1-pro-preview`.
- **Producer and wizard package blurbs** still mention the Wan2.2 lane and a `/classic` rail. `/classic` is not a route. `pyproject.toml` still lists `apps/wan` and `apps/finish` and ruff still knows `diony_finish`.
- **AGENTS.md** says the Producer UI stamp is `p16`. The file and `test_ui_copy.py` pin `p18`. Director is `p60`. Desk lobby is `brand3`.
- **Dead flags and dead APIs.** `DIONY_DRY_RUN` is set and never read. The launch API (teams, workflows, cost centers) has no console. Producer CSS still contains Idea and Production bay rules the HTML does not mount.
- **Cast approval and stale rate cards** are constitution gaps, not just cleanup. They can spend money the doctrine says they must not spend, or block spending the doctrine says must continue with a warning.
- **North Star vs. the cutter.** 1080p, 23.976, ProRes, and 5.1 are still written as the deliverable. The cutter emits 720p H.264 at 24 fps unless a Flow upscale finish is in the timeline.
- **No in-tree TODO/FIXME.** The gaps are behavioral, so a grep for TODOs will not find them.

---

## 5. Roadmap — ten next things

Ordered by what will hurt a real desk first. Effort is S (a focused change in a few files), M (crosses a service and its tests, or a release pipeline), L (changes the deliverable or the acquisition channel).

1. **Refresh the Grok rate card before 15 October 2026, and make staleness match the doctrine.** Effort **S**. The card’s fetch date is 31 August 2026 with a 45-day window. On 15 October `_run_approved()` will 409 every Grok run. Blueprint 25 says a stale card is reported and never fatal. Either re-fetch the xAI prices into `ratecards/grok-imagine.json` (and the Flow card before 25 October) or stop treating stale as a spend block. Do both if the prices have actually changed: a wrong card is a wrong receipt. This is the only item with a date on the calendar.

2. **Make `main` green again, and fix the startup sentence.** Effort **S**. `test_ensure_running_restarts_unhealthy_wizard` fails because it does not stub `wizard_html_ok`. The desk log still says Studio is stale because `/api/state` is not 200. Studio’s health URL is `/api/opening`. A red suite hides the next real failure, and the sentence sends a person debugging a launch to a route that 404s.

3. **Let Studio start when the privilege tool is missing.** Effort **S**. On this host `diony-wizard` raised `FileNotFoundError: pkexec` and never bound `:8787`. Catch that, print the numeric URLs, and keep the server up. `DIONY_NAMES_SKIP=1` already works and should be the documented Linux path until a name router is optional. A first open that dies before the page exists looks like a broken install.

4. **Enforce cast approval before a character clip can roll.** Effort **M**. The dock, the `face_approved` flag, and Approve all are built. `POST …/run_batch` queued work while every face was still unapproved. AGENTS.md and the human-gate list say that clip does not roll. Put the check in `ready_ops()` / `_run_approved()` for character clips, and have the Run buttons explain why they are waiting. Otherwise the dock is a suggestion.

5. **Ship the download Blueprint 17 already specifies.** Effort **L**. There is no `downloads.json`, no notarized DMG, and no signed Windows installer. The in-repo app is an ad-hoc arm64 build that Gatekeeper will challenge. Until those two artifacts exist, the marketing site must not offer Download buttons that 404, and `START HERE.txt` should stop describing a webpage download as the normal path. This is what turns a repo into a product a stranger can install.

6. **Put one true description of the product in front of people.** Effort **S**. Fix `START HERE.txt` (“the Director chooses per shot”), the Gemini 2.5 sentences in the README, and the wizard/producer package descriptions that still mention Wan and `/classic`. Move Blueprint 00’s GPU truths under an explicit “history” heading so they cannot be implemented again. The founder’s line “Own your Studio | Produce at Cost” is a better public tagline than the two lines the repo uses now; it is not in the product yet, so pick one and use it everywhere.

7. **Put brain spend on the receipt.** Effort **M**. The draft reply already confesses it: “LLM spend is not yet on the receipt.” The product’s promise is that nothing paid for is hidden, including a failure. Script and master-prompt calls on Gemini and Grok are paid. A rate-card line for those tokens, committed like any other op, is what makes the receipt match the doctrine.

8. **Decide what “1080p master” means, and make the cutter match it.** Effort **L**. README and the North Star say 1920×1080. `assembly.py` cuts 1280×720 at 24 fps. Flow can upscale server-side; Grok is scaled in ffmpeg. 23.976, Rec.709 legal range, and ProRes/DNxHR are not what the file is. Either the website and the README say “720p picture, 1080p when Google Flow upscales,” or the assembly finish actually emits 1920×1080 with a stated frame rate. Shipping the weaker file under the stronger name will be the first thing a filmmaker measures.

9. **Write down the loopback threat model, and stop bootstrapping an admin API on first GET.** Effort **M**. Local mode is acceptable on `127.0.0.1`. It is not acceptable if a port forward, a shared login, or `DIONY_BIND` exposes `:8788` and `:8790`. Refuse non-loopback binds unless `DIONY_AUTH_MODE=token`, and do not create `local-admin-team` as a side effect of reading `/api/v1/teams`. Gallery delete is the sharp edge.

10. **Delete the rest of the GPU product from the path a new engineer reads.** Effort **M**. Remove `apps/wan` and `apps/finish` from pytest paths, drop `diony_finish` from ruff, quarantine or hide the unused launch-governance routes, and stop setting `DIONY_DRY_RUN`. Align the four package versions with `0.22.0`. This does not change a film. It stops the next change from being built against a studio that no longer exists.

---

## Website brief

Audience, value, and features for a Diony.ai marketing site. Speak to one person at a desk, not to a platform team.

### Audience

Independent filmmakers and small studios who want a finished short from a sentence, on a Mac or a Windows PC, paying xAI or Google themselves. They care about the price before the render, and they want the files on their own disk. They are not shopping for a GPU cluster, a team workspace, or a browser app.

### Value propositions

Use these. They match what the software actually does.

1. **One line in. A priced film out.** The Director writes the script, shows the itemised cost, and waits.
2. **Nothing rolls until you see the price.** Clips, stills, and a retake allowance are on the lock. A failure that spent money stays on the receipt.
3. **Your keys. Your vault. Your machine.** No key form in the app. Media is downloaded and hashed locally. Provider links are not what you play.
4. **Two cameras, one look.** Grok Imagine or Google Flow, chosen once, used for the whole film.
5. **A master, not a folder of clips.** Cut, loudness-locked, narrated or spoken on camera, with SRT, VTT, an edit list, a QC packet, and the receipt.

The founder’s line **“Own your Studio | Produce at Cost”** fits this product and is clearer than “From idea to moving image.” It is not in the repo today. If the site uses it, the app and the README should use it too.

### Features worth a section

- Idea or pasted script, editable until lock.
- Reference stills, including one still as the opening frame.
- Cast dock: character sheets, approve a face, bring your own portrait.
- Styles: narrated, on-camera dialogue, or a mix. One narrator voice per film.
- Live run strip: quota pauses with a resume time, retake, skip a stubborn shot.
- Gallery: watch, read the prompt, download a ZIP, stitch, delete. Spend stays on the ledger after a delete.
- Readiness check before spending (`diony-director-check`).

### Features to leave off the homepage until they are true

- “Download for Mac / Download for Windows” as a one-click install. Those files are not published.
- Per-shot automatic camera choice. The product requires one camera per film. `START HERE.txt` currently says the opposite.
- 1080p as the default master on both cameras. Say “Google Flow can finish at 1080p; Grok is cut from 720p” until the cutter changes.
- Local GPU, team accounts, or “no third-party services.” Rendering is xAI or Google-through-useapi.net. The brain is Grok or Gemini.
- Gemini 2.5 Pro. The default writer is `gemini-3.1-pro-preview`.

### How someone gets the software today

There is no public installer and no `downloads.json`.

| Desk | What they actually do |
|---|---|
| Mac, from this repository | Double-click `dist/Diony.app`, or `Open Diony AI.command`. The committed build is arm64, ad-hoc signed, not notarized. macOS will warn. Right-click → Open. |
| Windows | Double-click `Build Diony AI.bat` once. That needs Python 3.11+ and produces `dist\Diony AI\Diony AI.exe`. Later, `Open Diony AI.bat`. SmartScreen will warn on an unsigned app. |
| Linux | `./open-diony.sh` from a source checkout, with Python 3.11+ and a system ffmpeg. This audit could not bind the `*.diony.local` names without root, and `diony-wizard` crashed on a missing `pkexec` until names setup was skipped. The services then answered on `http://127.0.0.1:8787`, `:8790`, and `:8788`. |
| Signed release (not in the tree) | Blueprint 17: `DionyAI-0.22.0-macOS-universal.dmg` and `DionyAI-Setup-0.22.0-windows-x64.exe`, after notarization and Authenticode. The site should appear only when those bytes and their hashes exist. |

After install, the person creates `~/.diony/keys.env` (`XAI_API_KEY`, or `USEAPI_TOKEN` plus `GEMINI_API_KEY` and `DIONY_FLOW_PLAN`). The first open asks for a password once to write the three hostnames. They pick a camera in Studio and continue to the Director.

A marketing site can take email or a license request now. It should not pretend the DMG is already on a CDN.
