# Blueprint 22 — Voice identity and performance system

Status: implemented contract and Producer orchestration; every real backend pack still requires signed-site qualification evidence.

Voice is a production layer, not a final-mix convenience. Any Shot with
`audio_line` or `dialogue_turns` follows this order:

```text
Bible Character
  → immutable VoiceProfile
  → profile-aware PerformanceScript
  → bounded local synthesis + speaker-identity QC
  → deterministic studio-audio chain + audio QC
  → clean dialogue + final clip mix + performance metadata lock
  → Wan2.2 S2V from the locked audio
  → lip/face/body audiovisual QC
  → 720 negative AutoQC
  → 1080 Finish
  → timeline assembly of verified per-clip voice mixes
  → MixLock
```

Picture may never be generated first and given required dialogue afterward.
Program Mix may assemble verified clip packages, but it may not synthesize raw
TTS. A visual-only Shot remains visual-only; room tone is allowed only when the
Project contains no spoken intent.

## Contracts

`packages/contracts/src/diony_contracts/voice.py` owns the machine API.

| Object | Required content | Invariant |
|---|---|---|
| `VoiceReferenceSample` | audio Asset, duration, emotion, transcript, embedding/model, checksum | zero/few-shot references total 5–30 seconds |
| `VoiceProfile` | Project/Character ids, revision, source, reference embeddings, speaker embedding, prosody, emotion map, descriptors, seed, style lock | content-addressed, frozen, and immutable |
| `DialogueTurn` | Character, spoken text, emotion curve, subtext, start/overlap/interruption, pronunciation | explicit multi-character timing intent |
| `PerformanceScript` | profile-aware lines, breaths, stress, pauses, timing, phonetics, emotion curve | deterministic content hash |
| `VoiceQCReceipt` | identity, consistency, SNR, LUFS, true peak, harshness, sibilance, lip confidence, AV offset | cannot claim pass below a locked threshold |
| `VoiceClipPackage` | profile ids, generation seeds, script, three Asset ids, backend versions, cache key, QC | `verified` requires complete audio and animation evidence |

`Character.voice_profile_id` names the current locked Project identity.
`Project.voice_profiles[]` retains every immutable revision. `Take.voice_package`
binds voice and performance evidence to the generated attempt. Spoken camera QC
also copies the decisive metrics to `QCReport` so picture lock cannot ignore a
failed or missing package.

JSON Schemas are exported for `voice_profile`, `performance_script`,
`voice_clip_package`, and `voice_qc_receipt`.

## Persistent registry

`VoiceProfileRegistry` is Project-scoped.

- `ensure_synthetic(character_id)` locks a deterministic seed, style token,
  synthetic speaker-space vector, prosody, and neutral/joy/grief/tension maps.
- `lock_reference_voice_profile(...)` accepts local audio Assets only, extracts
  a speaker embedding per sample through the configured local backend, records
  per-sample emotion/transcript/checksum, averages and normalizes the locked
  speaker vector, and rejects a total reference duration outside 5–30 seconds.
- A locked profile object is frozen. Changing voice identity requires a new
  versioned Bible/Project decision; regenerating a clip reuses the exact locked
  profile and content hash.
- `GET /api/voice-profiles` reads the registry.
- `POST /api/characters/{character_id}/voice-profiles/lock` locks a synthetic,
  zero-shot, or few-shot identity. It never uploads data or calls a hosted API.

Voice reference files, embeddings, performance metadata, and packages remain
under the Project vault. Portable export follows existing Project lineage and
must never include provider credentials.

## Voice Script Optimizer

`optimize_voice_script(project, shot, profiles)` sits between story breakdown
and synthesis. It uses Shot duration, Character identity, locked prosody,
emotion maps, authored subtext, pronunciation, turn starts, overlaps, and
interruptions. It outputs:

- target duration and start time per line;
- breath boundaries and micro-pause durations;
- word-stress indices;
- pronunciation guidance;
- continuous emotional intensity curves;
- subtext and interruption lineage.

The deterministic compiler is the contract floor. A local DeepSeek optimizer
may enrich word choice and performance direction, but it must return the same
typed object and may not change Character/Profile ids, authored text meaning,
Shot duration, or locked timing without a new script version.

## Swappable local backend

Production uses `DIONY_VOICE_BACKEND_COMMAND`. The executable receives:

```text
<command> <action> --request <json> --response <path>
```

Required actions:

| Action | Input | Output | Failure |
|---|---|---|---|
| `synthesize` | line, full locked profile, seed, local reference paths | high-fidelity WAV | empty/invalid audio blocks camera |
| `embed` | generated line WAV + locked embedding model | speaker vector JSON | dimensional mismatch blocks camera |
| `embed-reference` | clean local reference + embedding model | speaker vector JSON | no Profile lock |
| `align` | processed line + performance line | phoneme/viseme/co-articulation and face/body timing JSON | no S2V dispatch |
| `studio-audio` | line WAVs/timing, duration, LUFS/peak, deterministic seed | clean dialogue WAV, final clip mix WAV, audio metrics JSON | bounded retry, then QC hold |
| `audiovisual-qc` | Wan negative, clean dialogue, performance metadata | lip confidence, p95 offset, performance consistency | immutable visual retake |

The command is an adapter boundary, not permission to use a hosted service.
Backends remain local and replaceable. CosyVoice, a licensed voice engine,
ECAPA-TDNN/CAM++-class embedding, forced alignment, and SyncNet-class AV checks
may change independently behind these actions. Model/version names are written
into each package.

`DeterministicVoiceTestBackend` exists only under `DIONY_DRY_RUN=1`. Its tones
and fixed metrics are test evidence, never a deliverable or signed-site proof.
Production fails closed when the local command is absent.

## Studio audio

The backend receives this mandatory ordered processing contract:

1. noise gate;
2. de-essing;
3. breath control;
4. mouth-noise reduction;
5. Character/profile EQ;
6. gentle compression;
7. bounded harmonic enhancement;
8. deterministic room/scene matching;
9. loudness normalization;
10. dialogue-priority score/SFX ducking.

It writes 48 kHz production WAV assets for clean dialogue and the final clip
mix. Broadcast follows the Bible −24 LUFS target; streaming follows −16 LUFS;
true peak may not exceed the Bible ceiling (normally −1 dBTP). Raw synthesis is
never registered as a mix stem.

## Audio-driven picture

After audio lock, Producer changes the spoken Shot and Take to `s2v-14B`, sets
`audio_strategy=locked_voice_s2v`, and gives Wan the processed clean dialogue
Asset. A previous-frame Shot may use the prior selected print as its S2V image
anchor. A spoken Shot with neither a still nor a prior frame fails preflight;
Producer never substitutes unconditioned T2V.

Performance metadata contains phoneme/viseme timings, jaw openness, brow and
head-energy drives, emotion curves, subtext, Profile hashes, and seeds. S2V audio
is the hard picture condition. Backend-specific face/body controls may consume
the same metadata without changing the package contract.

## QC and automatic recovery

Default hard floors:

| Metric | Gate |
|---|---|
| speaker cosine similarity | ≥ 0.92 against locked Profile |
| prior Project consistency | ≥ 0.90 |
| dialogue SNR | ≥ 30 dB |
| integrated loudness | target ±1 LU |
| true peak | at or below Bible ceiling |
| harshness | ≤ 0.25 normalized score |
| sibilance | ≤ 0.25 normalized score |
| lip-sync confidence | ≥ 0.90 |
| p95 audiovisual offset | ≤ 40 ms |
| physical-performance consistency | ≥ 0.90 |

Identity synthesis and studio-audio processing retry automatically up to
`DIONY_VOICE_RETAKE_CAP` (default 3, maximum 5). Each attempt records its seed,
metrics, and diagnostic. A visual performance failure uses the normal immutable
Take retake path and counts against the four camera Take cap. Infrastructure and
missing-backend failures do not invent output.

The cache key covers pipeline version, PerformanceScript hash, and every
VoiceProfile hash. A visual retake reuses clean/final audio and performance
metadata, resets only animation QC, and must verify the new negative. Completed
audio is never resynthesized merely because picture changed.

## Clip and program outputs

Each spoken Take owns:

- `voice_clean_dialogue` Asset;
- `voice_final_mix` Asset;
- `voice_performance_metadata` Asset;
- Voice Profile ids and content hashes;
- generation seeds and attempt diagnostics;
- script hash and timing/viseme/face/body signals;
- audio and audiovisual consistency metrics.

Program Mix resolves verified packages in `TimelineEvent.start_frame` order,
delays them on the frame-authoritative rail, mixes, pads to picture duration,
normalizes, and muxes PCM audio without re-encoding picture. Missing or
unverified required dialogue blocks Mix. Final Mix never invokes synthesis.

## Qualification boundary

Passing local tests proves contracts, determinism, cache behavior, ordering,
and failure gates. It does not prove the trained-listener or commercial ADR bar.
A backend/version is eligible for a signed site only after real samples prove:

- blind listener acceptance for identity and emotional performance;
- same-Character cross-clip and cross-episode embedding stability;
- measured lip/face/body timing across complete clips;
- the full audio metric table;
- deterministic rerun hashes for profile, script, seed, and backend version;
- no hosted network calls.

Until that evidence exists, Producer correctly reports a voice readiness or QC
hold rather than claiming studio-grade output.
