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
   copied — narration never re-encodes the cut.
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

- **`grok` — the default.** The Grok voice API on the same xAI account and
  `DIONY_XAI_BASE` the picture lane rides (`XAI_API_KEY` alone selects it).
  OpenAI-compatible speech contract (`model`/`input`/`voice`/`response_format`),
  path `DIONY_GROK_VOICE_PATH` (default `/v1/audio/speech`), model
  `DIONY_GROK_VOICE_MODEL` (default `grok-voice`), default voice
  `DIONY_GROK_VOICE` (default `Ara`); a clip's `voice_ref` overrides per cue.
  Endpoint shape is re-verified against live docs before any billable rollout,
  the same doctrine as `providers/grok.py`.
- **`cli`.** An on-host command template, the audiogen contract:
  `DIONY_NARRATOR_CLI` with `{text} {output} {voice} {speaker} {direction}`
  placeholders. An authored template outranks the key-derived default;
  `DIONY_NARRATOR_BACKEND` forces either.

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
