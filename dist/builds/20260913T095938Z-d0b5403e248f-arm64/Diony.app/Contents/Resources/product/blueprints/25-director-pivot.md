# Blueprint 25 — The Director Pivot

Status: in force since 2026-08-31; amends Blueprint 00 (locked truth 5, "never cloud-by-default") for the Director lane

Owner: Diony AI

Applies to: the Studio opening page, `apps/director`, the Producer Gallery, the Desk landing, `packages/contracts` (`director.py`), the rate cards, every Blueprint numbered 26 and above

Depends on: Blueprint 00 (gates and quality bar), Blueprint 10 (quality mandate), Blueprint 16 (launch control plane and cost ledger), Blueprint 24 (montage intelligence: review families, decision log)

## Product decision

Diony generated on a local cluster because, in 2026's first half, that was the only
way to own the negative, the prompts, the lineage, and the cost. The cloud video
models that shipped through the summer changed the trade: Grok Imagine (xAI) meters
by the second with native audio, edits, and extensions; Google Flow (Veo 3.1 and
Omni Flash, reachable through useapi.net) locks voices and characters, takes first and
last frames, and finishes at 1080p on the server. A desk with two API keys now makes
a better film than a four-node crate, in minutes instead of days, and the price of
every shot is known before it rolls.

So the product pivoted. **Generation is cloud by default.** The reasoning models
stay where they were, as orchestration and review, never as the picture. The three
surfaces keep their names and their brand, but their jobs change:

| Surface | Before | After |
|---|---|---|
| Studio | Crate-to-cluster rail: Site, Connect, App, Ready, Sign | The opening page: choose the camera (Grok Imagine or Google Flow), see which keys are loaded, continue to the Director. |
| Producer | Idea, Production, Gallery over a signed GPU | The Gallery: one card per finished production — the cut film as its face, its clips as piece buttons that open from the card — with spend, Select all / Clear all, one-ZIP download, stitch, and two-step deletion; the launch control plane beside it. |
| **Director** | did not exist | The primary UI. A chat-first filmmaker that writes, prices, shoots, reviews, cuts, and narrates. |

The Desk lands on Studio, which hands the user to the Director, which hands the film
to Producer. `http://director.diony.local` (`:8790`) is a full product name laid
beside the other two by the same router.

## 1. What does not change

- **The human gates.** Bible (the script lock and the cast approval) and distribution.
- **The quality bar.** Blueprint 10 stands. Identity drift is a QC fail, not a note.
  Every print carries sampled-frame evidence; every automatic choice lands in the
  decision log; every master passes a final review.
- **Contracts first.** If a field is not on a `DirectorOp`, `ClipUnit`, or
  `ClipReview`, it does not exist for production.
- **The vault.** Every clip, frame, and film is downloaded the moment it completes
  and content-hashed. Provider URLs are never stored or played.
- **No hidden spend.** A failed job that burned money stays on the receipt.

## 2. Provider doctrine

| | Grok Imagine (xAI) | Google Flow (useapi.net) |
|---|---|---|
| Access | `XAI_API_KEY`, REST at `https://api.x.ai` | `USEAPI_TOKEN`, REST at `https://api.useapi.net/v1/google-flow`, a linked Google account on a Google AI plan |
| Pricing basis | metered, USD per second and per still | credits per generation from Google's published table, converted to USD from the declared plan (`DIONY_FLOW_PLAN`); never presented as Google's meter |
| Video models | `grok-imagine-video-1.5` (shoots; t2v, i2v, up to 3 refs, native audio, 1–15 s, 480p/720p/1080p), `grok-imagine-video` (extension and edit lanes) | `omni-flash` (default; audio-native, voices, characters, first+last frame, v2v edit, 4/6/8/10 s at 360p/720p), `veo-3.1-lite` / `-fast` / `-quality` (8 s; 4 and 6 s on Ultra; extension lane) |
| Stills | `grok-imagine-image-2.0` | `nano-banana-2` (included in the plan) |
| Brain | Grok 4.6 on the xAI key (`diony_director.brain`) | Gemini 3.1 Pro (preview, `gemini-3.1-pro-preview`) on `GEMINI_API_KEY` through Gemini's OpenAI-compatible endpoint; borrows Grok when the Gemini key is missing and says so |
| Voice | the Grok voice API (`/v1/tts`) | Gemini speech (`generateContent` with `responseModalities: AUDIO`, prebuilt voices `gemini:Kore` and company) on the same Gemini key; Omni's native voices under the Dialogue and Mix styles |
| Finish | conformed up at the cut | `POST /videos/upscale`, free at 1080p, 50 credits at 4K (Ultra) |
| Prompt limit | 4096 characters (live 400 otherwise) | 4096 characters, conservative |
| Pacing | none needed | one process-wide gate, requests never overlap, `DIONY_FLOW_REQUEST_GAP_S` (5 s) between them |
| Quota | 429 pauses the run until the window resets | 429 pauses the run; the documented reasons are translated to plain words |

**Each camera is a house.** The camera's own brain and narrator are the default (Gemini on Google Flow, Grok on Grok Imagine), resolved by `diony_director.brain` and `narration.backend()`; the desk's Brain pill (`{vault}/.cluster/brain.json`) and a voice id that names a house (`gemini:Kore`, or a Grok roster name) may cross houses when both keys exist. A missing house key borrows the other with a stated reason; nothing guesses a key.

**One camera per film.** Studio requires an explicit choice and there is no automatic route: the camera Studio chose shoots every clip of the film, picture and dialogue alike, so one look holds. A repeat failure on one provider may swap a single retake to the other by the fix ladder; the film's camera does not change.

**Capability matrix.** `diony_director.capabilities` is the authority for what each
model accepts (modes, reference count, character count, durations, voices, extension
and edit lanes, resolutions). An illegal combination is refused at compile or before
submit, never discovered as a provider 400 after money is committed. Frames and
references are mutually exclusive on every model.

## 3. The cost engine

1. **No priced op without an estimate.** `DirectorOp` refuses a priced operation
   without a provider, a model, and a `CostEstimate` whose lines reconcile to its
   total.
2. **Rate cards are documents.** JSON under `diony_director/ratecards/`
   (`DIONY_RATECARD_DIR` overrides), each naming its source, its official source, its
   fetch date, and a staleness window (45 days). A stale card is reported on every
   estimate and never fatal. Pricing never lives in code.
3. **Estimate, live, receipt.** Estimates commit on submit as job forecasts in the
   launch control plane's `CostAnalyticsService`; actuals reconcile on completion;
   `GET …/sessions/{sid}/receipt` lists estimate versus actual per op with a
   `billed_after_failure` flag.
4. **Budgets.** A session ceiling (`max_usd`) holds the batch at the ceiling instead of
   overspending; autopilot requires one.
5. **Durations quantize up.** Trimming surplus in assembly is free; a clipped word is a
   retake.

## 4. The Director

The Director is one FastAPI service (`diony-director`) with one static console. Its
loop:

```
idea + stills + duration
  → master prompt (Blueprint 27)       one brain call, the pre-split artifact
  → screenplay (scenes, lines, cast)   the screenwriter brain, or the template when no brain
  → split into takes (Blueprint 33)    route-length takes, one line per take
  → lock: price every op               clips + plates + character sheets + allowance
  → cast approval (Blueprint 32)       the human gate on faces
  → run: waves, chains, retakes        submitter and advance workers; nothing slow rides a request
  → review (Blueprint 24 families)     delivery, duration, audio, frames, identity, speech, line
  → assemble (Blueprint 28, 30, 31)    cut, loudness lock, room-tone floor, narration, 1080p finish
  → deliverables + receipt             film, srt, vtt, edl, assembly map, QC packet
```

Rules the Director keeps, learned on real productions:

- Anything slower than a request (a brain call, a provider submit, a wave) runs in a
  worker thread and is polled. The name router forwards with a 300 s timeout; nothing
  may depend on that.
- Every per-op error is named on the op (`failure`, `held`, `stalled`); nothing is
  swallowed. A refused clip redraws its opening frame on Retake; Skip shot leaves it
  out; Retry all re-queues every failed clip.
- A room holds one film. A new script retires the earlier chain.
- Never a native `confirm()` or `prompt()` in the UI; the two-step armed confirm is the
  only confirm.
- The UI stamp (`app.js?v=`) is functional: the Desk uses it to detect a stale build.

## 5. Engine selection

`ENGINE_CHOICES = ("grok", "google-flow")`, persisted at `{vault}/.cluster/engine.json`
by Studio's opening page. The Director reads the choice at every draft, lock, and
message (`session.engine_policy()`); with no choice on disk it refuses with a 409
that names Studio, and nothing defaults to a camera. `DIONY_ENGINE=local_legacy`
survives only as the switch that makes the Director refuse every paid run.

The local GPU lane that preceded the pivot was removed from the tree on 2026-09-10.

## 6. Pass criteria

- `diony-director-check --live` reports both providers with `ok: true` on a configured
  desk; `--roll-one grok --yes` lands a real clip in `{vault}/director/live-check/`.
- A lock reply's total equals clips + bible + allowance exactly, and the receipt's
  estimate equals the actual on an unremarkable run (zero variance was measured on
  2026-08-31).
- No Flow request overlaps another; no provider URL is ever persisted.
- The whole test suite runs offline (the root `conftest.py` blocks every non-loopback
  socket).

## Failure modes

| Symptom | Cause | Fix |
|---|---|---|
| Run & Assemble All returns 502 | a slow call rode a request through the router | move it to a worker; poll |
| "All operations failed" from Flow | nested `failureReasons`, usually the prominent-person filter | the likeness guard (Blueprint 27 §3a); Retake redraws the frame |
| Stills "generating" forever | an estimate could not reconcile (rate card unit missing) | the advance loop reports `stalled` with the reason; fix the card |
| Every clip looks different | no Look Bible or style frame on the prompts | Blueprint 29 |
| The film ends mid-word | narration timed off the picture length | Blueprint 28's tail hold |
| Flow 429 `THROTTLED` after a wave | requests landed in a burst | the pacing gate; never bypass it |
