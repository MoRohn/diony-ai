# Changelog

All notable changes to Diony AI are documented here.

## Unreleased

### Fixed

- Wan2.2 S2V speech lane: `ensure_s2v_tts_prompt_path` overlay passes the prompt clip to CosyVoice as a path or a tensor according to the installed API (an unpinned CosyVoice `main` failed every spoken Take with `TypeError: Invalid file: tensor(...)` on the live B200); Studio now pins the CosyVoice checkout revision.
- Screenplay compile: a line's quoted words are what Wan speaks and the direction around them (`Robot: electronic quack-beep “QUACK!”`, a `(beeping)` parenthetical) becomes the turn's performance direction instead of TTS text; an unbalanced quote is stripped. `INT. / EXT.` sluglines are scenes (numbered in order, weighted by what was written for them), every line remembers its scene, and its window is placed on that scene's shots rather than spread from shot one. The Whisper word-accuracy gate applies only to lines of six or more words.
- Wan hot-tier staging no longer holds the active Take behind a background prefetch. `_prepare_checkpoint` took the hot-cache flock for the whole copy, so the first-use prefetch of a second family (I2V-A14B, 126 GB) blocked the running Take's own `prepare_checkpoint` for the length of that copy (measured on the B200: ~8 min of "Generating" with no `generate.py`). The lock now guards only verify / reserve / land, the copy runs unlocked into a per-caller `.partial-` directory, a same-family caller joins the in-flight prefetch instead of copying twice, and a lost race discards its own bytes and reuses the landed cache. Also: the soundtrack planner now says on the feed when neither a licensed cue library nor generated audio is available, instead of silently planning no score.
- Studio Compute line no longer collapses into overlapping LOCAL/LEASE cards on a signed lease site. The Connect console card was keyed on `body[data-step="connect"]`; the signed-GPU identity poll moves the active line to Connect while the operator is still looking at Compute, so the rule re-gridded the shared form under the Compute cards (first column to zero, both lanes and sketches drawn on top of each other). The console is now keyed on the line actually painted (`.do[data-line]`), `paintLesson` keeps `body[data-step]` in sync with the painted line, the poll no longer clobbers a line opened from the rail, the lease column's floor is capped at the workspace width so any mismatch stacks instead of overlapping, and the static stamps advance to `brand17`.

### Added

- **Montage intelligence (Blueprint 24).** Production craft learned from studying the open-source OpenMontage system, re-implemented for Diony's local Wan2.2 / DeepSeek / ffmpeg appliance (no OpenMontage code, no network calls): a rundown variety review at Idea expansion (framing dominance and runs, static monotony, lighting, hero contrast, generic subjects) with deterministic coverage diversification and a conductor note on `revise`/`fail`; mood-adjective stripping and craft lint on every Wan prompt layer; sampled-frame AutoQC evidence (Laplacian sharpness, exposure, contrast, black frames — black frames reject under the measured-QC arming rule) and chain colour drift; an append-only decision log (`Project.decisions` + `projects/<id>/decision-log.jsonl`) for coverage, sizing, retake direction, settle ranking, hero selection, look, and final review; a program-level `Bible.look` / `look_intensity` applied once at the 1080 stitch with the long-declared `Bible.lut_asset_id` finally honoured; half-strength chain colour matching at Finish for drifted previous-frame prints; a post-mix final review (container, frames, audio, Brief promise, subtitles) that refuses the mix lock on `fail`; `.srt`/`.vtt` dialogue sidecars and a `qc-packet.json` listed as `Deliverable`s; measured production sizing on the activity feed; optional local Whisper transcript verification of the Wan speech lane (`DIONY_WHISPER_MODEL`) and optional local CLIP prompt adherence (`DIONY_CLIP_MODEL`) that gate nothing when absent; and a socket-level offline guard installed in every package's test session. Production readiness: the optional weights are pinned in `infra/models/manifest.yaml` and pulled best-effort by Studio App (`MONTAGE_PACK`), the GPU start script exports the lane env only when the staged files are complete and installs `infra/runtime/montage-overlay.txt` into the Producer interpreter (also applying Blueprint 22's `wan-tts-overlay.txt`, which had no reader), the final review is cached per master hash and stops a failed master's lock loop, program-master sampling seeks instead of decoding the whole file, retakes and restarts clear stale review/deliverables, transcript matching is fuzzy for names, `.diony.zip` exports carry the new evidence, and JSON Schemas are exported for the new contracts. Enhancement pass: a failed final review names the prints whose frames sampled black and retakes them automatically within the four-Take cap (once per master, logged), the Production rail reports a failed review as `QC HOLD` with the evidence and carries `final_review`/`rundown_review`/`deliverables` summaries, spot-check density scales with runtime (5–32 seeked frames), `GET /api/program/deliverables` and `GET /api/deliverables/{id}/file` expose listed deliverables inside the project namespace, STOP clears stale review evidence, transcript scoring spells out numbers, expands contractions, and lists the words not heard, `.diony.zip` inspection reports which evidence travelled, and a schema-sync test keeps the committed JSON Schemas honest. Deferred items delivered: **beat-grid score alignment** — Mix measures each score cue's beat grid locally (numpy spectral flux + autocorrelation, librosa when present) and shifts the cue's in-point by less than a bar so downbeats land on the hero cuts (60 ms tolerance, trust boundary on pulse prominence, `Bible.score_cuts_on_downbeats`), recorded as `MixLock.score_alignments`; **edit decision list** — a per-cut `EditDecisionList` with reasons, transition families, roles, and speakers, delivered as CMX3600 `master_1080.edl` + JSON beside the master and embedded in the QC packet. **Face-embedding identity** — OpenCV Zoo YuNet + SFace (Apache-2.0, native cv2, pinned by Git-LFS sha256 and pulled best-effort) measure the SFace cosine between the conditioning still's faces and each sampled frame's main face; a measured miss below 0.363 is a hard, auto-retaken failure and the score joins `attempt_score`. **5.1 program mix** — `Bible.audio_layout="stereo+5.1"` builds a 5.1 program from the rails (centre dialogue, front score/foley, surround ambience, low-passed LFE, same ducking and two-pass loudnorm), delivered as `program-mix-51-v<N>.wav` and muxed as a second PCM track on the master.

### Changed

- Studio's Compute and Connect lines share one adaptive console. The sheet is laid out for the line on screen — `body[data-step]` follows the rail, so Compute opened from the sidebar keeps its console layout instead of inheriting Connect's narrow column, and a running setup agent no longer hides the form of a line opened from the rail (it returns, repainted, when the operator comes back to the active line). Connect's SSH line is now a console card (Host · login · port · vault in a responsive grid, the resulting `ssh` line as a receipt) that uses the sheet's width like Compute does. Layout answers to the workspace's own width through container queries (`folio.css`, Ready 37): the card grids collapse on the space they actually have, not the window; the mast, rail, head, and Continue scale with the window; short windows (≤720 px) drop the mast to 4.75rem and tighten the head; the 761–900 px band scrolls again; and a fold shade tells the operator when more of a line waits below. The fabric sketch centres in its card instead of leaving a hole beneath it. The dead-GPU reason reads once at the head; the status line carries the action. Studio static stamp is `brand16`.
- Dialogue moved onto Wan2.2's own speech lane. A spoken Shot now hands Wan S2V the authored line and a per-character prompt clip (`--enable_tts --tts_prompt_audio --tts_prompt_text --tts_text --num_clip 1`); Wan performs the line and animates the face in one pass and muxes its speech into the negative. Producer measures only what Wan delivered (audio present, voiced-speech presence, the line finishing inside the window, Bible loudness and true peak on the conformed mix stem) and binds it to the negative as a `SpeechClipPackage`. The spoken prompt plan directs the performance (speaker, authored emotion, articulated mouth, still in pauses and after the last word, no one else speaking, no subtitles) and never carries the words. `VoiceCasting` (library slot by roster order, or an attached 5–15 s reference clip with its transcript) replaces `VoiceProfile`; `WanSpeechRequest` / `SpeechQCReceipt` replace `PerformanceScript` / `VoiceQCReceipt`; `GET /api/voice-castings` and `POST /api/characters/{id}/voice-casting` replace the voice-profile lock routes.
- Removed the Diony-side CosyVoice2 venv, worker, CAM++ identity floors and calibration, the studio-audio adapter command (`DIONY_VOICE_BACKEND_COMMAND`), the YuNet/MediaPipe mouth-sync QC, the casting panel and speaker face crops, head trim/hold alignment and its render headroom, and the still-mouth guidance raise. Studio App's CosyVoice2 and mouth-sync stages became one **Wan2.2 speech pack** stage that pins Wan's own TTS dependencies (CosyVoice checkout and CosyVoice2-0.5B) beside `generate.py`, installs `requirements_s2v.txt` + `infra/runtime/wan-tts-overlay.txt` into the Wan host interpreter, and seeds the casting library with transcripts. `DIONY_WAN_TTS_PROMPT_LIBRARY` / `_AUDIO` / `_TEXT` replace the `DIONY_COSYVOICE_*` and `DIONY_FACE_*` keys.
- Pre-camera voice holds no longer spend the four-Take render cap. Each synthesis-stage hold used to mint a new camera attempt, so sh-060's first actual render was "attempt 4 of 4" with one retake of picture left; a hold recorded before any negative now re-rolls in place and only measured renders count as attempts.
- Voice harshness/sibilance are measured frame-wise over speech-active frames (`spectral_band_ratios`). The old single whole-file Hann window weighted the audio by where it sat in the file: Lila's line measured 0.108 raw and 0.302 the moment it was padded with silence to the shot, with no processing in between. Frame-wise the same voice reads 0.18 raw / 0.19 clean / 0.23 processed. Audio holds now name the gate and its numbers ("harshness 0.356 exceeds 0.250 (this voice's raw render 0.108 + 0.05)") instead of "locked identity or studio-audio QC"; gate version `voice-gates-v4`, so holds judged under the old measurement re-roll on resume.
- Voice harshness/sibilance are measured frame-wise over speech-active frames (`spectral_band_ratios`). The old single whole-file Hann window weighted the audio by where it sat in the file: Lila's line measured 0.108 raw and 0.302 the moment it was padded with silence to the shot, with no processing in between. Frame-wise the same voice reads 0.18 raw / 0.19 clean / 0.23 processed. Audio holds now name the gate and its numbers ("harshness 0.356 exceeds 0.250 (this voice's raw render 0.108 + 0.05)") instead of "locked identity or studio-audio QC"; gate version `voice-gates-v4`, so holds judged under the old measurement re-roll on resume.
- A voice hold judged under an older gate re-rolls itself. Pre-camera voice holds now carry the gate version (`[gates:voice-gates-v3]`); on camera resume a hold without the current tag is re-queued once (synthesis, no Wan render) instead of waiting for a human retake — sh-060 (2026-08-27) stayed held on the old 0.25 harshness ceiling because its last attempt was judged seconds before the Producer relaunched with the fix. And the passed clips no longer wait behind a held Shot: when the farm has nothing left to roll on its own, every passed or settled take without a 1080 master finishes now (`finish_ready_takes_early`); the program master still waits for the decision.
- Retakes were judged on the previous take's evidence. A reused audio package carried every diagnostic line of the take it came from — faces, lag, the wrong-face defect — the re-measure appended its own lines after them, and the farm parsers took the first match: sh-040 t3/t4 failed "wrong face" on t1's coordinates, and head alignments were computed from a lag measured on another render. AV diagnostics are now stripped on reuse and replaced on re-measure (`strip_av_diagnostics`), and every parser reads the latest measurement.
- Production run audit, 2026-08-27 (11-shot kitchen rundown, 3 h in): six gaps found from per-take evidence and fixed. (1) Mouth-sync QC judged "the best-moving face", so the cast speaker's under-articulated mouth (aperture std 0.018) lost to a flapping edge artefact, the verdict read "wrong face", and the articulation-drive retake never fired — four attempts on sh-040 at the same guide. The camera's sidecar now tells QC where the cast face is (`expected_face`) and that face is judged. (2) A passing close-up take "learned" the speaker's position in crop coordinates over the human cast; learning never overwrites and never reads a close-up. (3) Lila's audio failed a fixed harshness ceiling (0.356 > 0.25) at 0.96 identity; harshness is now judged against the voice's own raw render plus 0.05 (capped 0.60) and the receipt carries that ceiling. (4) Dialogue windows are counted per speaker so the rundown grows instead of one crop mouthing two characters' lines. (5) The offset gate is 50 ms (one S2V frame is 62.5 ms; ITU-R BT.1359), which passed a 47 ms / 0.92-confidence take that had settled at the retake cap. (6) Receipts record the dispatched guide scale (they showed the lane default after a retake raised it).
- A code refresh never kills a Producer that is merely slow to answer. The GPU start script's busy check read an empty 2-second status call as "idle" and killed a Producer that was loading CosyVoice for the first spoken Shot (2026-08-27, right after Produce); the desk then showed the casting panel from its own faceless fallback. The check now fails closed (any failed or empty status call is "busy", a `shooting` project is busy between renders, 6 s timeout), `GET /api/casting` answers 503 "GPU Producer is restarting" instead of the desk's local project, and the panel retries every 5 s with that message until the GPU answers; a cast placed during the restart waits for the line and lands.
- Lip-lag measurement on periodic speech. Measured 2026-08-27 (sh-010-t2): −3.05 frames before alignment, +8 after a 3-frame hold — the lip/audio correlation carries near-equal peaks a phrase apart and "the tallest" jumped peaks. `av_sync.nearest_peak` now prefers the peak nearest zero lag among peaks within 0.05 of the maximum and reports the alternatives in the diagnostics; `align_spoken_head` re-measures after every trim/hold and restores the untouched render (receipt re-addressed) when the line measures farther off than before, so an ambiguous lag never turns into a worse take. A retake minted on camera resume now inherits the B200 attention canary bound to the take it replaces (`_rebind_canary_after_resume`) — t2/t3 had rendered on the plain lane (1233 s) while the receipt stayed bound to t1.
- Voice performance consistency floor 0.75 → 0.70 (`VoiceQCReceipt.performance_consistency_min`), measured 2026-08-27 on a correct, aligned take (offset 6.6 ms, confidence 0.92, speech 0.65 · silence 0.79 = 0.71) that was spending a 14-minute retake on two frames of disagreement; Blueprint 22 carries the measurement. On camera resume, a *measured, qualified* voice rejection whose stored numbers meet the current floors is re-judged as a pass (`_rejudge_stale_voice_floors`) instead of re-rendered — floors are recalibrated from evidence, and a take that was measured does not need to be shot again to be judged again.
- S2V head alignment actually lands. The lead-trim/hold remux used `apad` + `-shortest`; on the host's ffmpeg 6.1 that pair ends with "Error while filtering: No space left on device" — the sync queue's ENOSPC, not the disk (reproduced on the vault *and* /tmp, complete file written both times). Every spoken take with a whole-frame lip lag therefore lost its alignment and spent a retake (or, as the B200 attention canary, stopped Production). The remux now pads the locked audio to exactly the picture (`apad=whole_dur`) and cuts with `-t`; the earlier retry/temp-dir fallback stays for a real ENOSPC. A B200 canary held on *unqualified* evidence no longer stops the run: the Shot holds like any other, the rundown continues on the plain lane, and the canary is promoted when its re-judge on resume passes (`_promote_rejudged_canary`).
- Mutations wait out a GPU Producer restart instead of failing. On a signed lease the desk proxies every write to the GPU Producer; during its two-to-three-minute restart after an update, an image dropped on Idea used to answer "GPU control plane is not ready" / "did not accept the request" on the first miss. The proxy now retries every 3 s for up to `DIONY_GPU_MUTATION_WAIT_SECONDS` (default 360) before answering, and when it does give up it says why, how long it waited, and that nothing was applied locally (`Retry-After: 30`). Reads keep falling back to the local project immediately.
- Desk ↔ GPU control plane no longer relaunches a starting Producer in a loop. Measured 2026-08-27 after a desktop rebuild: the GPU Producer needs ~2 minutes to answer on :8788 (its lifespan resumes the camera before uvicorn binds), the start script only waited 10 s before reporting `GPU_PRODUCER_ENV_MISMATCH`, the desk treated that as a failed start, tore down the tunnel, and every subsequent request (a browser upload, the reconcile thread) re-ran the script — which killed the launching process and started the clock again; meanwhile every mutation answered "GPU control plane is not ready". Now: the script reports `GPU_PRODUCER_LAUNCHED` / `GPU_PRODUCER_STARTING` and never kills a process under five minutes old that carries the current code stamp; the desk keeps the tunnel and answers "starting" for a five-minute grace window after a launch instead of tearing down; and `ensure_gpu_producer` is single-flight so two callers cannot run two start scripts at once.
- Production retake root causes, measured on the signed B200 (2026-08-27, `studio-68481-12b7…`, sh-010 × 4 renders at ~21 min each): (1) the speaker close-up was cropped on the brief's staging cue ("Marcus stands in the centre") while the still put Marcus on the right, so every take animated Lila with Marcus's line — the cue is now a suggestion for a new **Casting** panel, never a reason to crop, and a spoken Shot with several faces and an uncast speaker is a durable pre-camera hold ("Casting needed…", no Take spent, rundown continues; `GET/POST /api/casting`, `farm.reroll_casting_holds`); (2) the measured +2-frame lip lag was eligible for the head trim but the trim failed with ENOSPC on the vault and QC then failed the untrimmed 120 ms offset and spent a retake — the remux now retries and falls back to the container temp dir, and a still-unwritable alignment marks the timing evidence *unqualified* (a one-take hold re-aligned on resume) instead of a retake; (3) the mouth-sync gate now refuses a take whose animated face is not the cast speaker's face (`negative_720.conditioning.json` records `speaker_face`); (4) dialogue books its close-up (reference still, overlap 0, focal ≥ 50) whatever the reference lock says — lock-NONE briefs were dispatching "wide shot, 24mm" prompts over a close-up keyframe; (5) the spoken scene layer no longer carries the world fragment ("at them expectantly, already nodding. Lila doesn't uncross her arms") and the world summary is a whole placing sentence; (6) the close-up crop floor is 640 px (a 2× upscale, not 2.67×). Blueprints 22 and 23 carry the casting gate and alignment hold.

- Mouth-sync floors lightened: `VoiceQCReceipt.lip_sync_confidence_min` 0.90 → 0.80 (correlation ≥ 0.20 instead of ≥ 0.60; a qualified measurement still needs a peak above `MIN_CORRELATION`) and `performance_consistency_min` 0.85 → 0.75, so a small articulating face is not held for landmark jitter. Blueprint 22 carries the new gates.
- Hugging Face auth is now the **Diony-Enterprise HF Token**, owned by the Studio backend (`diony_wizard/enterprise.py`, override with `DIONY_ENTERPRISE_HF_TOKEN`). Every pack pull and existing-site repair uses it automatically. The `HF_TOKEN` input is gone from Compute (new lease and existing-compute repair), App, and Ready; a pasted token is ignored and never stored, and the `.cluster/hf.token` file is no longer written or read. The token still never appears in public API state, the workbook, `site.json`, signed reports, or error text.

### Added

- Casting panel on the Production page. When a speaking Shot's still holds several faces, Producer asks who is speaking *before* the Shot reaches the camera (`run.casting.unresolved`, face detection cached per still) and holds it with "Waiting for casting" (amber, `casting` clip state) rather than a failure if it gets there first. Each speaking character shows a row of face thumbnails cropped from the still (CSS-cropped, no server round-trip), numbered left to right on the still; tapping one casts it (`POST /api/casting`), a face already cast to someone else moves to the new character and releases the old one, the brief's staging cue is a dashed hint only, all held Takes (hero siblings included) re-queue without spending a Take, and the panel reports progress ("2 of 3 cast"), errors, and a Done dismissal. Keyboard: the thumbnails are radio buttons (Enter / Space). Verified in a browser against real YuNet detection on the kitchen still.
- The desk waits for that identity before choosing where a launch lands: `landing_url` keeps polling (bounded, `IDENTIFY_WAIT_SECONDS`) while Studio reports `signed_gpu` unverified/identifying, treats a missing GPU as an unsigned site (Studio → Connect), and only opens Producer once the GPU answered — a restored lock no longer reads as signed the instant Studio starts.
- Studio identifies the signed GPU before it says "Setup is locked". A signed lease lock names a box by IP and port; after the provider stops or recycles that box, Studio used to restore the lock and open on "Setup is locked. Open Producer." with Producer pointed at a GPU that no longer existed. Studio now checks the Connect line (TCP, SSH, `nvidia-smi`) on every start and every open — the locked sheet reads "Identifying the signed GPU." without an Open Producer link until the box answers — and when the line is dead or carries the wrong GPU it lands on Connect with the host, login, and port prefilled and the reason on the line, skipping the intro. The site lock on disk stays for Producer until the next Sign rewrites it or Start over deletes it. Pytest, `DIONY_APPLY_FAKE`, and `DIONY_PROVE_OFF` record the signed GPU without opening a socket.
- Adversarial review closure across the overhaul. A full-diff review produced 18 verified-or-triaged findings; the real ones are fixed: legacy over-booked projects reconcile to measured camera truth at program stitch instead of failing every finish run; a stale temporal-measurement version now re-measures the landed negative once (at advance and picture lock) instead of silently stalling; a post-SR-rejected master is deleted and can never re-enter through the next pass's adoption branch (which now runs the same post-SR gate and records measured frames); a soundtrack plan that fails verification is a loud Mix hold, never a silently stripped master; voice timing targets camera-deliverable frames so legacy bookings stop wasting a render on audio that cannot fit; voice casting indexes the project roster (not per-shot dialogue order) so a character keeps one voice across ensemble and solo shots; dialogue windows ride only on bookings long enough to hold them (72-frame floor; short lane tails stay picture-only); the AV activity gate judges against the clip's own loud level so wall-to-wall dialogue qualifies; empty-choices brain responses fall back instead of erroring the Produce request; the bible pass runs only before any take exists so recovery rolls never rewrite the grammar landed takes were shot under; hero selection also runs on the adopt/resume recovery paths; auto-calibration is suppressed only by an explicit operator OFF; the library scan's ffprobe+sha cost now waits until planning actually happens; and the production monitor branches on a structured AV flag instead of display copy. Accepted as designed: the AV-backend qualification contract, and mid-run calibration arming (guard-railed, logged, operator-overridable). Known debt, deliberately deferred: retake routing still dispatches on QC note prose — a structured failure-kind enum on QCReport is the follow-up.
- Self-arming measured QC. At the end of a successful farm pass, when no calibration receipt exists and the operator has made no explicit `DIONY_MEASURED_QC_REQUIRED` choice, the conductor auto-calibrates from the vault's PASS-verdict 720 negatives once at least 12 exist — writing the same digest-protected receipt and announcing the armed gates as an activity line ("Measured picture QC armed: calibrated from N verified clips…"). An existing receipt is never overwritten, an explicit operator choice in either direction always wins, and any measurement problem defers arming to the next run rather than stalling anything. Blueprint 21 carries the self-arming behavior; the operator CLI remains for explicit control.
- Backlog closure: tiered locked-shot refinement, budget-aware repair, one-command pytest. Reference-locked Shots now accept DeepSeek refinement under tiered locks (none: subject/motion/scene/environment; character: motion+scene; character_and_background: motion only — supplied pixels keep the rest), with the refinement surviving the dispatch-time contract refresh; Blueprint 20 carries the tier table. The QC repair table gains compact fallbacks: an appending repair that would overflow the word budget on a full plan now degrades to its replacement layers plus negative terms instead of silently declining to a seed-only retake. `packages/contracts/tests/test_voice.py` is renamed `test_voice_contracts.py`, restoring the documented single-command pytest across all packages.
- Hero candidate takes and measured post-SR QC. Hero-class coverage (the character lock and dialogue beauty recipes; the commercial product lock — a new `Shot.hero` contract flag, never set on Samples) rolls two seed candidates on a live camera; once both land, AutoQC scores pick the print (verdict, then measured reference identity, temporal coherence, flicker, motion, with deterministic tie-breaks), retake decisions wait for the pair and apply to the selected print, and `DIONY_HERO_CANDIDATES=0` is the kill switch. Finish gains the Blueprint 06/19 post-SR gate (`diony-post-sr-v1`): a mid-clip finished frame must hold SSIM ≥ 0.75 against its own upscaled negative and the finished proxy may not carry more than 2.5× the negative's high-frequency temporal energy (grain boil); the measurement receipt lands beside the master, and a violation holds that take only while the rest of the finish queue keeps running — model/checkpoint failures remain terminal.
- Generated soundtrack cues (opt-in, open-weights, local). The soundtrack plan gains a second cue source: when a site exports `DIONY_AUDIOGEN=enabled` and the Bible's new `soundtrack_policy` field is `generated_ok`, DeepSeek may plan `source: "generate"` rows against the pinned local instruments — ThinkSound (Apache-2.0, video-conditioned Foley/ambience anchored to a shot's accepted negative), ACE-Step (Apache-2.0, score), Stable Audio Open (Stability Community License, textures) — with roles, prompts, durations, seeds, and a 20-cue budget validated at plan time. Rendering happens in the FINISH window through a new `diony-audiogen` command adapter (`audiogen_backend.py` + per-generator on-host CLI templates) that gates every cue on duration, energy, true peak, and a **no-speech** ceiling (the speech-presence detector run in reverse: CosyVoice remains the only speech instrument), writing a receipt with generator, prompt, seed, and sha256. Failed cues drop with a visible warning and never block delivery; provenance (`licensed` vs `generated`) is recorded throughout. NOTICE gains the three model licenses; Blueprint 19 carries the policy. Default behavior is unchanged: both switches off means licensed-library-or-dialogue only.
- Library-backed soundtrack and the 4-stem mix. A new `diony_producer.soundtrack` module scans an operator-provisioned licensed sound library (`DIONY_SOUND_LIBRARY`, default `{vault}/runtime/audio/library`, with `ambience/`/`foley/`/`score/` subfolders); at roll — while the brain is still resident, before the FARM drain — DeepSeek edit-lists cues against the rundown (roles must match the catalog, gains clamped to −36…+6 dB, durations clamped to real cue length, unknown ids rejected) and the plan persists as a digest-protected receipt recording every cue's sha256. Mix now builds separate dialogue/ambience/Foley/score rails from verified per-clip packages and plan-verified library bytes only, applies dialogue-priority sidechain ducking to ambience and score, crossfades dialogue clips over exactly the picture's junction blends instead of butt-cutting under a dissolve, and lands the program on the Bible loudness target through measured two-pass linear loudnorm (replacing the single-pass dynamic pass). A picture-only program with a planned licensed soundtrack is now deliverable; without a library or a plan, behavior is unchanged and the noise-only refusal stands. Blueprint 19 carries the stems contract.
- Evidence-driven retakes. On a QC FAIL, the full evidence bundle (failure notes, measured temporal/reference/continuity/AV values, the five typed layers, negative conditioning, and prior attempts' notes) goes to the DeepSeek retake director, which returns a multi-symptom directive — layer revisions plus up to four negative terms with a one-sentence rationale — applied through the plan validators and logged to the activity feed; the keyword repair table remains the offline fallback. Two structural fixes land with it: `refresh_reference_shot` now preserves a current, dispatch-valid plan when the refresh would not change the shot contract (repairs and directives were previously recompiled away right before dispatch, leaving retakes that differed only by seed), and retake seeds jump to a distant deterministic draw instead of `seed+19` adjacency. `diony_sr_v1` finish now refuses to run without OpenCV instead of silently degrading temporal SR to per-frame still SR (the Blueprint 06 "boiling grain" failure). Blueprint 20 carries the directive-first repair policy.
- DeepSeek planning that actually runs. The brain wire contract is fixed — `reasoning_effort` is a top-level request field (the SDK-style `extra_body` nesting never reached the server), sampling follows Blueprint 04 (`temperature 1.0`, `top_p 0.95`), output budgets are sized to the work (1.5k single / 6k batch), and every `vllm serve` command (wizard prep/fallback and profile cards) pins `--served-model-name DeepSeek-V4-Flash-0731` so the client model id resolves without an env override. Roll now runs `plan_production`: one health check, a bible pass that authors a project-specific logline and visual grammar (until the human Bible lock), draft batches of 8 shots per call, and a critique pass per productive batch auditing metadata leakage, camera/negative contradictions, temporal-beat motion, texture, and cross-shot repetition. Subject may now be sharpened on unlocked shots when every identity anchor survives verbatim; camera stays locked; all output still passes the deterministic validators. Every planning outcome — including brain-offline and per-batch fallbacks — is a visible production activity line; a production can no longer silently become template-only. Planning is bounded by a wall-clock budget (`DIONY_BRAIN_PLANNING_BUDGET`, default 180s, clamped 30–1800s) so a large rundown's Produce stays responsive — budget exhaustion is a visible activity line and unplanned shots keep their compiled plans. The desktop dry-run skips planning and soundtrack authoring silently: the laptop has no brain by design, and a warning on every Produce would read as a fault to a test user. Blueprints 04 and 20 carry the same contract.
- Calibrated measured picture QC, on by default. A new `diony_producer.calibration` module measures at least 12 verified-good 720p negatives (explicit paths or `--from-vault` PASS-verdict takes), derives the motion floor and morph/flicker ceilings with margin around what good clips actually measure (clamped to absolute guard rails), and writes a digest-protected receipt to `{vault}/.cluster/measured-qc-calibration.json`. With a valid receipt and no explicit `DIONY_MEASURED_QC_REQUIRED` choice, measured temporal QC is **required**: frozen/black plates, incoherent temporal change, and flicker are hard camera failures instead of computed-then-ignored evidence. Env thresholds still win as explicit operator overrides; tampered, stale-version, or out-of-bounds receipts never apply. The analysis proxy is upgraded to 320×180 @ 12 fps (`diony-temporal-v2`), and stored PASS receipts revalidate against the current measurement version. Blueprint 21 carries the calibration procedure.
- Deliverable live audio path. The local AV-sync check is now `diony-local-av-motion-sync-v2`: it measures at the clip's true frame rate, refines the audiovisual offset to sub-frame resolution with a parabolic fit around the correlation peak, and returns a **measured qualification** — evidence qualifies when both signals carry structure (enough comparable frames, active audio, an unambiguous interior peak) instead of being hardcoded unqualified, so verified voice packages, mix lock, and qualified visual retakes are reachable on a live signed site. Degenerate evidence (static plate, near-silence, edge-of-window peak) remains an unqualified one-take hold. Casting is now per character: synthesis and enrollment resolve their cloning prompt from the character's attached reference sample, then a `DIONY_COSYVOICE_PROMPT_LIBRARY` clip keyed by cast order, then the shared default — with prompt path/source/sha256 provenance in every response — and a new cast-distinct gate fails a script when two speaking characters measure pairwise speaker similarity above `DIONY_VOICE_CAST_DISTINCT_MAX` (default 0.90). Voice pipeline version is `diony-voice-performance-v4` so cached single-voice audio is never reused. Blueprint 22 carries the same policy.
- Frame-truth production lane. Shot planning now books lane-true durations — every take is at most the native Wan2.2 **81-frame** (4n+1, ~3.4 s) quality window, `wan_frame_num` rounds up within the cap so the camera never delivers fewer frames than booked, and the overlap stitch lands the length goal **exactly** (a 5m goal is 99 takes; 10m–15m cap at 120 with the existing note; >15m stays first reel). Program assembly conforms by **trim only**: the `setpts` time-stretch is removed, a real finish refuses a clip more than one frame short instead of silently retiming motion against the locked audio rail, and AutoQC gains a hard **Duration mismatch** gate (auto-retake eligible). Legacy Shots that booked 120 frames are reconciled in place to measured camera truth at QC, and 1080 finish lineage records measured output frames rather than the declared count. Blueprints 12 and 19 carry the lane-true wording in the same change.

- Producer Sample is now a real 5s/10s Idea action. It uses the current words and references, runs bounded Wan2.2 A14B camera takes through AutoQC, exact time-frame assembly, mandatory `diony_sr_v1` 1080 finish and mix, and enters Gallery only with the normal checksum-verified master lineage. The prior Night Press fixture shortcut is no longer wired to the Idea page.
- Subject-defining image references are now binding production locks: singular characters and plural character teams keep every Shot on Wan I2V, same-world shots hold one uninterrupted composition, the negative prompt reaches Wan's true `n_prompt` conditioning, five-point clip evidence is recorded (with a strict same-background floor), and I2V cannot silently downgrade to T2V. Adjacent locked Shots condition on the prior selected print's last frame, must pass boundary QC, and assemble through an eight-frame temporal blend while preserving requested Sample TRT. Picture conforms to the exact timeline frame count and the padded mix rail cannot trim locked picture frames. Retakes refresh the strict direction, and a landed-but-failed negative remains a visible QC hold instead of appearing to enter finish.
- Retake now repairs persisted pre-reference-detector Projects in place: the full Shot chain is promoted to I2V, reference keys, strict negatives, prior-frame continuity, and overlap before a new Take rolls. Binding I2V uses style-neutral technical negatives rather than the generic live-action `cartoon` exclusion, so illustrated references are not told to abandon their own medium. Finish rejects legacy or stale `PASS` receipts unless their engine, reference ids, five checkpoints, and prior-Take boundary evidence match the current Shot contract. Any retake invalidates and hides the old Gallery master until a newly verified finish succeeds; failed bytes remain in the vault for audit. Producer restart adoption resumes only currently selected missing Takes, never obsolete aborted/rejected rows.
- Gallery program playback, poster, and download URLs are now versioned by the verified program checksum. Re-finishing the same Project therefore displays the new master immediately instead of letting Safari replay the prior invalid video and thumbnail from cache.
- Gallery Details Copy Prompt now keeps one accessible overlapping-squares icon and copies synchronously from the visible prompt selection before any async browser boundary, fixing Safari on Mac while retaining the modern Clipboard API fallback for Windows and Mac. The modal shows visible success, or leaves the prompt selected with Command-C/Ctrl-C guidance if browser policy blocks both paths.
- Gallery is now an independent persistent library. It remains navigable when Producer reopens on a blank/new Idea, loads finished programs from the vault rather than the active Project, and shows explicit loading or failure status instead of disabling the floor.

### Changed

- The Production Shots ledger now expands through the unused desktop folio height, scrolls within that lane, and keeps bottom safe-area breathing room instead of stopping at a fixed `18rem` cap.
- Producer now has three pages: **Idea → Production → Gallery**. The non-working Edit page, its navigation/state, cutting-room DOM/JavaScript/CSS, Wizard promises, catalog entry, and prompt-to-edit API route were removed. Historical `ClipEdit` lineage remains readable so existing Project records are not corrupted. Producer UI stamp is `run60`; Studio UI stamp is `ready55`.

## 0.22.0 — 2026-08-20

### Added

- Wizard `ready31` replaces the undersized navigation and floating 3D notebook treatment with a 112px stage mast, 288px desktop cue rail, and integrated production-folio workspace; the exact B200 form and 390px mobile layout remain unclipped while Wizard objectives and site choices are unchanged.
- Repository-root `Build Diony AI.command` creates or reuses a deterministic native Mac development app, selects the stable `dist/desktop/{version}/development/Diony.app` path in Finder, and emits explicit non-publishable local proof without confusing it with Developer ID/notarization.
- Self-contained desktop release path for the future two-button webpage: notarized/stapled/Gatekeeper-verified Universal Mac DMG and Authenticode-signed/timestamped Windows installer, deterministic secret-free payload staging, and a stable manifest that refuses incomplete platform proof.
- Installed-app startup hardening: bundled service dispatch with no system Python or first-run `pip`, standard per-user state directories, immediate Desk feedback, semantic Wizard/Producer health and UI-stamp checks, safe unknown-port handling, persistent logs, and downloadable secret-free diagnostics. Blueprint 17 defines the acquisition, install, startup, trust, and publish gates.

- Launch control-plane contracts for users, workspaces, teams, departments, Cost Centers, governed rate cards, memberships, durable workflow runs, generation jobs, provider configuration, immutable usage/cost-allocation/audit records, render lineage, and replayable progress.
- Transactional local-first SQLite controller with WAL, strict workspace isolation, role and cost-admin gates, soft-delete support, fixed-point internal-credit and cost allocation, exact reconciliation, monitor-only budgets, scoped and organization rollups, metrics, and correlation IDs.
- Provider-neutral local registry preserving DeepSeek-V4-Flash-0731, Wan2.2 native 720p, assembly, mandatory `diony_sr_v1` finish, and creator-owned packaging. No hosted provider is registered by default.
- Retryable, resumable one-stage-at-a-time workflow orchestration with exponential backoff, cross-process dispatch leases, a `diony-control-worker`, signed-site engine checkpoint proof, explicit optional-stage skips, fallbacks, pause/cancel/retry controls, SSE progress, and actionable errors. Required stages stay `waiting_provider` until an engine proves output; no asset is invented.
- AES-256-GCM BYOK storage, optional AWS S3/Cloudflare R2 media adapter, and verifiable `.diony.zip` project export that excludes secrets while preserving project cost lineage and dimension snapshots.
- Workspace project inventory, team/membership administration, input/output safety checkpoints, JSON correlation logs, success/latency/cost-per-video-minute metrics, and confirmed project erasure across controller lineage, unshared vault media, exports, and configured object mirrors.
- Blueprint 16 defines the Open Source Content Producer philosophy, operational boundaries, API, failure modes, and proof layers. The repository license and Wizard/Producer objectives are unchanged.

## 0.21.0 — 2026-08-20

### Changed

- Desk, Wizard, and Producer now share the Diony Chorus creative system: Deep Void / Parchment / Diony Crimson / Stage Amber, Archivo Black display type, IBM Plex work and machine type, an original surface-specific stage spirit, a single crimson workflow ribbon, visible keyboard focus, responsive layouts, and reduced-motion fallbacks. Workflow gates, site truth, and Producer behavior are unchanged. Wizard UI stamp is `ready30`; Producer UI stamp is `run13`; Desk is `desk4`.
- Blueprint 15 is the source of truth for brand, motion, accessibility, surface density, and the originality boundary.

## 0.20.0 — 2026-08-19

### Changed

- Lease Wizard is **Site → Connect → App → Ready → Producer**. Ready is **Test GPU**: SSH over the Connect line, `nvidia-smi` must match the Site (B200 / B300 / Dual-Spark). **Pack red** means App did not pull (desk `DIONY_DRY_RUN=1`). **Pull pack** on Ready SSHes the same line. Pytest records the same rows and never opens a socket. A real IP requires a green Test GPU before Continue.
- Desk restarts Wizard when `:8787` is healthy but still serving an old HTML stamp (`app.js?v=`). Open Diony AI is enough to pick up UI. Hard-refresh `http://wizard.diony.local` once after restart.
- Wizard UI stamp is `ready29`. Producer UI stamp is `run12`. Floors are **Idea → Production → Edit → Gallery**. Produce opens Production immediately; open/roll continue in the background. If Wan is already farming, Produce does not start a second show. Production is a **live monitor** (tally, denoise step/40, elapsed/total clock, per-shot clips, camera-report activity — WAN / GPU / QC / SR — not uvicorn access logs). After Wan 720p, Production runs **1080 finish and mix** with no picture-lock or mix-lock click. Human gates stay bible and distribution. Edit and Gallery stay empty until a real clip exists. On a signed Dual-Spark / B300 / B200, desk Producer farms Wan **and** `diony_sr_v1` through the Connect SSH line onto GPU Producer. After a **pod restart** Producer prefers live Wizard Connect over a stale `site.json` IP/`-p`, drops a dead tunnel, health-checks GPU `:8788` (not `pgrep`), rsyncs Producer/Wan onto `/opt/diony-ai`, and adopts a live `generate.py` instead of auto-farming leftover takes. `generate.py` uses the host Python that has torch (`DIONY_PYTHON`), not the Diony FastAPI venv. Official `generate.py` still `import dashscope` at load — that is a host pip stub, not a Dashscope API call; keys stay empty and `--use_prompt_extend` is never passed. Farm proves `generate.py --help` on the farm thread, once, before N clips. Leased B200 / B300 farm with `--offload_model False` (upstream defaults True on one GPU). Spark keeps offload + dtype convert. Official Wan `flash_attention` asserts FlashAttention-2; lease host pip skips that wheel, so Diony applies Wan’s own SDPA path on `[B, L, N, C]` before FA2 flatten (SDPA after flatten is a `self.o` shape crash, not a clip QC fail). Activity never treats tqdm `Loading checkpoint` / `0/40` / the float32 `to()` warning as a clip QC fail. A QC fail is **failed**, not “next.” **Refresh** on Production (and **Start over** in the mast) posts `/api/studio/new` and aborts the farm-owned `generate.py`; it does not load Night Press. Wizard Sign + Test GPU is the pack lock — the laptop is not the camera and not the finish box. A signed lease with Test GPU is not a stub camera. Edit is optional coverage after the farm. Gallery is the clip inventory.
- Producer sheet uses IBM Plex Sans (same as Wizard) and fills the window.
- Length goal uses the Wan **5s quality lane**. Shot-count scales so stitched TRT is near 30s–5m. Do not lengthen a take past 5s. >15m is still first reel.
- Ready shows **Pack pull in progress** (moving bar + latest apply line + elapsed) while Hugging Face is on the GPU. Continue stays off until the pull finishes. Landing on Ready resumes the poll.
- **Refresh** on Site clears this Wizard and reloads the page (walk, apply log, Connect SSH user/port, local `site.json`). **Start over** in the mast does the same. Browser refresh does not. HF_TOKEN stays.
- App and Ready have **HF_TOKEN**. Open https://huggingface.co/settings/tokens, Create new token +, paste it. The token is not written to site.json or Wizard state.
- RunPod `Connection refused` on Ready means the pod is down or the TCP IP/`-p` changed. Connect stays editable after pass. Pull pack uses the Connect Host, not a stale inventory copy.
- Lease App Continue starts the pack pull and opens Ready. It does not wait on Hugging Face. A second Continue no longer deadlocks the Wizard.
- Lease App on RunPod: if `mount --bind /workspace /diony/vault` is denied, symlink `/workspace` so the product path stays `/diony/vault`. Python install uses `$DIONY_REPO/.venv`, then `pip --break-system-packages` (PEP 668). Apply skips `pip install -U pip` when `huggingface_hub` is already in the GPU venv (that listing was a false FAIL). Apply errors stay on App / Ready, not Connect.

## 0.19.0 — 2026-08-19

### Changed

- Wizard Site is four choices: **Local Quad-Spark Cluster**, **Leased Dual-Spark Cluster**, **Leased B300**, **Leased B200**. Three leases stay separate. Local is never pre-selected. `lease-gpu-v1` is still not a Wizard Site.
- Runpod → Wizard Connect map for B200 and B300: the Connect page lists the RunPod Connect headings in order (**HTTP services** skip, **SSH** skip, **SSH over exposed TCP** use, **Web terminal** skip, **Direct TCP ports** same). Host / SSH user / SSH port come from `ssh root@IP -p PORT`. Vault is Details → Pod volume Mount path `/workspace`. Blueprint 14.
- Leased B200 (`b200-v1`) is TP=1, 180 GB, `max_model_len 16384`, `gpu-memory-utilization 0.95`. Not a drop-in B300. Do not paste `1048576`.
- START HERE names hardware first: local crate, or a lease from Runpod (`https://www.runpod.io/`) then Enverge (`https://enverge.ai/`). Dual-Spark still uses a shared Network Volume. B200/B300 skip Network Volume unless it is already in that GPU’s data center.
- Connect SSH user: GPU login (word before `@` on RunPod Connect). Prefer the TCP line (`root`). Wizard refuses a pasted `ssh user@host -p PORT` line. Optional SSH port on B200/B300 (`DIONY_SSH_PORT`) for rsync.
- Open Producer and Continue use the product URLs `http://wizard.diony.local` and `http://producer.diony.local` on every site. Day 2 still binds `:8787` / `:8788`. Open Diony AI runs `python -m diony_wizard.gateway ensure` after Wizard/Producer start (hosts + `:80` Host router). First-open and Continue open those names, not `127.0.0.1:8787`. The Mac password helper runs `gateway install` in the foreground (it detaches `serve`); it does not `nohup` inside AppleScript. If names are not live, the lobby stays up (`need_names`) instead of opening a URL that does not resolve.

## 0.18.0 — 2026-08-18

### Added

- First open without a terminal: **START HERE.txt**, then double-click **Open Diony AI**. A lobby page lays `.venv` once and starts Wizard + Producer. Continue is Wizard.

## 0.17.0 — 2026-08-18

### Changed

- Wizard 0.17. Sign writes `site.json` with vault mount `/diony/vault` and copies it onto the GPU vault when live. App Continue mounts the recorded NFS share. Local How installs `diony-finish`. Open Producer uses host:8788 when live (laptop dry-run stays `127.0.0.1:8788`). Retry pack on a failed App apply.

## 0.16.0 — 2026-08-18

### Added

- Wizard Dual-Spark / B300 App Continue is live apply: SSH BatchMode (or local on spark-01), Hugging Face pack pull (DeepSeek, Wan A14B, `diony_sr_v1`), clone `/opt/wan22`, install `diony-finish`, start Producer. Pytest stays `DIONY_DRY_RUN=1` and never SSHes.
- `diony-finish` (`apps/finish`, :8020): tiled Real-ESRGAN x2 temporal SR → zscale 1920×1080 Rec.709 legal. Picture lock writes `takes/{id}/finish_1080.mov` and `program/{timeline}/master_1080.mov`. Mix lock refuses without those masters when finish is live. Never lanczos as a master.
- Producer Idea **length goal**: 30s, 1m, 3m, 5m, 10m, 15m, 20m, 30m, 40m, 1hr. Not a stopwatch. >15m is first reel.

### Changed

- Dual-Spark App is not a printed homework sheet. How is the log of what Wizard runs. Sequential SKU still starts Producer only (Produce starts Wan; picture lock starts finish).
- Producer 0.8. Idea length goal picker. Picture lock on a live site without `diony_sr_v1` is refused.

## 0.15.0 — 2026-08-18

### Added

- Live Wan2.2 camera: `diony-wan` wraps official `generate.py` (`1280*720`, Spark offload). Never Dashscope.
- Producer 0.6 FARM: after Wizard pack on Dual-Spark, Produce expands on DeepSeek if vLLM is up, drains brain, then writes 720p mp4 negatives into the vault. Retake uses the same path. Cap 4. Picture lock still refuses.
- Laptop without `/models/wan` stays stub so pytest does not call GPU.

### Changed

- Dual-Spark / B300 App starts `diony-wan`, clones Wan2.2 to `/opt/wan22`, installs `requirements.txt`, sets `PYTHONPATH`, `DIONY_PYTHON`, `DIONY_WAN_URL`, and `DIONY_DRAIN_BRAIN`.

## 0.14.0 — 2026-08-18

### Added

- Wizard Sign writes `{vault}/.cluster/site.json` and `mode.lock`. Producer and Conductor read the same lock.
- Producer 0.5 is Dual-Spark usable: `GET /api/site`, persist `projects/{id}/project.json`, dry-run **Retake** (cap 4), sequential SPLIT copy, `DIONY_BIND` / `DIONY_VAULT` on spark-01.
- Conductor refuses concurrent brain+Wan when the signed site is Dual-Spark or B300.

### Changed

- Dual-Spark and B300 App start Producer with `DIONY_VAULT=/diony/vault` and `DIONY_BIND=0.0.0.0`. Product URL stays `http://producer.diony.local`.
- New projects inherit `ClusterSite` from the site lock. Do not quote the Quad-Spark 2–5 day SLA on this pair.
- Live Wan / vLLM remain Week 2+. Day 2 still dry-run negatives.

## 0.13.0 — 2026-08-18

### Added

- Wizard Site **Leased Dual-Spark Cluster** (`dual` → `spark-2x-v1`): two GB10, Spark TP=2 brain, sequential SPLIT, CX-7 RoCE between the pair. Named human required. Not a shrink of Quad-Spark. Not B300. Not `lease-gpu-v1`.
- Profile `infra/profiles/spark-2x-v1.yaml`, serving notes, compose `brain-spark-2x.yml` / `split-spark-2x.yml`, interconnect `infra/switch/spark-2x-interconnect.md`
- `ComputePath.DUAL` on `ClusterSite`

### Changed

- Site labels: **Local Quad-Spark Cluster**, **Leased Dual-Spark Cluster**, **Leased B300**
- Lease rails are Site → Connect → App → Producer. App holds the pinned pack and Producer start. Desk records the pack on Producer. Models is not a Wizard page
- Do not quote the `spark-4x-v1` 2–5 day SLA on Dual-Spark or B300

## 0.11.3 — 2026-08-18

### Changed

- Connect Vault now teaches what it is, what to type, and where to find it (provider Storage / NFS). Notes show the fstab example. Desk Vault names the NAS admin UI and 10GbE address

## 0.11.2 — 2026-08-18

### Changed

- Producer and Wizard use product addresses: `http://producer.diony.local` and `http://wizard.diony.local`. Same URL on the laptop and on the GPU. Day 2 still binds `:8788` / `:8787`

## 0.11.1 — 2026-08-18

### Changed

- Wizard lines use choice buttons instead of empty “Choose” dropdowns. Site no longer stacks a Compare panel on top of the live hint
- Models auto-opens pull commands. Connect is host + vault against a short profile card. Producer is a floors card + URL + Sign. The signed report is a lock sheet, not a JSON dump

## 0.11.0 — 2026-08-18

### Changed

- Wizard Models is the pinned site pack (DeepSeek-V4-Flash-0731 + Wan2.2 A14B). Continue records it. No model shopping, no Hugging Face `latest` tags. TI2V-5B stays previz and is not in the pack
- Brain is no longer a Wizard page. The pack auto-records BRAIN (`spark-4x-v1` TP=4 on the desk, `b300-v1` TP=1 on Leased B300)
- Leased B300 Wizard is **Site → Connect → Models → App → Producer**. Local crate is ten lines (Site through Producer, no Brain line)

## 0.10.0 — 2026-08-18

### Changed

- Leased B300 Wizard is **Site → Connect → Models → Brain → App → Producer**. App installs and starts `diony-producer`. Producer confirms Idea / Dailies / Program / Lock, then Sign
- Producer adds **Program**: overlap stitch (8–16 frames), frame-to-clip on I2V, 16:9 screening. Dailies prompt-to-edit (optional in/out) stays on the take. Produce rolls and stitches so the cut is ready

## 0.9.0 — 2026-08-18

### Changed

- Leased B300 Wizard is **Site → Connect → Models → Brain → Producer**. Connect no longer auto-fills weights, BRAIN, or the Producer URL. Models is the DeepSeek + Wan pull. Brain is `b300-v1` TP=1. Sign still only on Producer, then open the showrunner

## 0.8.1 — 2026-08-18

### Changed

- Wizard Site is two choices: **Local DGX Cluster** or **Leased B300**. Four-node leased GPU is not a Wizard Site
- **Continue** on every line except the last. The clapboard **Sign** is only Ready / Producer

## 0.8.0 — 2026-08-18

### Changed

- Leased GPU and Leased B300 Wizard is **Site → Connect → Ready**. Connect is hosts + vault (defaults filled). Interconnect, matched image, weight pull, BRAIN, and Producer URL are recorded from the signed Site — not quizzes. Ready shows the plan and locks the pack
- Signing Site is the named-human authorization. The extra “Authorize this lease?” field is gone

## 0.7.1 — 2026-08-18

### Changed

- Single B300 is labeled **Leased B300** and uses the same six-line software rail as Leased GPU (Site → Nodes → Vault → Weights → Brain → Producer). No Unbox, Cables, Switch, Sync, or Fabric. Still `b300-v1` / TP=1 — not a four-node `4×B300` lease

## 0.7.0 — 2026-08-18

### Changed

- Leased GPU Wizard is a **software rail**: Site → Nodes → Vault → Weights → Brain → Producer. No Unbox, Cables, Switch, Sync, or Fabric. Provider already owns the box; Wizard records hosts, vault, weights, serving, and the Producer URL
- Until Site is signed, the menu is only Site. Desk still expands to the eleven-line crate sequence

## 0.6.0 — 2026-08-18

### Added

- Wizard Site third path: single B300 (`b300-v1`). Same rail and Producer as the desk crate. Named human required. TP=1 serving; SPLIT is sequential, not 2+2 Sparks
- Cluster profile `b300-v1`, on-package interconnect `infra/switch/b300-node.md`, compose stubs `brain-b300.yml` / `split-b300.yml`
- Per-site serving notes and Wizard **site pack** (profile + fstab + manifest + serving + Producer URL) so lease and B300 prep stay inside Diony

### Changed

- Lease Site is complete for Day 2: Files expose profile, serving notes, and pack; GPU class list includes 4×B300 (four-node lease, not the single-B300 Site)
- `ComputePath` adds `b300`. Blueprint 14 covers three sites

## 0.5.0 — 2026-08-17

### Added

- Wizard Site (`path`): local 4× Spark crate **or** leased GPU. Same rail, same Producer, same 1080 mandate. Lease is never the default and requires a named human exception
- Cluster profile `lease-gpu-v1` and interconnect policy `infra/switch/lease-interconnect.md`
- `ClusterSite` / `ComputePath` on `Project.compute`
- Blueprint 14 (compute paths). Site is Wizard line 01

### Changed

- Blueprints 00, 01, 08, 11: never cloud-by-default still holds; lease is a chosen site, not failover or Dashscope

## 0.4.0 — 2026-08-17

### Added

- Wizard crate-to-system preparation: Unbox as line 01 (before power), signed report, switch snippets, health dry-run, weight manifest and vault fstab in the report
- Blueprint 01/11: ten-line sequence starting at Unbox

## 0.3.0 — 2026-08-17

### Added

- Multimodal Brief: idea refs as image, video, or document (`Asset`, cutting bench in Producer)
- Clip edits: feedback on a take becomes a child version via Wan VACE Fun A14B — parent negative is never overwritten
- Version ladder and vaulted clip artifacts (720 negative / 1080 finish / proxy)
- Blueprint 13 (idea refs, mark-the-print edits, immutable versions)

## 0.2.0 — 2026-08-17

### Added

- Wizard camera school: learn + do workbook, check questions, interactive port-2 loom (Blueprint 11)
- Producer Idea-to-Studio: customer stamps, cast-from-idea, bible / rundown / dailies / lock bays (Blueprint 12)
- `diony_contracts.studio` playbooks for feature, series, commercial, music video, education, social

## 0.1.0 — 2026-08-17

### Added

- Product constitution: proprietary LICENSE, NOTICE (Wan Apache 2.0, DeepSeek MIT), README, AGENTS.md
- Blueprints 00–10 (crate-to-cluster through quality mandate)
- Infrastructure-as-Product profile `spark-4x-v1` (compose stubs, MikroTik 200G snippets, model manifest, health probe)
- `diony-contracts` (Project/Bible/Shot/Take/QC/Timeline/MixLock/Deliverable)
- Wizard dry-run (`diony-wizard` :8787)
- Producer showrunner shell (`diony-producer` :8788)
- Conductor stub (`diony-conductor` :8789)
- Cursor rules and skills for quality, cluster ops, and Blueprints
