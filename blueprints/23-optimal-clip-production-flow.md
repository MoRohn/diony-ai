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

The only successful clip contract is:

```text
authored Idea
  -> named cast + structured DialogueTurns
  -> Bible, reference, wardrobe, and immutable VoiceProfile locks
  -> Shot camera grammar + typed Wan prompt plan
  -> recorded/synthesized dialogue lock when speech is authored
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
synthetic `VoiceProfile` unless an approved 5–30 second reference creates a new
zero/few-shot revision. A still-based spoken Shot remains I2V while planned.
Voice renders and clears audio QC first; only then does the same Shot and Take
promote to S2V with the clean locked dialogue asset. Picture never precedes the
performance it must lip-sync.

Routing is explicit:

| Authored material | Camera route | Required lock |
|---|---|---|
| dialogue + character still | I2V plan -> S2V dispatch | VoiceProfile + clean dialogue + still/prior frame |
| character/product still | I2V A14B | exact still or selected prior boundary |
| driving performance video | Animate A14B | still + prepared motion root |
| picture-only description | T2V A14B | Bible and typed prompt plan |
| narration, score, or SFX without visible speech | picture route + post stem | timed audio asset |

## 2. Deterministic preflight

Before consuming GPU time, Producer verifies the signed Site, vault and overlay
safety floors, checkpoint manifests, prompt contract, negative conditioning,
reference files and hashes, previous-frame boundary, required voice backend,
audio QC, output ownership, and available Finish path. A missing dependency is a
durable hold, not a failed creative Take and not a reason to improvise another
engine.

The queue remains sequential on one B200. It may reuse a hot checkpoint or
resident pipeline, but it may not reorder previous-frame Shots or run concurrent
A14B Takes. Task-aware prefetch is allowed only when it cannot consume the active
Take's storage or device budget.

## 3. Receipt-gated lane selector

For every Take, the runner records the lane that actually executed rather than
the lane configuration hoped to execute:

1. A Project-promoted B200 cuDNN SDPA receipt selects the exact-attention kernel.
2. A matching resident BF16 or regional-compile receipt plus deterministic
   canary selection may retain the same task/checkpoint pipeline on device.
3. Any missing, held, incompatible, or runtime-suppressed accelerator selects
   pinned official `generate.py` with the reason recorded as the fallback.

The fallback is intentionally the same 24-step BF16 quality lane. It is safe but
not silent. `{negative}.generation.json` records backend, selected lane, task,
native raster, frames, steps, solver, precision, negative conditioning,
attention backend, accelerator receipt, fallback reason, elapsed time, byte
count, and output SHA-256. `Take.generation_receipt` persists the validated
receipt. Producer rejects an unaddressed output, a hash mismatch, or a claimed
quality match whose fields are not exactly 1280x720, 81 frames, 24 UniPC steps,
BF16, and negative-conditioned.

Production exposes the actual lane on each clip row. “Quality CLI fallback” must
include the held/missing/rollback reason. An optimization can therefore never
appear to vanish without evidence.

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
Failure holds the remaining queue.

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
