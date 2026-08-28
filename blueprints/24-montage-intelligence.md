# Blueprint 24 — Montage Intelligence

Status: implemented contract; measured gates arm with the site's QC calibration, optional model lanes arm with local weights

Owner: Diony AI

Applies to: Studio Idea compiler, Wan prompt compiler, Producer FARM AutoQC, retake policy, Finish, Mix lock, Gallery deliverables, tests

Depends on: Blueprints 05, 10, 19, 20, 21, 22, 23

## Product decision

Diony's camera, finish, and mix were already receipt-addressed and measured. What a
studio still asks after "did it render?" is "does it cut?" — whether the rundown
reads as coverage or as a slideshow, whether a prompt described a frame or a
feeling, why this attempt became the print, and whether the master honours the
Brief it was promised under. This Blueprint adds that layer of production craft
as contracts, deterministic gates, and durable evidence. It changes no Producer UI.

The strategies were studied from the open-source OpenMontage production system
(calesthio/OpenMontage, AGPL-3.0). **No OpenMontage code is used**; every rule
here is an original Diony implementation of the studied practice, adapted to a
Wan2.2 / local-DeepSeek / ffmpeg appliance. Nothing in this Blueprint calls a
network service. The only new model lanes — a local Whisper transcript and a
local CLIP encoder — are optional, weight-gated, and unmeasured when absent.

## 1. Rundown variety review (Studio, before any GPU time)

`diony_contracts.montage.review_rundown` scores the compiled rundown on a 0–5
risk scale (lower is better; `<2 strong`, `<3 acceptable`, `<4 revise`, else
`fail`; 0.6 per violation). Violations, each with its fix:

| Check | Violation |
|---|---|
| Framing dominance | one framing class books more than half the picture shots |
| Framing run | three consecutive picture shots share a framing class |
| Static monotony | more than 60% of picture shots are locked-off |
| Lighting | every shot shares one lighting plan |
| Hero | no hero shot declared (nothing rolls a second candidate) |
| Hero contrast | the hero shares its framing with an adjacent shot |
| Generic subjects | ≥30% of subjects lean on phrases that constrain no pixels ("a person", "modern", "stunning") |

Framing classes follow the prompt compiler's own vocabulary (`shot_size_for`:
≤24 mm wide, ≤40 medium, ≤65 medium close-up, else close-up). **Spoken shots
are their own class**: they are declared close-up cuts to the speaker
(Blueprint 23), so they never count toward dominance, runs, or the static ratio.
A `character_and_background` lock waives the camera checks and records the
waiver.

`coverage_recipes_with_review` diversifies cycled coverage deterministically
before Shots are compiled (`diversify_coverage`): the third shot of a framing
run is reframed from the pool, and locked-off picture shots receive a push-in,
pan, or track in turn until fewer than 60% are static. Dialogue recipes are
never reframed. The change lines are recorded as a `coverage` decision.
`expand_idea` stores the review on `Project.rundown_review`; a `revise`/`fail`
verdict adds the conductor note `n-rundown-variety` beside the rundown. The
Bible gate stays human; the review informs, it does not block.

## 2. Prompt craft lint (contracts `wan_prompt`)

Mood adjectives are stripped from every idea-derived prompt layer at compile
(`strip_mood_adjectives`: beautiful, epic, stunning, cinematic, masterpiece,
4k, … — orphaned connectors are healed and articles re-agreed). The scene
layer's technical phrase "cinematic Rec.709 color" is exempt. Refinements that
reintroduce mood words, generic subject phrasing, overlays placed on the depth
axis, or quoted dialogue in the motion layer are recorded as
`WanPromptPlan.craft_notes` — advisory, never a dispatch blocker, visible to
the retake director.

## 3. Sampled-frame AutoQC evidence (FARM)

`diony-frame-quality-v1`: five frames (first, quarter, middle, three-quarter,
last) on a 640×360 luma proxy. Sharpness = variance of a 3×3 Laplacian; exposure
= mean luma 0–255; contrast = luma standard deviation; a black frame is mean
<10 and spread <6. `QCReport` records `sharpness_min`, `brightness_min/max`,
`contrast_min`, `black_frames`, `frame_quality_ok`.

* **Black frame → hard failure** ("Black frame detected …", auto-retake), under
  the same arming rule as the temporal gates: a site calibration receipt or
  `DIONY_MEASURED_QC_REQUIRED=1`. The count is recorded on every site.
* Sharpness (`DIONY_SHARPNESS_MIN`, default 20), exposure (`DIONY_BRIGHTNESS_MIN`
  35 / `DIONY_BRIGHTNESS_MAX` 225), contrast (`DIONY_CONTRAST_MIN` 18) are
  advisory notes appended to a passing verdict.

**Chain colour drift.** On a previous-frame chain, the mean per-channel shift
between this print's first frame and the previous print's last frame is recorded
as `chain_color_drift` (0–1). Above 0.06 it is an advisory note.

**Prompt adherence (optional).** With `DIONY_CLIP_MODEL` naming a local
transformers-format CLIP checkpoint directory (`openai/clip-vit-base-patch32`
class, no network), the pooled embedding of the five sampled frames is compared
with the subject layer; the cosine is `QCReport.prompt_adherence` and joins
`attempt_score` at weight 0.15 (0.22 reads as "not the prompt", 0.30 as strong).
Unmeasured it is `None` and the other weights renormalise.

**Transcript (optional).** With `DIONY_WHISPER_MODEL` naming a local
faster-whisper model directory, the dialogue Wan delivered is heard back:
`SpeechQCReceipt.transcript_word_accuracy` (multiset fraction of the authored
words heard) and `transcript_last_word_ok`. Accuracy below 0.6 is a measured
speech failure ("Voice audio failed: …", bounded retake). Absent the model the
fields stay `None` and gate nothing.

## 4. Decision log

`diony_contracts.montage.Decision` — stage, category, subject, the options
considered (each with score, reason, `rejected_because`), the selection, the
reason, and a confidence that is never 1.0 for a heuristic. Append-only.
`Project.decisions` keeps the most recent 400; `projects/<id>/decision-log.jsonl`
keeps every entry. Recorded today:

| Category | Where | Options |
|---|---|---|
| `coverage` | Studio expansion | cycled pool vs diversified coverage |
| `sizing` | roll | measured p50 sizing vs no estimate |
| `retake` | `auto_retake_for_qc_failure` | DeepSeek-directed rewrite vs keyword repair, with the distant seed |
| `settle` | `settle_exhausted_shot` | every measured attempt with its `attempt_score` |
| `hero_select` | `select_hero_print` | every landed candidate, ranked |
| `look` | program stitch | neutral vs the Bible look |
| `final_review` | mix lock | present / lock with issues / refuse |

## 5. Program look and chain colour match (Finish)

`Bible.look` (`neutral` default, `cinematic_warm`, `cinematic_cool`,
`moody_dark`, `bright_clean`, `vintage_film`, `high_contrast`) and
`Bible.look_intensity` (0.3–1.0, default 0.6) apply **once, program-wide**, at
the 1080 stitch — never per clip — as an ffmpeg segment blended against the
ungraded picture (`look_filter`). `Bible.lut_asset_id`, declared since
Blueprint 05 and never read, is now honoured: a `.cube` asset is applied after
the profile at the same intensity. Every profile keeps saturation ≤1.10 so skin
stays on the vector line; the legalizer limiter runs after the look. A single-clip
program takes the filter path when a look is set instead of stream copy.

`Bible.chain_color_match` (default on): when a previous-frame print measured
`chain_color_drift ≥ 0.02`, Finish applies a per-channel gain/offset that moves
the print halfway (strength 0.5) toward the boundary frame's statistics before
super-resolution (`finish_clip(color_match=…)`, also carried by the Finish
worker's `/v1/finish`). Half, never full, so a deliberate lighting change is
not erased; the activity feed names the matched take.

## 6. Final review, sidecars, and deliverables (Mix lock)

`run_final_review` runs before the Mix gate closes on a live 1080 master:

* `technical_probe` — container, 1920×1080, fps, frames, audio stream, codec.
* `visual_spotcheck` — the five-frame evidence of §3 on the master.
* `audio_spotcheck` — measured LUFS / true peak from the mix receipt; silent
  when dialogue was authored, or clipping, is fatal.
* `promise_preservation` — delivered runtime against the Brief's goal (≥ goal −
  max(1 s, 10%); overshoot from dialogue fit is a note, not a miss; first-reel
  and Sample briefs size their own targets), fps vs Bible, loudness ±1 LU,
  motion ratio (fraction of selected prints whose measured motion passed;
  0.7 floor), settled and failed prints.
* `subtitle_check` — authored dialogue is delivered as `master_1080.srt` and
  `master_1080.vtt` beside the master (`dialogue_cues`: cue timing from each
  Shot's booked window, turns sharing a window by word count, ≤42 characters
  per line, two lines).

`fail` refuses the mix lock with the evidence (`mix lock refused by final
review: …`); `revise` locks with the issues on `Project.final_review` and in
`program/<tl>/final-review.json`. `qc-packet.json` (review, rundown review,
decision log, per-Take QC and generation receipts, mix receipt) and the
subtitles join `Project.deliverables` with the ProRes master and program stem
(`DeliverableKind.SUBTITLES` is new). A dry-run or proxied desk records a
`skipped` review.

## 7. Measured sizing

At roll, `announce_sizing` reads the site's performance journal: with at least
three measured camera clips it announces `takes × (camera p50 + finish p50)`
with a 1.3× retake allowance on the high end and records a `sizing` decision;
otherwise it records that no estimate exists. Blueprint 19's rule stands: never
a fabricated ETA.

## 8. Tests never touch the network

`conftest.py` at the repository root patches `socket.connect`, `connect_ex`,
and `create_connection` for the whole session: loopback and AF_UNIX pass,
anything else raises. `DIONY_ALLOW_NETWORK=1` lifts it for an explicitly live
run. The guard is itself tested.

## 9. Host provisioning for the optional lanes

The weights are pinned in `infra/models/manifest.yaml` (`optional: true`,
`required_for: []`) and pulled best-effort by Studio App after the pack
(`MONTAGE_PACK` in `apps/wizard/apply.py`; `DIONY_MONTAGE_PACK=0` skips; a
failed optional pull logs `HF_OPTIONAL_FAIL` and never fails the pack):

| Weights | Revision | Destination |
|---|---|---|
| `Systran/faster-whisper-large-v3` (MIT) | `edaa852e…` · 7 files · 3.09 GB | `/models/audio/faster-whisper-large-v3` |
| `openai/clip-vit-base-patch32` (MIT) | `3d74acf9…` · 10 files · 610 MB (Flax/TF copies skipped) | `/models/diony/clip-vit-base-patch32` |

The GPU start script (`gpu.py`) exports `DIONY_WHISPER_MODEL` / `DIONY_CLIP_MODEL`
only when the staged files are complete, installs
`infra/runtime/montage-overlay.txt` (faster-whisper, CTranslate2, transformers,
Pillow) into the Producer interpreter only then, and unsets the lane again if
the packages did not land (`GPU_MONTAGE_OVERLAY_UNAVAILABLE`) — an absent lane
measures nothing. The same repair step now also applies
`infra/runtime/wan-tts-overlay.txt` (Blueprint 22's CosyVoice imports), which
previously had no reader. Both env values are part of the running Producer's
environment check, so a pack that completes later relaunches the Producer at
the next idle boundary like every other env change.

## 10. Cost and staleness rules

* The final review is cached by `(timeline_id, master_sha256)`: the advance
  controller polls every second and a failed review must not re-probe, re-decode,
  and re-log each tick; `final_review_refuses_current_master` stops the lock
  attempt entirely until a retake or re-mix changes the program bytes.
* Frame sampling on a program master seeks (`-ss` before `-i`, one decode per
  sample) above 600 frames; an hour-long ProRes master costs five decodes.
* A retake (`reopen_picture_for_retake`) and a restart
  (`recalibrate_stopped_project`) clear `final_review` and `deliverables`;
  they are measured again at the next mix lock.
* Transcript word matching is fuzzy (difflib ratio ≥ 0.8 for words of four or
  more letters) so an ASR spelling of a name never fails a correct line.
* `.diony.zip` exports carry `decision-log.jsonl`, `final-review.json`,
  `qc-packet.json`, and the subtitle sidecars beside the master.
* JSON Schemas for `Decision`, `RundownReview`, `FinalReview`, and
  `ProductionSizing` are exported to `packages/contracts/jsonschema/`.

## 11. Remediation, evidence surfaces, and density

* **A failed final review remediates itself.** Every sampled black frame is
  mapped through the timeline to the print that produced it
  (`FinalReview.offending_shot_ids`). When the recommended action is `retake`,
  `remediate_final_review` marks each offending print `FAIL` with
  "Black frame detected in the program master (final review sample)", mints
  its bounded retake through the normal `auto_retake_for_qc_failure` path
  (DeepSeek direction, distant seed, four-Take cap), reopens picture, and
  restarts the farm for those Takes; the run then finishes, stitches, mixes,
  and reviews the new master. A print at the cap is held for a human with
  the evidence on the feed. Remediation runs once per reviewed master
  (`FinalReview.remediation`) and is a `retake` decision in the log.
* **The Production rail knows.** `production_standard` reports a current
  failed review as `QC HOLD` with a "Final review failed: …" blocker and
  never claims `master_ready`; the payload carries `final_review`,
  `rundown_review`, and `deliverables` summaries for any client. The "now"
  line names the failure and the remediation.
* **Sampling density scales with runtime** (`program_sample_count`): five
  frames up to two minutes, one more per two minutes, capped at 32 — an hour
  master is checked on 32 seeked frames.
* **Evidence surfaces.** `GET /api/program/deliverables` lists the master,
  stems, subtitles, and QC packet with download URLs;
  `GET /api/deliverables/{id}/file` serves only a listed deliverable inside
  the project's own vault namespace. `.diony.zip` inspection reports which
  evidence files travelled (`evidence`). STOP (keep or delete) clears the
  stale review and deliverables like a retake does.
* **Transcript evidence names the words.** Digits are spelled out (0–999),
  contractions expanded, and the words not heard back are listed in the
  speech diagnostics so the retake director sees "not heard: tide".
* **Schemas cannot drift.** `test_schema_sync` fails when a committed JSON
  Schema differs from its model; `diony-export-schemas` regenerates them.

## 12. Beat-grid score alignment (Mix)

Picture is frame-locked, so the score moves to the cuts. For every `score`
cue the Mix (a) measures a `BeatGrid` locally (`diony_producer.beat_grid`:
ffmpeg-decoded 22.05 kHz mono → spectral-flux onset envelope → tempo from the
envelope's autocorrelation in 55–200 BPM → beat phase by onset energy →
downbeats every four beats phased on the <150 Hz band → one-second RMS
energy phases; `librosa` supplies tempo and beats when importable on the
host; grids cache under `runtime/audio/beatgrids/` by the cue's bytes), and
(b) decides a source in-point shift of less than one bar
(`diony_contracts.score.align_cue_to_cuts`) that lands the most hero cuts
inside the cue's span on downbeats (then on beats, then least error), capped
by the source's remaining length. Hero cuts a non-integer number of bars
apart cannot all land — the cut the music enters on is what matters; misses
are counted on the receipt, never hidden. The mix graph applies it as
`atrim=shift:shift+duration,asetpts=PTS-STARTPTS`.

Trust boundary: a cue is `rhythmic` only when the autocorrelation peak's
prominence reaches 1.8 (click tracks ≈5, pads ≈1.2); otherwise, or when no
hero cut sits inside the span, or when no shift lands even one hero cut on a
downbeat within 60 ms, the cue is `phrase_flow` and plays as planned. Ambience and
foley never move. `Bible.score_cuts_on_downbeats` (default on) switches it
off. Every cue's `ScoreAlignment` (mode, shift, aligned cuts with error in
ms, reason) is on `MixLock.score_alignments`, a `soundtrack` decision, and
the activity feed.

## 13. Edit decision list

`diony_contracts.edl.build_edl` derives one `EditDecision` per timeline
event from the locked Timeline: record/source in-out frames, the transition
family (`cut`, or `dissolve` for the eight-frame temporal blend), the role
(establishing, identity_lock, insert, reaction, dialogue, bridge, coverage,
hold, button — from the coverage slug and the speech contract), the
speaker, hero flag, engine, and a one-line reason for the cut. The list
notes whether every cut carries a reason, how many transition families are
in use (limit 4), and whether hero shots hold the longest. The final review
writes `master_1080.edl` (CMX3600: video events, `D` dissolves, `* DIONY
…` comments) and `master_1080.edl.json` beside the master, lists both as
`DeliverableKind.EDL`, and embeds the list in `qc-packet.json`. The EDL is
evidence of the edit the run made; the Timeline remains the stitch
authority.

## Operational notes

* New env: `DIONY_SHARPNESS_MIN`, `DIONY_BRIGHTNESS_MIN`, `DIONY_BRIGHTNESS_MAX`,
  `DIONY_CONTRAST_MIN`, `DIONY_CLIP_MODEL`, `DIONY_WHISPER_MODEL`,
  `DIONY_WHISPER_DEVICE`, `DIONY_WHISPER_COMPUTE`, `DIONY_ALLOW_NETWORK` (tests).
* Host packages for the optional lanes come from `infra/runtime/montage-overlay.txt`
  (§9); never at render time, never from a network call in Production.
* Every new field on `Bible`, `WanPromptPlan`, `QCReport`, `SpeechQCReceipt`,
  `TimelineEvent`, and `Project` has a default, so persisted projects load
  unchanged; `refresh_reference_shot` and re-measurement fill evidence forward.
* Not done here, recorded for the next pass: face-embedding identity beyond
  SSIM, 5.1 stems, energy-aware score dips at reveals (the energy phases are
  measured; no creative dip is applied automatically).
