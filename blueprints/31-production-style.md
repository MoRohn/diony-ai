# Blueprint 31 — Production Style: Narrated, Dialogue, Mix

Status: implemented on the Director (cloud) lane; the Producer/Wan lane keeps Blueprint 22 (generation-time speech is the only physically possible path for S2V lip-sync)

Owner: Diony AI

Applies to: the Director console (a new **Style** pill beside Voice), the session settings API, the contracts (`AudioPacket.voicing`, `DirectorOp.voicing`, `ProductionPackage.production_style`), the splitter, the compiler (all three dialects), the master-prompt brain contract, `compile_master_body`, review, the revision ladder, the speech guard, the cue sheet, assembly, deliverables

Depends on: Blueprints 27 (Master Video Prompt), 28 (Post-Assembly Narration), 29 (Look Bible)

## Product decision

Blueprint 28 made the voice track a POST instrument for every Director film:
one narrator, exact words, placement measured off the cut. That is the right
default and it stays the default. But it is one production style, not the
only one. A dialogue-driven scene wants the character's own mouth to say the
words on camera, with the provider's native audio and lip sync; a documentary
wants a narrator over picture; a drama wants both — characters speaking in
the scene and a narrator between them.

The **Production Style** is the Director's answer, chosen per session:

| Style | Scripted on-camera dialogue | Voice-over lines / one-shot narration | Narrator runs |
|---|---|---|---|
| **Narrated** (default) | post narrator | post narrator | always, when lines exist |
| **Dialogue** | voiced in the clip | voiced in the clip | only to rescue a mute take |
| **Mix** | voiced in the clip | post narrator | for voice-over and rescues |

Narrated is byte-identical to the pre-31 platform. Nothing about Blueprint 28
changes under it.

## The law

1. **The style resolves into a per-clip `voicing`, and the clip is the
   authority.** At the split (`split_script(..., production_style=)`) every
   line-carrying clip's `AudioPacket.voicing` is set to `post` or
   `generated`; every one-shot op's `DirectorOp.voicing` is set at draft
   time. The compiler, review, speech guard, cue sheet, mix, and EDL read the
   clip's voicing — never the session's style — so a late style change can
   never silently reinterpret footage already shot.
2. **The style is fixed once the script locks.** `POST …/sessions/{sid}/style`
   (and the `style` field on message / produce bodies, None-means-keep) is
   refused with a 409 after `lock_script`. One-shots drafted after a change
   take the new style; ops already drafted keep the voicing they were
   priced with.
3. **A generated clip carries its words verbatim.** The compiler replaces the
   Blueprint 28 delivery performance and `NO_SPEECH` with the spoken clause:
   the speaker, *speaks aloud, on camera, lips in sync with the words, exactly
   these words and nothing else:* the quoted line, the direction, and
   `ONLY_THE_LINE` — one voice, no narration, no other speaker. A voice-over
   line under Dialogue is heard in the speaker's voice over the picture. The
   quoted piece rides at priority 0 and `_fit_prompt` shrinks it last of all.
   Picture clips with no line keep `NO_SPEECH` under every style — only the
   LINES moved. A generated clip never travels with `generate_audio: false`.
4. **A one-shot's narration is voice-over by nature.** Dialogue voices it in
   the clip (`spoken_narration_clause`); Narrated and Mix keep it on the
   narrator. The brain contract follows the voicing: under generated voicing
   the OUTPUT CONTRACT and CLIP DELIVERY tell the brain the *system* appends
   the spoken performance, so it must neither quote the words itself nor
   write that the scene is silent. The default contract is byte-identical to
   Blueprint 28's.
5. **Review measures the line, not its absence.** A generated clip's track is
   transcribed once (`DIONY_WHISPER_MODEL`, the Blueprint 28 detector) and the
   heard words are scored against the authored line (`score_transcript`): the
   `speech` family passes when a voice is heard (it is the authored one) and a
   new `line` family fails under `LINE_ACCURACY_FAIL` (0.6) or when nothing is
   heard — naming the words heard and the words missed. No detector → an
   honest `unmeasured`. Chain links that voice their narration are measured
   the same way (`review_chain_link(..., spoken_line=)`).
6. **The fix is the line.** A failed `line` family plans `fix-line` through
   the ladder (*the character must speak the quoted line aloud, clearly,
   exactly as written, once — and nothing else is spoken*). A generated clip
   never receives the Blueprint 28 `fix-audio` directive ("remove all
   speech") — `revise_prompt` maps it to `fix-line`.
7. **The cut balances, rescues, and mutes from the script.** The speech
   guard (`_speech_guard`) reads each clip's voicing:
   - a `generated` clip's track IS the voice: it is never muted. Measured
     talking → *kept*; unmeasurable → *kept, unverified, and said so*;
     measured with NO talking voice (the provider stayed mute) → *rescued*:
     the entry is flagged `narrate_anyway` and the narrator records that
     line in post on the clip's mark (`cue_sheet` otherwise skips generated
     clips).
   - a `post` clip keeps Blueprint 28 exactly: a measured voice is muted; a
     line-carrying track this host cannot verify is presumed dirty and muted.
   - **Balance:** whenever the cut has a narrator rail at all (post lines or
     a rescue), every kept spoken track is level-matched to the narrator
     (`spec.loudness = NARRATION_LOUDNESS`, −15 LUFS) instead of the −18 LUFS
     ambience bed, so on-camera dialogue and narration sit at one voice
     level. Without a rail (a pure Dialogue film) tracks keep the bed level.
   - A generated clip is never tail-trimmed to the word-count arithmetic
     (`needed_s`): the provider paced the words, and a trim could cut the
     last of them.
   Every decision is a receipt: `speech_guard.{kept, rescued, balanced,
   muted}` and a `style` block on the assemble response (`key`,
   `spoken_in_clip`, `narrated`, `rescued`); `narration.skipped` reads
   `lines voiced in the clips` when no rail was needed.
8. **Dialogue needs no narrator.** `wants_narration` is true only when the
   cut carries post lines or a rescue; a Dialogue film assembles without any
   narrator backend and the 409 for a missing backend never fires for it.
   The Voice pill still matters there — it is the voice a rescue speaks with.
9. **Deliverables tell the truth.** Subtitles cover every line under every
   style (a generated line is timed to its clip window; a narrated or rescued
   line to the narrator's measured take); `edl.json` events carry `voicing`;
   the QC packet's narration section lists exactly the cues the narrator
   spoke.
10. **One castable voice per character — the voice card.** A spoken prompt
    that said only "a natural, clear voice" left the provider to invent one:
    a different voice per clip, the same voice for everyone, a woman read by
    a man. Every character now carries a VOICE CARD on `CharacterProfile.voice`
    (`VoiceProfile.gender / age_band / timbre / accent`, with `source`):
    the screenwriter brain writes it per character (`voice: {gender, age,
    timbre, accent}`), `voices.derive_voice` reads it deterministically from
    the role, the casting notes and the identity anchor otherwise (gender
    only when the writing says it plainly — both tells present is no read;
    an age from "34", "in her fifties", "elderly"; a timbre from what the
    writing says about how the person sounds, else the palette), and
    `voices.cast_voices` makes the cast DISTINCT — no two characters share a
    gender-and-timbre pair, later ones walking the palette or taking a pitch
    spread. `packagebuilder.build_package` casts a draft that has no cards
    (`voices.ensure_cast`), never rewriting one it has. The card rides every
    generated clip's spoken clause verbatim (`compiler.speaker_voice_card`,
    on-camera, voice-over and off-screen alike, in the priority-0 piece with
    the quoted line: *VOICE — MARA: a female voice, thirties, warm and
    bright, quick and friendly; MARA has this same voice in every clip, and
    no other character in this film shares it*), and only the speaker's card
    — never another character's. A one-shot voiced in the clip names ONE
    described voice for the whole chain (`session.spoken_voice_read`: the
    Voice pill's roster voice, or the one the Director casts for the session,
    described from its roster tags). Narrated prompts carry no card: they
    are byte-identical to Blueprint 28. The BIBLES card shows each
    character's read (`voice`, `voice_source` on the board).
11. **A character's line on the narrator rail is read in the character's
    voice.** Under Dialogue and Mix the cue sheet stamps each cue with its
    clip's `voicing`, and `cast_cues(character_voices=)` casts a cue whose
    speaker is a cast member — a voice-over Mix keeps in post, or a mute take
    the speech guard rescued — to a ROSTER voice scored from that
    character's card (`voices.roster_notes`: its gender tells and timbre
    words are the rosters' own tags), one voice per character, distinct
    across the cast (`cast_voice(avoid=)` keeps two characters off one
    roster voice while another of the right gender is free), and neither the
    Voice pill nor the site pin overrides it: the pill is the narrator's own
    voice. The receipt reads `character voice (MARA): female read; warm,
    bright — rescued in the character's voice`, and `narration.voice` on the
    assemble response names the narrator's cue, never a character's. Under
    Narrated no character voices are handed in and the single-speaker
    doctrine stands untouched.

## Trade-offs, stated

- **Dialogue trades voice consistency for lip sync.** Each clip's voice is
  the provider's; the voice card (§10) binds it to one described read per
  character on every clip, which is as far as a prompt can bind timbre
  (provider voice registration stayed gone with Blueprint 28). Mix exists
  for films that want a consistent narrator *and* characters speaking in
  frame.
- **Run length under Dialogue follows the provider's delivery.** The clip is
  booked from the word-count arithmetic as before, but the take plays at its
  full length; a line the model rushes or drags is a `line`/`duration` review
  matter, never a trim.
- **Rescue is a fallback, not a plan.** A rescued line is a roster voice cast
  from the character's card (§11), not the provider's take of the character;
  the receipt names it so the human can retake instead.

## API

- `GET /api/director/styles` — the catalog (`default: narrated`).
- `POST /api/director/sessions/{sid}/style` `{style}` — set; 409 once locked; 422 unknown.
- `GET /api/director/sessions/{sid}/settings` — carries `style`.
- `style` on `MessageIn` / `ProduceIn` (None keeps).
- Persisted as `style` in state.json; `voicing` rides every op and clip.

## Tests

`apps/director/tests/test_production_style.py`: the doctrine matrix, the
split, all three dialects, length pressure on a quoted line, the fix-line
mapping, the spoken-line review (pass / mute / off-script / unmeasured /
chain link), the cue sheet, the mix balance, the speech guard, persistence and
rehydration, the API lock, one-shots under each style, the brain contract, and
two end-to-end films: a Dialogue cut with no narrator on the host, and a Mix
cut that narrates the voice-over, balances the dialogue, and rescues a mute
take. `test_voice_cast.py`: the reads (gender, age, timbre; no guess on an
ambiguous or technical note), the distinct cast and the brain's card, the
card's clause, every drafted character cast and a pre-card draft cast at
lock, the card on Dialogue and Mix prompts (verbatim, the speaker's only,
off-screen, under length pressure) and never on Narrated, the one-shot voice
read, the narrator rail (a character's cue to a matching roster voice, two
same-gender characters on two voices, Narrated's one narrator), the board.
