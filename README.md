# Diony AI

**One line in. A priced, reviewed, finished film out.**

Diony is a browser-based local film studio that shoots through cloud video APIs. Studio chooses one camera, Director writes and prices the film, and Producer holds the finished Gallery. Generated clips are downloaded into a local vault immediately; provider URLs are never the source of record.

No local model or GPU is required. Diony supports Grok Imagine through xAI and Google Flow through useapi.net, with room for additional provider adapters behind the same contracts and cost gates.

## Quick start

Requirements:

- Python 3.11 or newer
- ffmpeg and ffprobe on `PATH`, or `DIONY_FFMPEG` and `DIONY_FFPROBE`
- At least one configured camera house

Put provider keys in `~/.diony/keys.env`. Explicit environment variables override the file.

```dotenv
# Grok Imagine camera, Grok screenwriter, and Grok narrator
XAI_API_KEY=xai-...

# Google Flow camera through useapi.net
USEAPI_TOKEN=user:...
DIONY_FLOW_EMAIL=you@example.com
DIONY_FLOW_PLAN=pro

# Gemini screenwriter and narrator for the Google camera
GEMINI_API_KEY=AIza...
```

Google Flow also requires captcha capacity. useapi.net includes 300 solves
with the first linked Flow account; after those are exhausted, configure a
provider such as CapSolver through useapi.net's
[captcha-provider endpoint](https://useapi.net/docs/api-google-flow-v1/post-google-flow-accounts-captcha-providers).
The solver key belongs in that useapi.net account configuration, not in
`~/.diony/keys.env`. Verify the linked account and masked solver configuration
with `diony-director-check --live` before a large run.

Keep the file private:

```bash
mkdir -p ~/.diony
chmod 700 ~/.diony
chmod 600 ~/.diony/keys.env
```

Then run:

```bash
./open-diony.sh
```

On macOS you may also double-click `Open Diony AI.command`; on Windows, double-click `Open Diony AI.bat`. These are thin source launchers, not packaged applications. They create `.venv` when needed, install the four editable packages, start the three loopback services, and open Studio in the default browser.

Leave the terminal open while Diony runs. Press `Ctrl-C` to stop all three services.

## Browser surfaces

| Surface | Default URL | Job |
|---|---|---|
| Studio | `http://127.0.0.1:8787` | Choose Grok Imagine or Google Flow and inspect key readiness. |
| Director | `http://127.0.0.1:8790` | Write, price, shoot, review, revise, assemble, narrate, and deliver. |
| Producer | `http://127.0.0.1:8788` | Browse the compact ten-across Finished Clips sheet, expand prompts on demand, select all, download one ZIP, stitch clips, or delete selected media while receipts remain. |

No hosts-file changes, administrator prompt, name router, desktop bundle, or installer is involved.

## What is preserved

The lightweight runtime keeps the full cloud production workflow:

1. Explicit one-camera selection; there is no automatic provider route.
2. Script lock and cast approval human gates.
3. Estimates before any priced operation, live spend while work runs, and a reconciled receipt afterward.
4. Look Bible, Cast Lock, continuity references, prompt fitting, provider capability checks, revisions, and retakes.
5. Sampled-frame, identity, speech, duration, audio, and delivery review.
6. Assembly, narration or dialogue/mix styles, loudness lock, subtitles, edit data, QC packet, and local Gallery.
7. Immediate download and hashing of provider results into the local vault.

For a reusable character already created in Google Flow, lock the script with
**Cast: Locked**, open its BIBLES card, choose **Use existing Flow character**,
and select it from the relay-visible Flow library (or paste its character page
URL / API reference). Diony verifies the character against `DIONY_FLOW_EMAIL`
and uses that account-scoped identity on compatible Flow clips.

## Environment variables

### Provider keys

| Variable | Purpose |
|---|---|
| `XAI_API_KEY` | Grok Imagine video/stills, Grok screenwriter, and Grok narration. |
| `USEAPI_TOKEN` | Google Flow access through useapi.net; captcha solver keys are configured on the useapi.net account, not stored by Diony. |
| `GEMINI_API_KEY` | Gemini screenwriter, likeness audit, and Gemini speech. |
| `DIONY_FLOW_EMAIL` | Optional linked Flow account selection. |
| `DIONY_FLOW_PLAN` | Flow credit model: `plus`, `pro`, `ultra-99`, or `ultra-199`; default `pro`. |
| `DIONY_KEYS_FILE` | Alternate key file; default `~/.diony/keys.env`. |

### Local runtime

| Variable | Default | Purpose |
|---|---|---|
| `DIONY_VAULT` | `<checkout>/.diony-vault` from the launcher | Local clips, films, state, and receipts. |
| `DIONY_PRODUCER_VAULT` | Same as `DIONY_VAULT` | Compatibility alias; keep both on one root. |
| `DIONY_BIND` | `127.0.0.1` | Service bind address. |
| `DIONY_WIZARD_PORT` | `8787` | Studio port. |
| `DIONY_DIRECTOR_PORT` | `8790` | Director port. |
| `DIONY_PRODUCER_PORT` | `8788` | Producer port. |
| `DIONY_STUDIO_URL` | Derived loopback URL | Browser-facing Studio link. |
| `DIONY_DIRECTOR_URL` | Derived loopback URL | Browser-facing Director link. |
| `DIONY_PRODUCER_URL` | Derived loopback URL | Browser-facing Producer link. |
| `DIONY_NO_BROWSER` | unset | Set to `1` to start services without opening a tab. |
| `DIONY_FFMPEG` | `ffmpeg` on `PATH` | Explicit ffmpeg executable. |
| `DIONY_FFPROBE` | `ffprobe` on `PATH` | Explicit ffprobe executable. |

### Optional brain, voice, and quality overrides

- `DIONY_DIRECTOR_BRAIN_URL`, `DIONY_DIRECTOR_BRAIN_KEY`, `DIONY_DIRECTOR_BRAIN_MODEL`
- `DIONY_GEMINI_BASE`, `DIONY_GEMINI_BRAIN_MODEL`, `DIONY_GEMINI_TTS_MODEL`, `DIONY_GEMINI_VOICE`
- `DIONY_RATECARD_DIR`
- `DIONY_FLOW_REQUEST_GAP_S`
- `DIONY_WHISPER_MODEL`
- `DIONY_FACE_DETECT_MODEL`, `DIONY_FACE_ID_MODEL`

Run the readiness CLI before spending:

```bash
source .venv/bin/activate
diony-director-check
diony-director-check --live
```

`--live` performs non-generation provider probes. A paid one-clip proof requires an explicit confirmation:

```bash
diony-director-check --roll-one grok --yes
```

## Development

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e packages/contracts -e apps/wizard -e apps/producer -e apps/director
pip install pytest ruff

pytest
ruff check .
ruff format --check .
```

The offline test guard blocks non-loopback sockets. Set `DIONY_ALLOW_NETWORK=1` only for an intentional live provider check. Never run two pytest invocations concurrently.

## Repository map

| Path | Role |
|---|---|
| `apps/wizard` | Studio camera selection and key readiness. |
| `apps/director` | Cloud provider orchestration, pricing, review, assembly, narration, and deliverables. |
| `apps/producer` | Gallery plus shared ledger, control plane, media, identity, and quality services. |
| `packages/contracts` | Pydantic contracts and generated JSON Schemas. |
| `scripts/run_local.py` | Lightweight three-service localhost supervisor. |
| `blueprints` | Product constitution and operational doctrine. |

The retired local GPU lane and desktop packaging history remain available in Git history rather than in the working product.
