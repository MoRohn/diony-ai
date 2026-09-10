<div align="center">

<img src="docs/brand/diony-banner.png" alt="Diony — From idea to moving image. Create with control." width="100%" />

<br />

**ONE LINE IN&nbsp;&nbsp;·&nbsp;&nbsp;A PRICED, FINISHED FILM OUT**

<br />

![Python 3.11+](https://img.shields.io/badge/Python-3.11+-0D0D0D?style=flat-square&logo=python&logoColor=F6F6F6)
![Engines](https://img.shields.io/badge/Engines-Grok_Imagine_·_Google_Flow-FF7A2F?style=flat-square)
![Master](https://img.shields.io/badge/Master-1080p_·_Rec.709-FF402F?style=flat-square)
![Platforms](https://img.shields.io/badge/Desktop-macOS_·_Windows_·_Linux-0D0D0D?style=flat-square)
![License](https://img.shields.io/badge/License-Proprietary-8A8A8A?style=flat-square)

<br />

[Quick start](#quick-start) · [How a film is made](#how-a-film-is-made) · [Engines and pricing](#engines-and-pricing) · [Configuration](#configuration) · [FAQ](#faq) · [Engineers](#engineers) · [Blueprints](blueprints/)

</div>

<img src="docs/brand/gradient-rule.png" alt="" width="100%" height="4" />

Diony AI is a desktop film studio that shoots on cloud video models. You type an idea, the **Director** writes the script, prices every shot, rolls the clips on **Grok Imagine** (xAI) or **Google Flow** (Veo 3.1 and Omni Flash through useapi.net), reviews each print against the script, cuts the film, narrates it, and hands you the master with its subtitles, edit list, QC packet, and receipt. Nothing rolls until you see the price. Nothing you paid for is hidden, including a failure that burned money.

No GPU is required. You bring your own provider keys, they never leave your machine, and every clip, frame, and film is downloaded into a local vault the moment it exists. The lineage, the prompts, and the costs are yours.

## The surfaces

| Surface | URL | Job |
|---|---|---|
| **Studio** | `http://studio.diony.local` | The front door. Choose the camera: Grok Imagine or Google Flow. Shows whether each provider key is loaded. |
| **Director** | `http://director.diony.local` | The room where films are made. Idea in, script out, every shot priced, faces approved, clips rolled, film cut and narrated. |
| **Producer** | `http://producer.diony.local` | The Gallery of finished clips and films. Watch, read the prompt that rolled, download, stitch, or delete, with every receipt. |

The Desk opens all three and lays the `*.diony.local` names on first launch. The whole flow is Studio → Director → Producer.

## Quick start

**1. Requirements**

- macOS 13+ or Windows 10/11 (Linux runs the Mac path). Python 3.11+ for a source checkout; the packaged app bundles its own runtime.
- `ffmpeg` and `ffprobe`: `brew install ffmpeg` on a Mac, `winget install Gyan.FFmpeg` on Windows, or point `DIONY_FFMPEG` / `DIONY_FFPROBE` at them. They cut, mix, and review every film. Diony also looks in the Homebrew, winget, Chocolatey, scoop, and `C:\ffmpeg\bin` homes when the app's PATH is bare.
- For the Grok Imagine camera: an xAI API key. For the Google Flow camera: a useapi.net token with a linked Google account, plus a Gemini API key so the script and the narrator are Google too. Both cameras on one desk is fine.

**2. Put the keys in one file**

Create `~/.diony/keys.env` on a Mac or `C:\Users\<you>\.diony\keys.env` on Windows (override the location with `DIONY_KEYS_FILE`) and keep it private:

```bash
mkdir -p ~/.diony && chmod 700 ~/.diony
cat > ~/.diony/keys.env <<'EOF'
# xAI — Grok Imagine video, Grok Imagine image, the Grok narrator voice, and the screenwriter brain
XAI_API_KEY=xai-...

# useapi.net — Google Flow (Veo 3.1, Omni Flash, Nano Banana stills, 1080p upscale)
USEAPI_TOKEN=user:...
DIONY_FLOW_EMAIL=you@gmail.com      # optional: pin one connected Flow account
DIONY_FLOW_PLAN=pro                 # plus | pro | ultra-99 | ultra-199 — prices Flow credits

# Google AI Studio — the Gemini brain (script, master prompt, likeness audit) and Gemini speech (narrator)
GEMINI_API_KEY=AIza...
EOF
chmod 600 ~/.diony/keys.env
```

An explicit environment variable always wins over the file. There is no key form in the UI by design. On Windows use PowerShell: `New-Item -ItemType Directory -Force $env:USERPROFILE\.diony`, then `notepad $env:USERPROFILE\.diony\keys.env` and paste the same lines.

**Which key does what.** Each camera has its own house. On Grok Imagine the xAI key shoots, writes the script with Grok 4.6, and narrates with the Grok voice API. On Google Flow the useapi.net token shoots and the Gemini key writes with Gemini 2.5 Pro and narrates with Gemini speech, so the film is Google end to end. When a camera's own brain key is missing the Director borrows the other house and says so in the header; when both houses are on the desk, the Brain and Voice pills offer either.

**3. Open Diony**

- Mac: double-click **Build Diony AI.command** to build and open `Diony.app`, or **Open Diony AI.command** for the source launcher.
- Windows: **Open Diony AI.bat** (or the signed installer, `DionyAI-Setup-<version>-windows-x64.exe`). Linux: `./open-diony.sh`.

The lobby installs the desk once, opens Studio, and asks once to lay the product names: your password on a Mac, a User Account Control prompt on Windows. Studio's **This computer** toggle (Mac | Windows) is preset to the desk it detected; every path, command, and launcher name Diony shows follows it, and you can flip it to read the instructions for the other desk. Pick a camera and press **Continue to the Director**.

**4. Prove the keys before spending**

```bash
diony-director-check            # offline: engine, keys found, plan, rate-card age, vault path
diony-director-check --live     # $0 auth probes: xAI model list, Flow accounts and credits
diony-director-check --roll-one grok --yes   # one 1 s 480p clip, about $0.08, lands in {vault}/director/live-check/
```

Optional staging, both free: `--stage-face-models` downloads OpenCV Zoo's YuNet and SFace (about 37 MB, SHA-256 pinned) so Cast Lock can measure faces; `--reconnect` re-links a signed-out Flow account from a live browser session.

## How a film is made

1. **Describe the film** in the Director console. One line is enough. Drop or paste reference stills; mark one as the opener to open the frame on it.
2. **Draft script** or **One shot**. The screenwriter brain (Gemini 2.5 Pro on the Google Flow camera, Grok 4.6 on Grok Imagine) writes a screenplay with characters, settings, and lines, then the Master Video Prompt stage derives the exact prompt every clip inherits. Drafting takes a minute or two on a reasoning model; the room shows the elapsed time. A one shot of any length chains into sequential clips that open on the previous clip's last frame.
3. **Edit the lines** on the script sheet. Every line and its authored shot is editable until the lock.
4. **Lock script & price the clips.** Scenes are packed into route-length takes (15 s on Grok, 10 s on Omni Flash), one spoken line per take. The lock reply itemises clips, plates and character sheets, and a revision allowance, and states the session total. Nothing has rolled yet.
5. **Approve the cast.** Character sheets draw in the background; the cast dock shows each face with its measured verdict. Approve one, or **Approve all**. Under **Cast: Locked** the approved sheet rides every prompt verbatim and cast keyframes are composed before each clip.
6. **Run & Assemble All**, **Run & Assemble Selected**, or **Run, Assemble & Upscale All** (Google Flow takes finish at 1080p server-side, free on a paid Google plan). The console folds into a live run strip. A provider quota pause is a card with a resume time, never a failure. A refused clip gets its opening frame redrawn on **Retake**; **Skip shot** leaves a stubborn clip out and the run carries on; **Retry all** re-queues every failed clip.
7. **The cut.** Clips are reviewed (delivery, duration, audio, sampled frames, identity against the approved sheet, spoken lines when a whisper model is staged), assembled with fades and a room-tone floor, loudness-locked, and narrated in post by one cast voice from the camera's house (Gemini speech on Google Flow, the Grok voice API on Grok Imagine) with cues placed at each line's mark. The film ends when the last line ends.
8. **Deliverables and receipt.** `film.mp4`, `film.srt`, `film.vtt`, `edl.json`, `assembly-map.json`, and `qc-packet.json` land beside each other in the vault. The receipt lists estimate versus actual per operation. The Producer's Finished Clips gallery lists every clip and the film with its spend.

### Console controls

| Pill | Choices | What it does |
|---|---|---|
| **Provider** | Grok Imagine · Google Flow | The camera the film rolls on. It opens on the Studio choice; one camera shoots the whole film. |
| **Duration** | m:ss, up to 180 minutes | Runtime, starting at 0m 0s. Nothing rolls until it is set: a run at 0m 0s is refused with a tip. Seconds for one shot (long one-shots chain); minutes for a scripted piece. |
| **Res** / **Aspect** | 720p · 480p / 16:9 · 9:16 | Render resolution and program shape, held by every shot, plate, and the cut. |
| **Grok cam** / **Grok atmo** | Authored + the Imagine 1.5 lexicon | Camera moves and atmosphere on the Grok route. Inline shorthand works too (`/pushin`, `/dust`). |
| **Flow style** / **Flow cam** | Baseline + worlds and camera commands | WORLD and CAMERA templating on the Google route (`/filmic`, `/orbit`). |
| **Style** | Narrated · Dialogue · Mix | One post narrator; characters speak on camera; or both where each belongs. Fixed at the lock. |
| **Cast** | Standard · Locked | Cast Lock: the approved identity card at priority 0 on every prompt. Fixed at the lock. |
| **Brain** | Auto · Grok · Gemini | Which model writes. Auto follows the camera; a named house needs its key. Shown only when both keys are on the desk. |
| **Voice** | Auto + every roster the desk holds a key for | One narrator per film, cast from the tone or chosen by name (Grok voices, or `Gemini · Kore` and company). Changeable until the cut. |

The header meter reads in the camera's own currency: dollars for Grok Imagine as metered by xAI, plan credits for Google Flow. Total is lifetime spend on this desk; today is today's.

## Engines and pricing

Prices live in versioned JSON rate cards at `apps/director/src/diony_director/ratecards/`, each naming its source and fetch date. A card older than 45 days is reported as stale on every estimate. Override the directory with `DIONY_RATECARD_DIR`.

**Grok Imagine (xAI), metered per second.** Default video model `grok-imagine-video-1.5`; stills and character sheets on `grok-imagine-image-2.0`; the narrator on the Grok voice API; the screenwriter on `grok-4.6`. All on the one xAI key.

| Model | Unit | USD |
|---|---|---|
| `grok-imagine-video-1.5` | second at 480p / 720p / 1080p | 0.08 / 0.14 / 0.25 |
| `grok-imagine-video` | second at 480p / 720p | 0.05 / 0.07 |
| `grok-imagine-image-2.0` | still at 1K / 2K | 0.04 / 0.08 |

Clips run 1 to 15 seconds. Up to 3 reference images. Native audio. The 1.5 model shoots; the 1.0 model carries the extension and edit lanes.

**Google Flow via useapi.net, priced in Google's published credits.** The script, the master prompt and the likeness audit run on Gemini 2.5 Pro and the narrator on Gemini speech, both on the Gemini API key, billed by Google AI Studio on its own meter. Default video model `omni-flash` (Omni 1.1 Flash: audio-native, voices, characters, first and last frames, 4/6/8/10 s at 360p or 720p). Veo 3.1 tiers stay reachable by explicit model choice and carry the extension lane. The credit costs below are the ones Google charges your account per generation; the Director converts them to dollars from your plan's price per credit, which is a model of your subscription, never Google's meter.

| Model | Unit | Credits | Ultra plans |
|---|---|---|---|
| `omni-flash` | 720p clip at 4 / 6 / 8 / 10 s | 7 / 10 / 12 / 15 | same |
| `omni-flash` | 360p clip at 4 / 6 / 8 / 10 s | 4 / 5 / 6 / 7 | same |
| `omni-flash` | video edit (v2v) | 40 | same |
| `veo-3.1-lite` | one generation or extend (8 s; 4 and 6 s on Ultra) | 10 | 5 |
| `veo-3.1-lite-low-priority` | same lane, queued | 10 | 5, free and unlimited on Ultra $199.99 |
| `veo-3.1-fast` | one generation or extend | 20 | 10 |
| `veo-3.1-quality` | one 8 s generation or extend | 100 | 100 |
| `video-upscale` | 720p / 1080p / 4K finish | 0 / 0 / 50 | 4K needs Ultra |
| `nano-banana-2`, `-lite`, `-pro` | one still | included | included |

| Google AI plan | Per month | Credits per month | Modeled price per credit |
|---|---|---|---|
| Plus | $7.99 | 200 | $0.040 |
| Pro | $19.99 | 1,000 | $0.020 |
| Ultra | $99.99 | 10,000 | $0.010 |
| Ultra | $199.99 | 25,000 | $0.008 |

Credits refresh monthly and do not roll over; a free daily allowance exists but is not modeled. useapi.net is a flat subscription on top and adds no per-generation fee; captcha solves are metered separately (about $0.003 each after the free ones) and appear as their own line on every Flow estimate. Every Flow API call passes one process-wide pacing gate (`DIONY_FLOW_REQUEST_GAP_S`, default 5 s) so a wave never trips the throttle. Sources: [Google Flow Help, Manage your Google Flow credits](https://support.google.com/flow/answer/16526234) and the [useapi.net Google Flow API docs](https://useapi.net/docs/api-google-flow-v1), both read 2026-09-10.

**Cost doctrine.** No priced operation exists without a reconciling estimate. Estimates commit on submit, actuals reconcile on completion, and a failed job that burned money stays on the receipt. A 429 pauses the run until the provider's window resets and spends nothing. Prompts are fitted to the 4096-character provider limit before any request leaves, with dialogue, camera, and the identity card untouchable.

## What the Director guards

- **Likeness.** A real person recognised in an idea or a still is described, never named, in every prompt. Google Flow refuses named people beside a reference picture; the guard closes that before render (`DIONY_LIKENESS_AUDIT`).
- **Continuity.** A film-level Look Bible and style frames ride every prompt; setting plates open scenes; chained clips open on the previous last frame; identity is scored against the approved sheet, never the previous shot.
- **Speech.** Location tracks are speechless by law on the narrated style; a provider-invented voice is measured (with `DIONY_WHISPER_MODEL`) or muted with a named receipt. Narration cues never overlap.
- **The script reaches the cut.** A provisional prompt never spends. A cut that expects lines and carries none is refused.
- **One film per room.** A new script retires the earlier chain; its prints stay in the Gallery and on the ledger.

The doctrine is written down in [Blueprints 25 to 33](blueprints/README.md).

## Where everything lands

The vault is `DIONY_VAULT`, else `/diony/vault` when it exists, else `apps/producer/data` in a checkout.

```
{vault}/director/{session}/   state.json, every clip as {job}.mp4, uploads/, frames/,
                              film.mp4, film.srt, film.vtt, edl.json, assembly-map.json, qc-packet.json
{vault}/director/live-check/  --roll-one output
{vault}/.cluster/engine.json  the Studio engine choice
{vault}/.control/             the cost ledger and control plane (sqlite)
{vault}/models/opencv-zoo/    staged YuNet + SFace
```

Provider URLs are never stored or played. Completed media is downloaded and content-hashed the moment it completes.

## Configuration

Everything is an environment variable; the keys file is loaded first with set-default semantics.

| Variable | Default | Meaning |
|---|---|---|
| `XAI_API_KEY` | unset | xAI bearer token: Grok video, image, voice, and the brain |
| `USEAPI_TOKEN` | unset | useapi.net bearer token for Google Flow |
| `DIONY_FLOW_EMAIL` / `DIONY_FLOW_PLAN` | Flow picks / `pro` | Pin one Flow account; price its credits |
| `DIONY_KEYS_FILE` | `~/.diony/keys.env` | Where the keys live |
| `GEMINI_API_KEY` | unset | Google AI Studio key: the Gemini brain and Gemini speech narrator, the Google Flow camera's own house |
| `DIONY_GEMINI_BRAIN_MODEL` / `DIONY_GEMINI_TTS_MODEL` | `gemini-2.5-pro` / `gemini-2.5-flash-preview-tts` | The Gemini writer and speech models |
| `DIONY_GEMINI_VOICE` / `DIONY_GEMINI_VOICE_ROSTER` | cast per film | Pin a Gemini narrator voice (`gemini:Kore`); override the roster as JSON |
| `DIONY_DIRECTOR_BRAIN_URL` / `_MODEL` / `_KEY` | resolved by camera | Force any OpenAI-compatible chat endpoint to write the script. Set the URL empty to run the deterministic template writer. Otherwise the brain is the camera's house, or the desk's Brain pill choice (`{vault}/.cluster/brain.json`). |
| `DIONY_DIRECTOR_BRAIN_TIMEOUT` | `600` | Seconds per brain call; the draft is asynchronous |
| `DIONY_XAI_BASE` / `DIONY_USEAPI_BASE` | provider defaults | API bases, for mocks and proxies |
| `DIONY_FLOW_REQUEST_GAP_S` / `DIONY_FLOW_UPSCALE_PARALLEL` | `5` / `4` | Flow pacing gap; parallel 1080p finishes |
| `DIONY_PROMPT_LIMIT_GROK` / `_GOOGLE_FLOW` | `4096` | Prompt character caps |
| `DIONY_NARRATOR_BACKEND` / `DIONY_NARRATOR_CLI` | the camera's house | Force `grok`, `gemini`, or `cli` (an on-host command template with `{text}`, `{output}`, `{voice}`) |
| `DIONY_GROK_VOICE` / `DIONY_GROK_VOICE_ROSTER` | cast per film | Pin a narrator voice; override the roster as JSON |
| `DIONY_WHISPER_MODEL` | unset | faster-whisper model path; turns the speech guard into a measurement |
| `DIONY_FACE_ID_DIR` | `{vault}/models/opencv-zoo` | Staged face models for Cast Lock |
| `DIONY_LIKENESS_AUDIT` | `1` | Second-pass audit for unconfessed real names |
| `DIONY_FFMPEG` / `DIONY_FFPROBE` | PATH | Explicit media tool paths |
| `DIONY_VAULT` | see above | Vault root |
| `DIONY_DIRECTOR_PORT` | `8790` | Director bind port |
| `DIONY_DIRECTOR_DRY_RUN` | unset | `1` refuses every paid run with a 409 |
| `DIONY_ENGINE` | cloud | Only `local_legacy` turns the cloud engine off |

## FAQ

**What do I need to make a film?**
A Mac, Windows, or Linux machine with ffmpeg, and one camera's keys: an xAI API key (Grok Imagine, the Grok narrator voice, and the Grok brain), or a useapi.net token linked to a Google account with a Google AI plan plus a Gemini API key (Google Flow, Gemini speech, and the Gemini brain). No GPU, no model downloads. Each camera is complete on its own keys; with both on the desk the Brain and Voice pills let a Flow film borrow Grok's writer or narrator and the other way round.

**Which camera should I pick?**
Studio asks you to choose one, and one camera shoots the whole film so every clip carries one look. Pick Grok Imagine when you want exact per-second pricing, 15 s takes, and the fastest drafts. Pick Google Flow when you want Omni Flash's native voices, registered characters, first-and-last-frame shots, Veo 3.1, and the free 1080p finish. The choice can change any time on the Studio page or the Director's Provider pill; a room already rolling keeps its camera.

**How much does a film cost?**
The lock reply tells you before anything rolls, itemised: clips, plates and character sheets, and a revision allowance for retakes. As a guide, one minute of 720p picture costs about $8.40 on Grok Imagine 1.5 (60 s at $0.14) and, cut as six 10 s Omni Flash takes, 90 Flow credits (about $1.80 on Pro, $0.90 on Ultra $99.99). Character sheets and plates add a few cents on Grok and are included on Flow. Grok is billed by xAI per second; Flow is charged in credits against your Google AI plan's monthly pool, priced here from the plan you declare in `DIONY_FLOW_PLAN`.

**Where do Google Flow credits come from, and are your numbers Google's?**
Credits come from your Google AI plan (Plus 200, Pro 1,000, Ultra $99.99 10,000, Ultra $199.99 25,000 per month, plus a free daily allowance) and are spent on Google's side per generation. The Director quotes the per-generation credit costs Google publishes and converts them to dollars from your plan's price per credit; that conversion is a model of your subscription, not a meter Google runs. The receipt shows credits and dollars side by side.

**Why did my run pause?**
A provider answered 429: the Flow account's quota window, a per-model daily limit, a captcha, or xAI throttling. Nothing was spent. The run card shows the reason and the resume time in your local clock, and the queued clips roll on their own when the window opens. Flow calls are also paced (one at a time, five seconds apart) so a wave never triggers the pause in the first place.

**Why was a clip refused, and what does Retake do?**
Google Flow refuses a prompt that names a recognisable person beside a reference picture, and both providers moderate content. The likeness guard describes people instead of naming them before the prompt leaves, and a refusal on a chained clip redraws the opening frame without the recognisable face, then retries from your own stills alone. After three redraws the room tells you the prompt itself is what the filter matches. Skip shot leaves a stubborn clip out; the cut ignores it.

**How long does a film take?**
Drafting on a reasoning model takes one to two minutes. Clips render in parallel waves; a 10 s Omni Flash clip or a 15 s Grok clip typically lands in one to three minutes, chained one-shots are sequential because each link opens on the previous last frame. A one-minute film is usually cut, narrated, and in the Gallery within ten to fifteen minutes; long runtimes scale with the number of clips and the provider's queue.

**Can I bring my own script?**
Yes. Paste a screenplay or a beat list into the console and press Draft script: authored lines are realised verbatim, authored shots ride each take, and `(O.S.)` lines are heard from off screen. You can also paste a full master prompt for a one-shot. Every line stays editable on the script sheet until the lock.

**Do characters speak, or is it narrated?**
Both. The default style is Narrated: one Grok voice reads every line onto the finished cut, the location tracks stay speechless, and the cues land at each line's mark. Dialogue puts the lines into the generation prompt so the character speaks on camera with the provider's native audio. Mix uses on-camera dialogue where a character is in frame and post narration for voice-over. Cast Lock keeps a face consistent across the film; Voice picks the narrator.

**Is my footage uploaded anywhere or kept by anyone?**
Prompts and reference stills go to the provider you chose, because that is where the clip is rendered; nothing goes anywhere else. Finished media is downloaded to your vault the moment it completes, provider URLs are never stored or played, keys stay in `~/.diony/keys.env` on your machine, and the ledger is a local SQLite file. Delete a session from the Gallery and its media is gone; the spend stays on the receipt.

**What is the 1080p finish?**
Providers render at 720p. Run, Assemble & Upscale All sends every Flow take through Google's server-side upscale, free at 1080p on a paid plan (4K costs 50 credits and needs Ultra), and cuts the film at 1920×1080. Grok takes are conformed up at the cut. A finish that comes back short fails its review and the native take is used instead.

**What happens when a rate card is stale?**
Every estimate says so, and the run still proceeds. Cards carry a fetch date and go stale after 45 days; update the JSON under `apps/director/src/diony_director/ratecards/` or point `DIONY_RATECARD_DIR` at your own copy. The receipt always shows what the provider actually charged where the API reports it.

**Why does the first launch ask for my password?**
To write `studio.diony.local`, `director.diony.local`, and `producer.diony.local` into the hosts file and bind the name router on port 80. It happens once. Cancel it and the lobby's Continue retries; the services still run on `:8787`, `:8790`, and `:8788` meanwhile.

**Does the Google Flow camera use Grok for anything?**
Not when a Gemini key is on the desk: the script, the master prompt, the likeness audit and the narrator all run on Google, and the header's brain chip says `brain Gemini · gemini-2.5-pro`. Without a Gemini key the Director borrows the xAI key for the script and the narrator when it exists, and the chip says so; with neither, the deterministic template writer drafts and the cut needs the Dialogue style or a narrator command.

**Does it run the same on Windows as on a Mac?**
Yes. The runtime already speaks each desk's language: the product names go into the hosts file behind one password prompt on a Mac or one User Account Control prompt on Windows, the data directory is `~/Library/Application Support/Diony AI` or `%LOCALAPPDATA%\Diony AI`, Python and ffmpeg are found in each desk's usual homes (Homebrew and the Python.org frameworks; winget, Chocolatey, scoop, `C:\ffmpeg\bin`), and the launchers are `Open Diony AI.command` and `Open Diony AI.bat`. Studio's **This computer** toggle only changes what the product tells you: the keys path, the setup commands, the ffmpeg install line, the launcher name, and the missing-ffmpeg message inside the Director. It defaults to the detected desk.

**Can I use a local GPU instead of the cloud engines?**
No. Diony shot on local DGX Spark clusters and leased GPUs until September 2026; that lane was removed from the product on 2026-09-10. The two cameras are Grok Imagine and Google Flow, and the desk needs no GPU at all. The history is in the [changelog](CHANGELOG.md).

**How do I start over?**
＋ NEW SESSION in the Director masthead opens a fresh room; the ledger keeps every dollar. A room you left with a priced script or unfinished clips comes back whole the next time the Director opens, with its cards, its batch bar, and a Retry on every failed clip. Pasting a new script into a room that holds a film retires the earlier chain rather than stacking the two.

## Engineers

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e packages/contracts -e apps/wizard -e apps/producer -e apps/director -e apps/desk
pip install ruff pytest

diony-desk               # the lobby: starts Studio, Producer, Director and lays the names
diony-wizard             # Studio alone, :8787
diony-director           # Director alone, :8790
diony-producer           # Producer alone, :8788

pytest                   # the whole repository, offline (root pyproject.toml + conftest.py)
ruff check . && ruff format --check .
```

Tests never open a socket: the root `conftest.py` blocks every non-loopback connection for the session (`DIONY_ALLOW_NETWORK=1` only for a deliberate live run). Never run two pytest invocations at once; the desk's instance-lock tests deadlock across processes. Tests that cut real media skip without ffmpeg.

**Packaged app.** `Build Diony AI.command` stages a secret-free payload, hashes it, bundles a runtime, and opens `dist/desktop/{version}/development/Diony.app`. Rebuild after every change you want to test in the app; a rebuild is skipped when nothing changed. The version is `packaging/desktop/version.json`. Signed, notarised releases come from `scripts/release/` (Blueprint 17).

**UI stamps.** Each surface's static assets carry a `?v=` stamp that the desk uses to detect a stale build. Bump the Director stamp in `apps/director/src/diony_director/static/index.html` when you change its UI; the Producer and Studio stamps are pinned by tests, update the pins with them.

**Rules of the house.** Anything slow (a brain call, a provider submit) runs in a worker and is polled; nothing slow rides a request through the name router. Never use native `confirm()` or `prompt()` in a Diony UI. Pricing lives in JSON, never in code. See [AGENTS.md](AGENTS.md) for the full operating notes.

## Repository map

```
apps/director/          The Director: sessions, screenwriter, master prompt, splitter, compiler,
                        providers/{grok,flow}, review, revisions, assembly, narration, deliverables,
                        ledger, rate cards, cast lock, likeness guard, static console
apps/wizard/            Studio: the opening page (camera choice) and the :80 name router
apps/producer/          Producer: the Finished Clips gallery, the cost ledger and control plane the
                        Director's receipts read, and the review services the Director imports (face
                        identity, frame and speech QC)
apps/desk/              First open: lobby, service supervision, packaged-app payload staging
packages/contracts/     Pydantic contracts and JSON Schema: Director ops, cost estimates, production
                        packages, the launch control plane, the engine choice, look and subtitles
blueprints/             The numbered constitution; 25–33 are the cloud era
packaging/, scripts/    Desktop payload, PyInstaller spec, signed release builders
docs/brand/             Banner, mark, gradient rule
```

## Design system

The Diony identity is the bar mark: three gradient bars converging into charcoal and a rounded D. Charcoal `#0D0D0D`, Light `#F6F6F6`, and the gradient `#FFA23A → #FF7A2F → #FF402F → #E43B4B`; Archivo Expanded for display, Inter for interface, IBM Plex Mono for chips. Every surface is dark with orange primary actions. Brand, motion, and accessibility are governed by [Blueprint 15](blueprints/15-creative-design-system.md); assets live in [`docs/brand/`](docs/brand/) and [`packaging/desktop/icons/`](packaging/desktop/icons/).

## License

Diony AI software and Blueprints are proprietary; see [LICENSE](LICENSE). Generation runs on third-party services under their own terms: xAI (Grok Imagine and the Grok voice API) and Google Flow through useapi.net. Bundled and optional open-source components, including OpenCV Zoo face models and faster-whisper, keep their licenses; see [NOTICE](NOTICE). Generated content is the licensee's responsibility.

<br />

<div align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/brand/diony-mark-reversed.svg" />
    <img src="packaging/desktop/icons/diony-mark.svg" alt="" width="56" />
  </picture>
  <br />
  <sub><b>Diony AI</b> — From idea to moving image. Create with control.</sub>
</div>
