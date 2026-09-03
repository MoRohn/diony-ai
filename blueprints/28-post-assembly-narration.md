# Blueprint 28 — Post-Assembly Narration

Status: implemented on the Director (cloud) lane; the Producer/Wan lane keeps Blueprint 22 (S2V lip-sync physically requires the audio at render)

Owner: Diony AI

Applies to: the Director compiler (all three dialects), the master-prompt contract (single and CLIP DELIVERY split), `DirectorOp`, the assemble endpoint, deliverables, the narration module

Depends on: Blueprints 25 (Director pivot), 27 (Master Video Prompt)

## Product decision

Before this Blueprint every scripted or narrated line reached the finished film by
being *generated inside its clip*: the compiler quoted the line into the provider
prompt and the provider performed it. That produced the two defects this Blueprint
exists to kill:

- **No voice consistency.** Each clip invented its own narrator — timbre, accent,
  and pacing changed at every cut.
- **No run-length control.** The provider stretched or crammed the picture to fit
  its own delivery of the words, so booked lengths drifted and the splitter's
  word-count arithmetic was a hope, not a contract.

**The voice track is now a POST instrument.** Clips are generated with picture and
ambience only; `assembly.assemble()` cuts the film exactly as booked; then the
narration stage records the authored words — one synthesized cue per clip that
carries a line — against the *real* cut timeline and mixes them into the master.
One voice, exact words, measured placement.

## The law

1. **The words never ride a generator prompt.** The compiler replaces the quoted
   line with a delivery performance (an on-camera speaker visibly speaking,
   direction preserved) plus the `NO_SPEECH` clause: the location track carries no
   dialogue, no narration, no voice-over. The master template's OUTPUT CONTRACT and
   CLIP DELIVERY amendment return narration as *data* (`narration` per clip),
   never as spoken-voiceover prompt prose.
2. **Narration is structured all the way to the cut.** A scripted clip's line
   lives in `AudioPacket.script_excerpt` (unchanged); a chained one-shot's split
   narration lands on `DirectorOp.narration`; a single one-shot's on
   `MasterPrompt.narration`. Chains therefore now get subtitles and cues too.
3. **Placement is the assembled cut's arithmetic.** `narration.cue_sheet()` walks
   the same `(op, clip, window)` entries deliverables consume and places each cue
   at its clip's actual timeline start plus the cue lead-in — never at the
   provider's booked length.
4. **The mix is a real mix.** Each cue is loudness-conformed (−15 LUFS voice over
   the −18 LUFS clip bed), placed with `adelay`, joined into one voice rail; the
   film's own track is ducked under it with the same sidechain values the
   Producer program mix earned (`threshold=0.02:ratio=6:attack=25:release=350`);
   the program then relocks to the delivery loudness target. Picture is stream-
   copied — narration never re-encodes the cut — except for a tail hold (below).
4a. **The film ends when the last line ends.** The booked length is where the
   picture stops, not where the voice stops: `tail_hold_for()` measures how far
   the final cue plays past the picture and clones the last frame for that long
   plus a breath (`TAIL_BREATH_S`), capped at `MAX_TAIL_HOLD_S` (30 s), with the
   location bed padded under it on the room-tone floor. The hold is a receipt
   (`tail_hold_s`), the master's delivered length times the sidecars, and only
   what the cap still cuts is named as an overrun — the words are never trimmed.
5. **Failure is loud.** A cut that carries lines with no narrator backend is a 409
   *before* ffmpeg spends a render. A narrator error or empty take fails the
   narration, never ships a silently mute film. A line longer than its clip window
   plus the 1.0 s tail tolerance is named in the report (`book a longer clip`),
   never trimmed — the words are authored law.
6. **The mute cut survives.** The pre-narration assembly is kept as
   `picture-lock.mp4` beside `film.mp4`, so re-narration reads it instead of
   re-rendering the assembly.
7. **Voice locks bind the narrator, not the provider.** They no longer constrain
   routing (a voice lock compiles onto Grok), and provider voice registration
   (`voices` refs, Grok `reference_audios` for speech) is gone from compilation.

## Backends

Resolved by `narration.backend()`:

- **`grok` — the default.** The Grok TTS API on the same xAI account and
  `DIONY_XAI_BASE` the picture lane rides (`XAI_API_KEY` alone selects it).
  Contract **verified live 2026-09-02** (docs.x.ai + this account):
  `POST /v1/tts` with `{text, voice_id, language, output_format}` returning raw
  audio bytes — requested as WAV at 48 kHz, the mix's native rate; the voice
  roster comes from `GET /v1/tts/voices`. Path `DIONY_GROK_VOICE_PATH`
  (default `/v1/tts`), language `DIONY_GROK_VOICE_LANGUAGE` (default `en`).

## Voice casting

The narrator voice is **cast, never defaulted**. `narration.cast_voice()` scores
the casting roster — a curated slice of the live Grok voice list
(ara/eve/luna/celeste/rex/sal/leo/atlas; voice ids and genders verified against
`GET /v1/tts/voices`, tone tags are Diony's own casting reads; overridable as
JSON via `DIONY_GROK_VOICE_ROSTER`) against the master
prompt's tone — mood, visual style, performance, light quality — plus the cue's
own delivery direction, and reads the speaking character's notes for a gender
preference. Ties break on a stable hash of the speaker, so one production casts
each character to the same voice on every assemble, and different characters
spread across the roster. Order of authority: a clip's authored `voice_ref`
lock → the site-pinned `DIONY_GROK_VOICE` → the cast. Every cue's chosen voice
and casting reason ship in the QC packet's narration section.
- **`cli`.** An on-host command template, the audiogen contract:
  `DIONY_NARRATOR_CLI` with `{text} {output} {voice} {speaker} {direction}`
  placeholders. An authored template outranks the key-derived default;
  `DIONY_NARRATOR_BACKEND` forces either.

## Performed direction

The authored parenthetical (plus the audio packet's delivery notes) does two
jobs: it reads into the **casting**, and on the Grok backend it becomes
**performed speech tags** — `perform_line()` maps direction keywords onto the
tags verified in the live docs (`[pause]`, `[long-pause]`, `[laugh]`, `[sigh]`,
`<whisper>…</whisper>`), inline expressions leading the words in the order the
direction names them, one wrapping style at most. The authored words ride
byte-identical inside the tags; subtitles always carry the clean line. An
unmapped direction performs nothing (it already shaped the cast), and
`DIONY_NARRATOR_TAGS` extends the map as JSON without a code change. Verified
live 2026-09-02: a `<whisper>` wrap cut the take's RMS to a third at equal
length, `[long-pause]` added ~1.3 s of silence — tags act, and are never
spoken aloud. Each cue's `performed` text ships in the QC packet beside the
raw line. The CLI backend gets the raw words and a `{direction}` placeholder —
tag syntax is the Grok contract's, not a universal one.

## Engine-agnostic

Narration runs on the assembled film, so it is identical under every Studio
engine option — `auto`, `grok`, and `google-flow` — and the full loop is
proven per engine (`test_narration_lands_on_every_studio_engine`): routing
honors the choice, every dialect's prompts stay speechless, and the cut gets
the same cast, mix, deliverables, and picture-lock regardless of who shot the
clips.

## Deliverables

- `film.srt` / `film.vtt` cues now cover chained one-shots (from
  `DirectorOp.narration`) and are timed by the narrator's *measured* take when it
  exists — a line may breathe past its cut and the subtitle follows the voice.
- `qc-packet.json` gains a `narration` section: recorded flag, per-cue placement
  and measured duration, and every overrun by name.
- The assemble response carries `narration: {cue_count, overruns}` (or the named
  skip reason: `narrate off` / `no lines`).

## What did NOT move

The Producer/Wan lane keeps generation-time speech (Blueprint 22): Wan S2V
animates the mouth *from* the audio it is given, so its voice physically cannot be
post-recorded. Blueprint 22's "no post-picture TTS" doctrine remains true there
and only there; on the Director lane this Blueprint supersedes the old
prompt-quoted dialogue entirely.

## One voice at a time (2026-09-02 hardening)

A production shipped shots with **multiple talking voices**: the `NO_SPEECH`
clause is a request, not a guarantee — providers voiced the on-camera speaker
anyway (the delivery clause shows a moving mouth, and a model voices what it
sees), review only ever checked that audio *existed*, and the post narrator then
stacked on top of the invented voice. Separately, the narrator rail itself could
overlap: a line that overran its clip played straight into the next cue's mark —
two cast voices at once. Three layered guarantees now stand:

1. **The location track is measured speechless.** Review carries a `speech`
   family on every clip and every chain link. With a whisper model staged
   (`DIONY_WHISPER_MODEL`), `detect_speech()` transcribes the track
   (VAD-filtered so instrumental score and ambience never read as words); three
   or more heard words, or 1.5s of speech, fail the take with the words named,
   planning a `fix-audio` retake through the ladder. No model → an honest
   `unmeasured`. The pre-28 `script_conformance` check — which demanded the
   provider *speak* the scripted line, the defect backwards — is deleted.
2. **The cut is guarded even when review couldn't measure.** Assembly accepts a
   per-clip `mute` spec (the clip is cut with a silent bed; the room-tone floor
   keeps the join alive). Before the cut, `assemble` runs a speech guard: a
   track that measures a talking voice is muted whatever the clip class, and a
   line-carrying clip whose track cannot be verified on this host is presumed
   dirty and muted — the narrator is about to speak over it. Every mute is a
   named receipt in the response (`speech_guard`); `speech_guard: false` ships
   the tracks as delivered.
3. **Narration cues can never overlap.** `layout_cues()` starts each cue no
   earlier than the previous line's end plus a breath (`MIN_CUE_GAP_S`).
   Shifts are receipts on the narration report (`shifts`); overruns stay named
   with the lasting fix — book a longer clip. The words are never trimmed.
