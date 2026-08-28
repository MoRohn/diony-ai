# Blueprint 23 — Optimal 1080p Clip Production Flow

Status: implemented contract; live B200 activation remains receipt and canary gated

Owner: Diony AI

Applies to: Studio Idea compiler, Voice, Producer FARM, Wan2.2, AutoQC, Finish, Mix, Gallery

Depends on: Blueprints 10, 19, 20, and 21

## Product decision

“Optimal” means the shortest measured path that still clears the complete Diony
quality contract. A lane that changes weights, steps, precision, references,
conditioning, native raster, or Finish is a different creative model and cannot
silently replace Production. GPU utilization, a vendor speed claim, or one good
preview is not production qualification.

The B200 operational objective is strictly less than 600 seconds of end-to-end
Producer time for every five-second Shot, including voice preparation when
required, Wan camera, AutoQC, and the per-Take 1080 Finish. Cold activation and
steady-state rows are recorded separately, but neither is excluded from the
objective. A source-only optimization or camera-only timing is not proof.

The only successful clip contract is:

```text
authored Idea
  -> named cast + structured DialogueTurns
  -> Bible, reference, wardrobe, and immutable VoiceCasting locks
  -> Shot camera grammar + typed Wan prompt plan
  -> Wan speech request lock when speech is authored (recorded audio stays --audio)
  -> one receipt-approved Wan lane at 1280x720
  -> addressed negative + generation execution receipt
  -> measured reference / identity / temporal / voice AutoQC
  -> diony_sr_v1 temporal Finish
  -> verified 1920x1080 ProRes 422 HQ 10-bit Rec.709 limited master
  -> picture assembly -> measured mix -> Gallery
```

No stage may infer success from the existence of a file alone.

## 1. Idea and performance compilation

Explicit screenplay lines such as `MARCUS: "We move now."` compile into named
Bible Characters and ordered `DialogueTurn` objects. Diony does not replace an
authored ensemble with a generic “lead,” and it does not leave quoted dialogue
inside a picture prompt as if the camera model were the voice engine.

Every speaking Character receives one deterministic, project-scoped, locked
`VoiceCasting` (a slot in the site's casting library by roster order) unless an
attached 5–15 second reference clip casts it directly. A still-based spoken
Shot remains I2V while planned; the speech lane locks the `WanSpeechRequest`
before camera, and the same Shot and Take promote to S2V with Wan's own TTS
inputs. Wan performs the line and the picture together; the speech it delivers
is measured on the negative.

Routing is explicit:

| Authored material | Camera route | Required lock |
|---|---|---|
| dialogue + character still | I2V plan -> S2V dispatch (native TTS) | VoiceCasting + WanSpeechRequest + still/prior frame |
| character/product still | I2V A14B | exact still or selected prior boundary |
| driving performance video | Animate A14B | still + prepared motion root |
| picture-only description | T2V A14B | Bible and typed prompt plan |
| narration, score, or SFX without visible speech | picture route + post stem | timed audio asset |

## 2. Deterministic preflight

Before consuming GPU time, Producer verifies the signed Site, vault and overlay
safety floors, checkpoint manifests, prompt contract, negative conditioning,
reference files and hashes, previous-frame boundary, required voice backend,
natural-rate dialogue allocation, measured synthesized-line duration, audio QC,
output ownership, and available Finish path. A missing dependency is a
durable hold, not a failed creative Take and not a reason to improvise another
engine.

The queue remains sequential on one B200. It may reuse a hot checkpoint or
resident pipeline, but it may not reorder previous-frame Shots or run concurrent
A14B Takes. Task-aware prefetch is allowed only when it cannot consume the active
Take's storage or device budget. Its time budget too: the hot-tier lock
guards only verify / reserve / land, never a copy, so a first-use family
staging in the background (I2V-A14B, 126 GB, minutes) can never hold the
active Take's own checkpoint check behind it; a same-family caller joins
the in-flight copy instead of copying twice.

## 3. Receipt-gated lane selector

For every Take, the runner records the lane that actually executed rather than
the lane configuration hoped to execute:

1. A Project-promoted B200 cuDNN SDPA receipt selects the exact-attention kernel.
2. A matching resident BF16 or regional-compile receipt plus deterministic
   canary selection may preload before the first Shot timer and retain the same
   task/checkpoint pipeline on device. Cold preload remains separately measured
   and still counts toward the end-to-end cold objective.
3. Any missing, held, incompatible, or runtime-suppressed accelerator selects
   pinned official `generate.py` with the reason recorded as the fallback.

The fallback is intentionally the same 24-step BF16 quality lane. It is safe but
not silent. `{negative}.generation.json` records backend, selected lane, task,
native raster, output frames, inference frames per pass, denoise-pass count,
effective denoise steps, solver, precision, negative conditioning,
attention backend, accelerator receipt, fallback reason, elapsed time, byte
count, and output SHA-256. `Take.generation_receipt` persists the validated
receipt. Producer rejects an unaddressed output, a hash mismatch, or a claimed
quality match whose fields are not exactly 1280x720, the booked lane frames, the lane's UniPC steps (24; S2V 40),
BF16, and negative-conditioned.

Production exposes the actual lane on each clip row. “Quality CLI fallback” must
include the held/missing/rollback reason. An optimization can therefore never
appear to vanish without evidence.

A spoken Shot is a declared cut to its speaker: the Studio contract books it
at medium close-up focal length with REFERENCE continuity and overlap 0 (the
following Shot cuts back the same way), starting from the subject still. This
framing is a property of speech, not of the reference lock: a lock-NONE brief
books its dialogue the same way (`frame_spoken_shot` at compile and on
refresh), so the camera layer never says "wide shot, 24mm" over a close-up
direction. S2V animates one prominent face; the prompt plan names the speaker
and the performance so the model animates the right one.

**Wan speaks the line.** The spoken lane is Wan2.2 S2V's own speech lane
(`--enable_tts --tts_prompt_audio --tts_prompt_text --tts_text --num_clip 1`,
Blueprint 22): Producer locks a `WanSpeechRequest` (the authored line, the
character's casting resolved to a prompt clip on the host, the window, the
performance direction) before the camera rolls, Wan clones the clip, performs
the line, animates the face from that speech, and muxes it into the negative.
No render headroom is booked and no head trim or hold is applied afterwards —
the take is judged as Wan delivered it. Three rules keep a spoken take honest:

* A window of dialogue never spans a speaker change when the rundown can hold
  it (`_fit_dialogue_to_shots`), and windows are balanced so no two-word
  orphan turn is left at a boundary (`_balanced_windows`). S2V animates the
  visible face with every word it hears, so a two-speaker window makes one
  character mouth the other's line; Producer re-checks and holds
  (`Voice casting failed`) before camera. A line past three words per second
  cannot finish inside its window and holds (`Voice timing failed`). Neither
  hold spends a Take.
* A spoken Shot whose speaker differs from the prior Shot's is a cut, not a
  continuation (`_speaker_changes`): it restarts from the reference still.
* After the render the speech Wan delivered is measured on the negative
  (`verify_speech_render`): an audio stream must be present, voiced-speech
  presence ≥ 0.20, the last speech-active moment at least one S2V frame
  (62.5 ms) before the window ends, and the conformed mix stem within ±1 LU of
  the Bible target under its true-peak ceiling. A miss is `Voice audio failed:
  …` naming the gate — a bounded retake (a new seed changes Wan's pacing), never
  a silent print.

**Retake cap → best attempt by policy.** When every one of a Shot's four
bounded attempts fails measured QC, Producer no longer holds the Shot for
review (which froze the clock and stalled the rundown). `settle_exhausted_shot`
scores each landed attempt (`attempt_score`: voiced-speech presence 0.30,
speech tail 0.15 — a cut line scores zero —, reference identity 0.35,
temporal residual 0.20; missing measurements drop out of the mean), selects
the highest-scoring attempt as the print, records its verdict as WARN with
`settled_best_of`/`settled_score` and the measured notes kept, and announces
the ranking on the activity feed. Finish, picture lock, mix (a settled print
whose speech measured present still feeds its stem), and the previous-frame
chain accept a settled print explicitly; a plain rejection is still refused
everywhere. `resume_camera` settles already-exhausted Shots on restart.

The spoken lane also follows its own upstream recipe: S2V-14B denoises in
**40** UniPC steps (shift 3) where T2V/I2V A14B use 24 (`lane_sample_steps`).
Under-stepping S2V left its lip motion loosely coupled to the line; the
receipt contract is task-aware. Three more spoken-lane facts are load-bearing:

* **Guidance drives the mouth.** S2V's classifier-free guidance zeroes the
  audio in its unconditional branch, so the guide scale is how hard the line
  drives the lips. Diony dispatches `--sample_guide_scale 5.5`
  (`lane_guide_scale`; upstream default 4.5 measured two mouth openings across
  three phrases) and the receipt records it.
* **No `--start_from_ref`.** That flag builds the first clip's motion context
  from 67 black frames plus six copies of the frozen reference still and asks
  the model to continue from a freeze; measured mouths started up to eight
  frames late and moved sparsely. The upstream first-clip path
  (`drop_first_motion`) starts clean from the reference latent and delivers
  exactly `infer_frames − 3 = frame_num`.
* **The prompt directs a performance, never the words.** The five-layer
  template writes every Shot as a blocking beat and pads its motion with a
  word-limited fragment of the idea; for a spoken Shot that fragment was
  another character's beat cut mid-sentence, under a "slow steady push-in".
  `spoken_wan_prompt_plan` rewrites the motion, camera, and environment layers
  for the S2V lane — the named speaker speaks the line straight to camera with
  the authored emotion in the voice and face, lips, jaw, and cheeks on every
  syllable, mouth still through each pause and after the last word, no one
  else speaking, locked-off medium close-up — keeping the subject and lighting
  layers and adding `subtitles` / `on-screen text` to the negatives. The line
  itself rides `--tts_text` only; quoted in the picture prompt it rendered
  captions and a second reading. `refresh_reference_shot` upgrades persisted
  S2V plans on the next dispatch.

A spoken negative is booked at S2V's native 16 fps: `s2v_frame_num` takes the
booked seconds (81 frames at 23.976 = 3.378 s), adds one conform frame, and
rounds up to Wan's 4n+1 — 57 frames — so S2V runs with `--frame_num 57
--infer_frames 60`: the smallest VAE-aligned window that
covers the booking, one audio bucket, one 24-step denoise pass, and no third
of the render spent on padded silence. The 1080 finish conforms the 16 fps
negative to the 23.976 timeline by frame duplication and trims to the booked
frames; the farm judges duration in the booking's frame rate. The prior
80-frame value crossed the upstream audio-bucket boundary and executed two
24-step passes. Upstream then stream-copied H.264 with the five-second audio as
`-shortest`; reordered video packets silently reduced the published file to 77
frames. Diony's exact S2V publish overlay pads the final audio tail and caps the
encoded stream at 81 frames. Its Production raster overlay also bypasses the
aspect-preserving 64-pixel helper that silently rounded height to 704. A measured
line that cannot fit one window is blocked before Wan rather than truncated or
paid for as a hidden second pass.

The execution receipt probes the completed file with FFprobe and records actual
decoded width, height, and frame count. Requested arguments can never make
`quality_contract_matched=true` when the media bytes are 1280x704, 77 frames,
or otherwise outside the pinned native contract.

## 4. B200 activation sequence

Activation occurs only at a drained immutable-Take boundary:

```bash
python scripts/benchmark/measure_wan_attention.py \
  --project-id "$PROJECT_ID" \
  --approved-by "Named reviewer" \
  --output /diony/vault/qualification/wan-cudnn-sdpa-validation.json
```

The command measures forced Flash SDPA and cuDNN SDPA on the target B200 at the
exact BF16 `[1,40,75600,128]` shape and writes only
`operator_passed_quality_canary_required`. It cannot activate Production.

Producer then binds exactly one selected immutable Take. That Take must run the
same prompts, seed, references, weights, 24 steps, BF16 precision, and negative
conditioning; clear measured reference and temporal AutoQC; and land a verified
1080 Finish. Only that evidence promotes cuDNN for the rest of that Project.
An eligible creative/voice QC failure creates the normal bounded immutable
retake, rebinds the still-unpromoted operator canary to that retry, and preserves
the waiting rundown. Receipt/runtime failure or exhaustion of the Take cap holds
the remaining queue.

Resident eager and regional compilation remain later general candidates because
they require 30 paired clips under Blueprint 21. They cannot borrow the cuDNN
receipt or bypass their own p50, p95, OOM, retry, decoded-frame, AutoQC, master,
and blind-review gates.

## 5. Advanced Blackwell model lane

Distilled four-step, sparse, FP8, and NVFP4 models are promising throughput
candidates but are not “same quality” accelerators. They require a separately
versioned model/weights/license manifest and may run only in isolated candidate
output paths. Promotion requires paired prompt/seed/reference sets across
dialogue, faces, hands, products, motion, low light, and continuity; current
AutoQC; verified Diony 1080 masters; no worse retake/OOM/retry rate; and blinded
human preference that meets the release threshold. Until then they are labeled
candidate/previz and never a Production fallback.

This boundary keeps Diony ready to adopt a materially faster Blackwell-native
model without allowing a vendor benchmark or low-step preview to weaken a paid
master.

## 6. Success and operational handoff

A clip is successful only when all of these are true:

- the accepted 720 negative hash matches its generation execution receipt;
- the receipt names the actual lane and a matched Production quality contract;
- required reference, identity, motion, flicker, morphology, duration, camera,
  lighting, voice, lip-sync, and loudness evidence pass;
- Finish is 1920x1080, ProRes 422 HQ, `yuv422p10le`, Rec.709 tagged and decoded
  legal, at 23.976 or 24.000 fps;
- the 1080 artifact retains lineage to the addressed negative and prompt plan;
- failures create bounded retakes or infrastructure holds instead of silently
  consuming the queue.

Code deployment, GPU service restart, operator measurement, canary generation,
and queue resume are separate actions. A user STOP/Keep or durable pause is
preserved. Diony may stage and verify code while idle, but it never resumes the
remaining Takes without the owning production action.
