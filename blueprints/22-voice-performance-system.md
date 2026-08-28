# Blueprint 22 — Voice performance on the Wan2.2-native spoken lane

Status: implemented contract and Producer orchestration; the Wan2.2 speech pack
still requires signed-site qualification evidence.

Voice is a production layer, not a final-mix convenience — and Diony no
longer synthesizes it. Wan2.2 S2V ships its own speech lane
(`generate.py --enable_tts --tts_prompt_audio --tts_prompt_text --tts_text`,
`WanS2V.tts`): given a prompt clip to clone and the text to say, Wan performs
the line, animates the visible face from the speech it hears, and muxes that
speech into the negative. Any Shot with `audio_line` or `dialogue_turns`
follows this order:

```text
Bible Character
  → immutable VoiceCasting (which prompt clip Wan clones)
  → WanSpeechRequest locked before camera (line, casting, window, direction)
  → Wan2.2 S2V renders picture and speech in one pass (native TTS)
  → speech QC on the audio Wan delivered
  → 720 negative AutoQC
  → 1080 Finish
  → timeline assembly of verified per-clip dialogue stems
  → MixLock
```

Picture may never be generated first and given required dialogue afterward.
Program Mix assembles verified clip packages; it never synthesizes. A
visual-only Shot remains visual-only. Generated room tone, noise, or a test
tone is never accepted as a content-bearing soundtrack; a Project with no
verified dialogue, score, SFX, or recorded audio delivers a mute picture-only
master with the policy on its receipt.

## What was removed, and why

The earlier lane ran CosyVoice2 in a Diony-owned venv, measured speaker
identity with CAM++, calibrated per-voice floors, processed a studio-audio
chain, conditioned S2V on that locked WAV, then measured the mouth with YuNet
and MediaPipe (casting panel, face crops, head trim/hold alignment, still-mouth
guidance raises). That was a second speech system wrapped around Wan's. The
Wan-native lane keeps one authority for the spoken performance — Wan2.2 S2V —
and Producer measures only what Wan delivered. Wan's own TTS is CosyVoice2 under
the hood (upstream `load_tts`), so the CosyVoice checkout and CosyVoice2-0.5B
weights remain a *Wan* dependency that Studio pins beside `generate.py`; Diony
runs no CosyVoice process, embedding, or mouth model of its own.

## Contracts

`packages/contracts/src/diony_contracts/voice.py` owns the machine API.

| Object | Required content | Invariant |
|---|---|---|
| `DialogueTurn` | Character, spoken text, emotion, timing intent | authored words, never paraphrased |
| `VoiceCasting` | Project/Character ids, source (`library` slot by roster order, or `reference` Asset + transcript), descriptors | content-addressed, frozen; reference clips run 5–15 s at ≥ 16 kHz (upstream `generate.py`) |
| `WanSpeechRequest` | character, casting, `tts_text`, resolved prompt clip sha256 and transcript, prompt source, word count, window, performance direction | deterministic content hash; the words never enter the picture prompt |
| `SpeechQCReceipt` | audio present, duration, voiced-speech presence, speech tail before the window ends, integrated LUFS, true peak | cannot claim pass below any gate |
| `SpeechClipPackage` | request, dialogue Asset (extracted from the negative), mix-stem Asset, exact video SHA-256, backend, QC | `verified` requires a passing receipt, both assets, and the rendered negative's hash |

`Character.voice_casting_id` names the current locked Project casting.
`Project.voice_castings[]` retains every revision. `Take.voice_package` binds
the speech evidence to the generated attempt. AutoQC copies the decisive
metrics to `QCReport` (`speech_backend`, `speech_presence_score`,
`speech_tail_ms`, `dialogue_lufs`, `dialogue_true_peak_dbtp`) so picture lock
cannot ignore a failed or missing package. `lipsync_ok` is never claimed on
this lane: lip motion is Wan S2V's own audio conditioning, not a separate
measurement.

JSON Schemas are exported for `voice_casting`, `wan_speech_request`,
`speech_clip_package`, and `speech_qc_receipt`.

## Casting

Casting is per character, never one shared voice.

- Studio compiles a `library` casting for every character when the Idea
  carries dialogue: slot = Bible roster order. `VoiceCastingRegistry.ensure_library`
  is idempotent and locks the same decision on the GPU Producer.
- `POST /api/characters/{character_id}/voice-casting` with
  `source=reference` casts a character on a local audio Asset attached to the
  Project plus its exact transcript. Producer measures the clip from its bytes
  (duration 5–15 s, sample rate ≥ 16 kHz). A reference casting is a Bible-lock
  decision: it replaces a library casting and is itself immutable.
- `GET /api/voice-castings` reads the registry.
- At dispatch the casting resolves to a clip on the host: a reference Asset;
  else the site library `DIONY_WAN_TTS_PROMPT_LIBRARY` (clips in name order,
  slot = cast index modulo library size, sibling `.txt` = exact transcript so
  Wan clones zero-shot; without one Wan clones cross-lingually from the audio);
  else the single default `DIONY_WAN_TTS_PROMPT_AUDIO` / `_TEXT`. The resolved
  path's sha256, transcript, and source are written into the request.
- Studio setup seeds the library at `/diony/vault/runtime/voice/prompt-library`
  from open-licensed clips already on the host (`cast-00` CosyVoice
  cross-lingual prompt, `cast-01` Wan's `zero_shot_prompt.wav` with its
  transcript, `cast-02` Wan's `talk.wav`). Library order is cast order, so
  renaming files recasts.

Nothing here uploads data or calls a hosted API.

## The line and the window

Studio allocates authored dialogue at no more than two words per second of
Shot time and never splits a window across two speakers (Wan S2V animates the
visible face with every word it hears). Producer re-checks before camera: a
window with several speakers is a `Voice casting failed` hold, and a line past
three words per second is a `Voice timing failed` hold. Both are pre-camera
holds that spend no Take.

`prepare_speech_for_take` locks the `WanSpeechRequest`, routes the Shot and
Take to `s2v-14B`, clears any stale `audio_asset_ids` (the authored line *is*
the performance), stamps `audio_strategy = wan_native_tts_s2v`, and directs
the prompt plan (below). Recorded on-camera audio attached to a Shot without an
authored line still conditions S2V through `--audio`; the TTS lane is only for
authored lines.

## Prompt direction for the Wan-native lane

Wan S2V reads lips, jaw, and cheeks from the audio and everything else from the
prompt. `spoken_wan_prompt_plan` therefore replaces the five-layer blocking
beat with a performance direction and keeps subject anchors and lighting:

- Motion: *`{speaker}` speaks the line straight toward camera with
  `{emotion}` in the voice and face* (or *at a steady conversational pace*),
  *lips, jaw, and cheeks shaping every syllable in time with the voice, mouth
  closed and still through each pause and after the last word, small natural
  head gestures and blinks while the shoulders settle, no one else speaking*.
  The emotion comes from the authored `DialogueTurn.emotion`.
- Camera: the booked medium close-up, static, eyes on the lens.
- Scene: the lighting statement and the Rec.709 colour contract only.
- Negative terms add `frozen lips`, `late mouth motion`, `doubled mouth`,
  `mumbling`, `closed mouth while speaking`, `mouth moving in silence`,
  `another character's mouth moving`, `subtitles`, `on-screen text`.
- The words are never written into the picture prompt — they ride
  `--tts_text`. Quoting them made Wan render a second reading and captions.

## Camera dispatch

`spark_argv` adds, for an S2V Shot without recorded audio:

```text
--enable_tts --tts_prompt_audio <clip> [--tts_prompt_text <transcript>] --tts_text <line> --num_clip 1 --infer_frames <n>
```

`--num_clip 1` renders exactly one window: the picture is the booking
(`s2v_frame_num`, native 16 fps), and Wan's speech is padded or cut to it by
the exact-publish overlay. No alignment headroom is rendered and no head trim
or hold is applied afterwards; the take is judged as Wan delivered it. The
resident worker carries the same inputs (`enable_tts`, `tts_*`,
`num_repeat=1`) and muxes Wan's `tts.wav` from its working directory.

Before a TTS render `ensure_s2v_speech_pack` refuses to start unless
`/opt/wan22/CosyVoice` and `/opt/wan22/CosyVoice2-0.5B` are complete: upstream
`load_tts` would otherwise clone and download at render time, and Diony never
fetches during Production.

## Speech QC

After the render `verify_speech_render` extracts the negative's audio track as
48 kHz WAV (untouched), measures it, writes a loudness-conformed stereo stem for
the program mix (70 Hz high-pass, deterministic two-pass `loudnorm` to the
Bible target, padded or cut to the picture), and binds everything to the
negative's SHA-256.

| Metric | Gate |
|---|---|
| audio present | the negative carries an audio stream |
| voiced-speech presence | ≥ 0.20 of analysis frames energetic and periodic (60–400 Hz) |
| speech tail | last speech-active moment ≥ 62.5 ms (one S2V frame) before the window ends — otherwise the line was cut by the picture |
| integrated loudness | Bible target ±1 LU on the mix stem |
| true peak | at or below the Bible ceiling on the mix stem |

A failed receipt is a `Voice audio failed: …` verdict that names the gate. It
is an auto-retake under the normal four-Take cap (a new seed changes Wan's
pacing and delivery); at the cap the best-scoring attempt proceeds by policy
(`attempt_score`: speech presence 0.30, speech tail 0.15, reference 0.35,
temporal 0.20). A print settled by policy whose speech measured present may
still feed Mix. Infrastructure and missing-pack failures do not invent output.

## Clip and program outputs

Each spoken Take owns a `voice_dialogue` Asset (Wan's speech as delivered), a
`voice_mix_stem` Asset, the locked request, and the receipt. Program Mix
resolves verified packages in `TimelineEvent.start_frame` order, delays them
on the frame-authoritative rail, mixes, pads to picture duration, normalizes,
and muxes PCM audio without re-encoding picture. Missing or unverified required
dialogue blocks Mix. Final Mix never invokes synthesis.

## Site preparation

Studio App's **Wan2.2 speech pack** stage (`SPEECH_PACK_*`) pins the
CosyVoice checkout at `/diony/vault/runtime/wan-tts/CosyVoice` and links it to
`/opt/wan22/CosyVoice`, installs `requirements_s2v.txt` plus
`infra/runtime/wan-tts-overlay.txt` into the Wan host interpreter (the one that
runs `generate.py`), verifies `from cosyvoice.cli.cosyvoice import CosyVoice2`
under that interpreter, pulls `FunAudioLLM/CosyVoice2-0.5B` to
`/diony/vault/models/audio/CosyVoice2-0.5B` and links it to
`/opt/wan22/CosyVoice2-0.5B`, and seeds the casting library. The Connect start
script exports `DIONY_WAN_TTS_PROMPT_LIBRARY` / `_AUDIO` / `_TEXT` only when
that pack is complete; a Producer launched earlier re-derives them from the
host layout (`host_speech_env`) when the first spoken Take needs them and
otherwise holds with the missing pieces named.

## Qualification boundary

Passing local tests proves contracts, determinism, dispatch arguments, and
failure gates. It does not prove the trained-listener bar. The Wan2.2 speech
lane is eligible for a signed site only after real samples prove blind listener
acceptance for identity and delivery, same-Character cross-clip stability from
one prompt clip, measured lip timing across complete clips, the loudness table,
deterministic rerun hashes for casting, request, seed, and Wan version, and no
hosted network calls. Until then Producer reports a voice readiness or QC hold
rather than claiming studio-grade output.

## Addendum 2026-08-28 — CosyVoice API pin and the prompt-clip overlay

Studio cloned CosyVoice from `main` on clone day. Wan2.2's `WanS2V.tts` hands
CosyVoice a 16 kHz *tensor*; CosyVoice revisions after 2025 take the prompt
clip as a *path* (`inference_cross_lingual(tts_text, prompt_wav)` →
`load_wav`), so every spoken Take failed with `TypeError: Invalid file:
tensor(...)` on the live B200. Two fixes, both required: the runner overlay
`ensure_s2v_tts_prompt_path` (marker `DIONY_S2V_TTS_PROMPT_PATH`, applied at
dispatch and by the GPU start script) inspects the installed signature and
passes the path or the tensor accordingly; and Studio pins the checkout to
`074ca6dc…` (`WAN_TTS_CHECKOUT_REV`, `infra/models/manifest.yaml`) so the
overlay is exercised against one known revision on every site.
