# Blueprint 19 — Producer Final Production Standard

Status: definitive Producer completion specification

Owner: Diony AI

Applies to: Producer, Conductor, contracts, Wan FARM, Diony Finish, audio/mix,
launch control plane, portable export

Depends on: Blueprints 05, 06, 07, 10, 12, 13, 14, 15, 16, and 18

## Product decision

Diony AI Producer is the primary professional workspace after either Studio
entry path. Its single job is to turn an idea or an existing project into a
creator-owned, fully attributable, studio-quality 1920×1080 program.

The product promise is exact:

> **Own Your Studio, Produce at Cost**

Producer does not expose the internal pipeline as a collection of unrelated AI
tools. It exposes three stable working floors—**Idea, Production, Gallery**—
backed by a complete, inspectable production state machine. Script,
rundown, generation, QC, finish, assembly, and mix are real stages inside the
run; they do not become extra top-level tabs.

The quality rule is also exact:

> Wan2.2 A14B creates 1280×720 camera negatives. Diony Finish creates the
> mandatory 1920×1080 Rec.709 master. A negative, proxy, title card, or previz
> can never be represented or exported as the final program.

When quality and schedule conflict, slip the clock. When a take fails hard QC,
retake it. When a human gate is open, stop.

Quality-preserving performance changes, benchmark receipts, canaries, and
automatic rollback follow Blueprint 21. No speed flag can weaken this standard.

## 1. The complete production loop

```text
Idea / script / import / existing project
  → normalized Brief
  → DeepSeek script and production Bible
  → HUMAN: Bible lock
  → scene and 5-second quality-lane Shot rundown
  → character / wardrobe / environment / style locks
  → prompt expansion and engine routing
  → Wan2.2 A14B 1280×720 negatives
  → AutoQC
       hard fail → child Take / retake, maximum four
       pass      → selected print
  → overlap assembly and continuity conform
  → diony_sr_v1 temporal 720→1080 finish
  → post-finish QC and Rec.709 legalizer
  → dialogue / score / SFX stems and loudness mix
  → 1920×1080 program master and portable package
  → HUMAN: Distribution
```

Every arrow is a persisted transition. A browser refresh, Producer restart,
worker restart, or dropped network connection resumes from the last durable
checkpoint. Completed stages are idempotent and are not repeated merely because
the UI lost contact.

### Stage ownership

| Stage | System owner | Human control | Durable output |
|---|---|---|---|
| Intake | Producer | choose entry mode, length, references | `Brief` + ingested `Asset[]` |
| Script | DeepSeek on signed site | tone, structure, notes, version choice | script version + scene plan |
| Bible | DeepSeek + Producer | lock story, identity, wardrobe, grammar | locked `Bible` |
| Breakdown | Conductor | reorder/edit shots before roll | `Shot[]` rundown |
| Camera | Wan2.2 | pause, retake, replace selected print | immutable 720 `Take` versions |
| QC | AutoQC + Producer | inspect fail, guide retake | `QCReport` per Take |
| Assembly | Producer | pacing and selected prints | versioned `Timeline` |
| Finish | `diony_sr_v1` | monitor and recover | 1080 take masters + program master |
| Mix | audio rails | notes and target selection | stems + measured `MixLock` |
| Delivery | Producer | Distribution gate and package choice | checksummed deliverables/export |

## 2. Stable information architecture

### Idea — define the show

Idea accepts four entry modes without splitting the product:

1. **New idea** — plain-language concept plus target program length.
2. **Paste script** — script text becomes an intake asset; the original is
   retained unchanged and a normalized script version is derived from it.
3. **Import** — `.diony.zip`, supported text/document assets, stills, clips, and
   reference material enter through validation and checksum boundaries.
4. **Continue project** — restores the last durable state and names the next
   incomplete or blocked action.

The core surface remains one generous writing field. References land on the
bench. Advanced Brief controls open in a subordinate sheet and include:

- format/customer kind;
- target length (goal, not wall-clock);
- audience and distribution intent;
- tone and content boundaries;
- 23.976 or 24.000 fps;
- broadcast −24 LUFS or streaming −16 LUFS;
- required characters, locations, dialogue, and supplied assets.

The Idea floor always displays `Final standard: 1920×1080 · 16:9 · Rec.709`.
Aspect variants are derived reframes after the 16:9 master, never alternate
low-resolution masters.

Primary action: `Produce`.

Before Produce, the confirmation reads:

`Lock the Bible and start the quality run. Wan makes 720p negatives; Diony
Finish makes the 1080 master.`

### Production — watch and recover the run

Production is a camera report, not a shot picker. It contains:

- title, current action, elapsed/total clock, and bounded progress;
- live clip tally with 720-negative truth;
- ordered pipeline with waiting, rolling, passed, failed, and blocked states;
- the **Master Rail**, a persistent technical truth strip;
- concise activity journal with GPU, provider, model, attempt, and recovery;
- cost attribution summary for the active member and Cost Center;
- `Pause`, `Resume`, and `Retry failed stage` only when valid.

Production never reports 100% while a required checkpoint, 1080 validation,
mix measurement, checksum, or Distribution gate remains unresolved.

### Gallery — verify, own, and distribute

Gallery contains finished programs and explicitly time-framed 5–10s Samples.
Coverage clips remain inside Production lineage. A Sample is a full Project run with a Brief,
camera receipts, AutoQC, 1080 finish, measured mix, and master checksum; it is
not a loose Take promoted into Gallery.
Each Gallery item shows:

- `1080 MASTER` status, 1920×1080, fps, Rec.709, codec, duration, bytes;
- measured LUFS and true peak;
- program and package SHA-256;
- originating Project, Timeline, selected Take versions, models, and site;
- Profile Name and Cost Center attribution snapshot;
- `Play master`, `Export production package`, and `Distribution` actions.

The Distribution gate cannot close until the master readiness receipt passes.

## 3. Golden-path quality configuration

The default is **Quality**, not speed. Producer may expose a labeled `Previz`
mode for blocking, but it cannot silently change a deliverable run to previz.

### Brain and narrative

- Model: pinned DeepSeek-V4-Flash-0731 on the signed site.
- Encoding: upstream `encoding_dsv4`; do not substitute a Jinja chat template.
- Context: Bible, active script version, scene ledger, character ledger,
  continuity summaries, human notes, and referenced asset metadata.
- Output: typed contracts, never an unparsed chat transcript as production state.
- Script versions are immutable children. Notes identify the version they amend.
- For a one-hour program, generate and lock the long-form rundown in BRAIN mode;
  do not pretend twelve five-second shots represent an hour.

### Shot construction

Every dispatchable Shot has:

- scene, slug, exact frames, 23.976/24.000 fps;
- focal length, T-stop, lighting ratio, camera move;
- positive and negative prompts;
- stable seed and explicit retake seed decision;
- character reference IDs and locked still/keyframe IDs;
- `reference_lock`, continuity handoff mode, and 8–16 frame overlap intent;
- engine and `finish=deliverable` or visibly labeled `previz`.

Prompt construction order:

1. subject identity and locked physical facts;
2. action and emotional beat with pace, direction, and body mechanics;
3. lens, framing, camera height, one compatible move, and duration;
4. environment, lighting ratio, color grammar, texture, and atmosphere;
5. first/last-frame or reference conditioning;
6. negative-conditioning input; the native host overlay exposes Wan's existing `n_prompt`, retains the pinned defaults, and appends Shot exclusions;
7. exclusions for identity drift, morphology, flicker, text, and unwanted cuts.

The persisted `WanPromptPlan` and 80–140 word dispatch budget are defined in
Blueprint 20. Subject and camera are program-owned locks; on-site DeepSeek may
enrich Motion, Aesthetic/Lighting, and Environment Dynamics only when the typed
candidate passes every dispatch check.

Prompt expansion may clarify production facts but may not change locked identity,
story outcome, safety decision, human note, or supplied reference ownership.

### Existing model routing

| Intent | Required default | Rule |
|---|---|---|
| cinematic establishing / no identity lock | Wan2.2 T2V-A14B | 1280×720 quality lane |
| character, wardrobe, or environment lock | Wan2.2 I2V-A14B | prefer locked still over hope-based T2V |
| talking or singing | Wan2.2 S2V-14B | voice conditioning and ≤40 ms lipsync QC |
| motion/identity transfer | Wan2.2 Animate-14B | preserve source identity and body geometry |
| fast blocking | TI2V-5B | `previz` only; never master input without A14B replacement |
| final picture | `diony_sr_v1` | temporal SR, legalize, grade, conform |

Camera duration stays on the approximately five-second A14B quality lane. Longer
programs gain shots/reels; they do not stretch generation duration to fake TRT.

### Consistency locks

- Character lock: canonical stills, face/body facts, age, hair, wardrobe version,
  and prohibited drift.
- Environment lock: geography, entrances, screen direction, weather, time,
  practical sources, and spatial anchors.
- Style lock: lens family, contrast, palette, grain, aspect, camera behavior,
  and motion cadence.
- Continuity ledger: selected Take, last usable frame, props, eyelines, wardrobe,
  light direction, and story state per scene.
- A Shot with a locked recurring character defaults to I2V/Animate. T2V requires
  an explicit reason stored on the Shot/run.
- A subject-defining still never silently degrades from I2V to T2V. The first Shot
  uses the canonical staged still; each following continuity Shot uses the prior
  selected print's last frame while retaining the canonical reference in QC lineage.
- Prompts animate the supplied subject directly. A presenter, gallery wall, framed
  picture, screen-within-screen, or rendering-medium substitution is a hard mismatch.

## 4. Strict 1080 production standard

### Master Rail contract

Production displays one unambiguous technical strip:

```text
MASTER STANDARD / 1920×1080 / 16:9 / 23.976 or 24.000
NEGATIVE  Wan2.2 A14B · 1280×720 · 5s quality lane
PICTURE   diony_sr_v1 · temporal SR · Rec.709 legal 16–235 / 16–240
SOUND     −24 LUFS broadcast or −16 LUFS streaming · −1 dBTP
STATUS    PLANNING | NEGATIVES | QC HOLD | FINISHING | MIXING | MASTER READY
```

The data comes from the Project and run, not hard-coded success copy. The Rail
includes blocker strings when readiness is false.

### Enforcement

- `Bible.width == 1920` and `Bible.height == 1080` for all deliverable Projects.
- Final aspect is 16:9. Controlled crops/reframes are derivatives linked to the
  16:9 master.
- Wan output is recorded as 1280×720 `camera_negative` and labeled `720p
  negative` everywhere.
- A failed hard QC Take cannot enter finish, even if manually selected.
- `diony_sr_v1` output must be 1920×1080, at project fps, even-frame conformed,
  Rec.709 legal, non-empty, and checksummed.
- Naive Lanczos may create a labeled proxy; it can never satisfy a deliverable
  receipt.
- Picture assembly consumes only selected 1080 Take masters.
- Mix lock requires the 1080 program path, at least one versioned audio stem,
  measured loudness/peak within the Bible target, and a verified audio stream in
  the program master when live. Missing evidence keeps Mix visibly blocked and
  reopens any stale or falsely closed live Mix gate.
- A Gallery card requires a real playable `.mov` or `.mp4`, not a stub, text
  artifact, missing path, or rundown.
- A Gallery Sample requires `production_kind=sample`, a requested duration from
  5 through 10 seconds, and the same complete master receipt as a program.
- The Gallery master and preview require a complete, checksum-verified lineage in
  the current unique Project namespace: prompt/reference → 720 negative → selected
  1080 finish → picture master → mixed program master. Filename coincidence is not
  evidence, and global `takes/{id}` media is never adopted.
- A newly stitched or newly mixed program master is a new Gallery version and
  clears any recoverable removal marker left on the superseded program bytes.
- Professional export kinds (`ProRes HQ`, `DNxHR HQ`) require
  `finish=deliverable`, verified 1920×1080 media, and checksum.
- If validation is unknown, status is `Verification needed`, never `Master`.

### Preview labeling

| Material | Required visible label | May distribute? |
|---|---|---|
| TI2V-5B blocking | `PREVIZ · NOT FINAL` | no |
| Wan A14B output | `720p CAMERA NEGATIVE · NOT FINAL` | no |
| editor proxy | `PROXY · NOT MASTER` | no |
| Diony finished Take | `1080 FINISHED TAKE` | only as review media |
| finished program + mix receipt | `1080 MASTER READY` | after Distribution gate |

There is no final-resolution dropdown. Resolution is a production contract.

## 5. Quality gates and automatic checks

### Before camera dispatch

- Bible is locked by a human.
- Shot contract is complete and engine is available on the signed site.
- Required reference bytes exist and checksums match.
- Reference-locked clips record first, quarter, middle, three-quarter, and last-frame evidence; a same-background lock fails if any sampled SSIM is below `0.75`.
- Cost Center resolves and is assigned to the acting membership.
- No hard budget gate exists; threshold warnings are acknowledged and recorded.
- Fleet transition completed drain → unload → load → healthgate.

### 720 negative QC

Hard fail and block finish on:

- identity or wardrobe drift from locked reference;
- morphing face/hands/body, extra limbs, broken geometry;
- temporal flicker or unstable texture beyond threshold;
- duration mismatch greater than one frame;
- missing/corrupt media or wrong native size;
- discontinuous first/last frame where a handoff is required;
- absent parent-Take lineage or a declared overlap rendered as a hard concat;
- lipsync offset greater than 40 ms for speech/song;
- safety or rights failure.

Warn without silently accepting on:

- minor grade mismatch;
- non-critical background discontinuity;
- missing optional picture-only audio bed.

Warnings remain visible and must have an explicit policy result before final
readiness. Four failed camera attempts stop on `Human review needed` with the
failed checks, stable references, and recommended repair route.

### Post-finish QC

- 1920×1080 exact raster and project cadence;
- no illegal stretch, 1920×1088 leak, or odd conform;
- Rec.709 luma/chroma legal;
- no new SR identity drift, edge ringing, temporal boil, or grain shimmer;
- frame count, handles, and timeline duration reconcile;
- decoded media is playable and checksum is stored.

### Audio parity

- dialogue intelligibility precedes score impact;
- required scripted dialogue locks an immutable Project-scoped VoiceProfile,
  profile-aware PerformanceScript, processed clean dialogue, final clip mix,
  timing/performance metadata, seeds, backend versions, and QC before picture;
- voice identity, multi-emotion reference embeddings, prosody, script hash,
  pronunciation, subtext, overlap, and interruption intent remain versioned inputs;
- generated speaker similarity is at least 0.92, prior-Project consistency at
  least 0.90, SNR at least 30 dB, and harshness/sibilance at most 0.25;
- Wan S2V consumes the locked processed audio. Lip confidence is at least 0.90
  and p95 dialogue offset is within 40 ms; facial and upper-body performance
  must pass the same timed performance receipt;
- dialogue, score, and SFX are separate stems when present;
- a picture-only request does not invent dialogue, route a template beat to
  S2V, or synthesize noise merely to populate a soundtrack; without authored
  dialogue or verified content-bearing audio, Mix remains open and blocks the
  deliverable;
- room tone, white noise, pink noise, test tones, and silence never satisfy
  dialogue or finished-soundtrack evidence;
- final Mix assembles verified per-clip VoiceClipPackages and never invokes synthesis;
- broadcast target is −24 LUFS, streaming target is −16 LUFS;
- true peak is −1 dBTP;
- missing required dialogue, clipped stems, silent channels, phase faults, or
  measured loudness outside tolerance block master readiness.

## 6. Reliability and recovery

### Durable execution

- Every stage has queued, running, waiting-provider, retry-wait, failed,
  canceled, and succeeded semantics where applicable.
- Each provider dispatch has an idempotency key, attempt, max attempts, next
  attempt time, input lineage, and output asset IDs.
- Required engine work remains `waiting_provider` until a signed-site checkpoint
  proves completion. Producer never fabricates media in the control plane.
- Retry schedule is 5s, 10s, 20s, then exponential up to 300s unless a
  provider-specific safe policy is stricter.
- A retry never overwrites a Take or deliverable. It creates a new attempt or
  resumes an incomplete idempotent checkpoint.
- AutoQC reference-lock or shot-boundary failure rejects the Take and
  automatically queues a newly seeded Take for the same Shot before later
  Shots. It does not fail the Production run while fewer than four generation
  Takes exist. Producer restart resumes that selected retry from the vault.
- After four failed generation Takes, stop that continuity chain for human
  review. Preserve all completed Shots and every rejected print; do not spend
  the four-take quality budget on infrastructure or provider failures.

### Actionable recovery table

| Condition | User truth | Actions |
|---|---|---|
| network interruption | `The line dropped. The run may still be rolling.` | reconnect and replay events; do not re-dispatch |
| Producer restart | `Restoring the last production checkpoint.` | resume from SQLite/vault |
| GPU pod address changed | `The signed GPU moved.` | Studio Connect, save new SSH line, retry healthgate |
| engine unavailable | name exact engine/model/site | repair site or pause; no hidden hosted fallback |
| Wan hard failure | concise classified cause and Shot | retry, revise prompt, replace ref, or stop |
| QC identity/style drift | failed dimensions and comparisons | locked still, I2V/Animate, guided retake |
| four-take cap | `Human review needed after four takes.` | choose best print, revise Shot/Bible, or abandon Shot |
| finish failure | failing Take/master and reason | retry finish from retained 720 negative |
| mix failure | measured/missing stem facts | rebounce mix without rerunning picture |
| checksum mismatch | `The master did not verify.` | rebuild package from vault originals |
| optional object mirror down | local master remains authoritative | retry mirror; local export stays available |
| threshold crossed | exact Cost Center warning | continue production; budgets never become a paywall |
| unrated usage | `Cost not rated yet` | retain usage; apply versioned rate later |

### Cancellation and partial completion

Production exposes one **STOP** control while a run can still dispatch Camera,
Finish, or Mix work. STOP first writes a durable `ProductionStopReceipt`, then
cancels the current Wan job and every future Camera/Finish/Mix dispatch. A
stopped Project cannot resume because the browser refreshed or Producer
restarted. Starting a new Idea is the explicit path to another run.

An explicit **recalibrated restart** is the only same-Project exception. It is
available only after a durable STOP receipt exists. `POST
/api/production/restart` advances the Project version, nests the prior STOP
receipt in a `ProductionRestartReceipt`, preserves every old immutable Take and
retained incomplete result, recompiles every Shot through the current
script/reference/prompt contract, and creates a newly selected generation Take
for every Shot. It never reuses an old selected Take or its output as if it were
generated by the new contract. Bible lock remains human-owned; picture, mix,
and distribution reopen because the replacement cut has no master.

STOP opens a modal with exactly two completed-clip choices:

- **Keep** retains only checksum-valid, QC-passed completed camera negatives,
  removes unfinished partial writes, and overlap-stitches the retained clips in
  rundown order as `incomplete_720.mov`. Production labels this
  `INCOMPLETE 720P RESULT · NOT MASTER`, exposes playback/download from the
  Project vault, and never promotes it to Gallery, picture lock, Mix,
  Distribution, or a 1080 deliverable. If no clip completed, the stop receipt is
  still durable and no result file is invented.
- **Delete** stops the same work, then removes generated Take and Program media
  inside that exact Project namespace. It retains the Brief, script/Bible, Shot
  and Take metadata, prompts, notes, stop time, completed-Take ids, and deleted
  file count as audit evidence. Source references and unrelated Projects or
  Gallery compositions are never deleted.

The completed-Take inventory is captured before cancellation cleanup. Repeating
the same STOP request is idempotent; changing Keep to Delete or Delete to Keep
after the receipt exists is refused. A failed incomplete stitch never resumes
generation: Production remains stopped and reports the stitch error.

## 7. Transparency, ownership, and Studio context

Every generation and final receipt identifies:

- Studio, Project, acting membership, Profile Name snapshot;
- Team, Department, Cost Center, and monitoring state;
- site/profile and fleet mode;
- provider, exact model, engine, parameters, seed, prompt, negative prompt;
- reference/checksum lineage and parent Take;
- started/completed times, attempt, measured use, internal credits, fixed-point
  cost or visible unrated state;
- QC reports, human notes, selected print, Timeline, finish, mix, and package.

Creators see their assigned cost scope. Department managers see managed scope.
Only the configured Cost/Budgetary Admin sees organization totals and changes
member Cost Center assignments. Crossing a threshold never blocks Produce,
Retake, Finish, export, or Distribution.

Concurrent member edits use optimistic version checks. A stale write returns the
newer version and offers `Review changes`; it never silently overwrites another
maker's script, Shot, selected Take, or Timeline.

## 8. Local and leased site behavior

The Producer UI and quality contract are identical for Local Quad-Spark, Leased
Dual-Spark, Leased B300, and Leased B200.

- Local Quad-Spark may use parallel SPLIT and has the documented 2–5 day
  one-hour SLA.
- Dual-Spark, B300, and B200 are sequential: one engine at a time.
- Desk is the UI for a signed leased site. Wan and `diony_sr_v1` run on the GPU
  Producer over the current Studio Connect line.
- The desk laptop never decides that a leased finish pack is absent by checking
  its own `/models` directory.
- No site may silently route frames, scripts, prompts, or weights to an internet
  generation service. Explicitly configured non-frame providers remain subject
  to Blueprint 16 policy and disclosure.

User-facing ETA language names the selected site and current queue truth. It
does not promise real-time generation or reuse the Quad-Spark SLA for other
profiles.

## 9. Contracts and API completion

### Contracts

Extend existing typed objects; do not create a parallel project universe.

| Contract | Completion requirement |
|---|---|
| `Brief` | entry mode, source asset/version, audience, length, distribution intent |
| `Bible` | locked 1920×1080/fps/color/audio standard and continuity locks |
| `Shot` | complete camera grammar, refs, overlap, engine reason, finish intent |
| `Take` | immutable version/parent, 720 and 1080 artifacts, QC, lineage |
| `QCReport` | pre/post-finish checks, thresholds, evidence, actionable repair |
| `Timeline` | selected Take versions, handles, overlap, duration reconciliation |
| `MixLock` | stems, measured LUFS/peak, picture reference, validation result |
| `Deliverable` | raster/fps/color/codec/checksum/master readiness receipt |
| `WorkflowRun` | durable stages, current action, recovery actions, cost scope |
| `ProductionStopReceipt` | Keep/Delete decision, completed Take ids, stop time, incomplete result or deletion receipt |
| `ProductionRestartReceipt` | Prior STOP receipt, Project version edge, preserved and replacement Take ids, prompt strategy, reference assets, restart time and reason |
| `UsageEvent` | immutable member/Profile/Cost Center/model/site attribution |

### Producer read model

`GET /api/run` returns a `production_standard` object with:

- final and negative raster;
- fps, aspect, color/legal range;
- loudness and true-peak target;
- negative model/quality lane and finish model;
- current material label and master status;
- master readiness boolean and exact blockers.

This object powers the Master Rail. It is never inferred independently in
JavaScript.

### Required API behaviors

- Intake/import endpoints retain source bytes and validation results.
- Script/Bible/Shot/Take mutations require project version preconditions.
- Pause/resume/cancel/retry remain durable `/api/v1/workflows` actions.
- `POST /api/production/stop` durably records `keep` or `delete`, stops future
  legacy Production dispatch, and returns the authoritative stopped run model.
- `POST /api/production/restart` requires that durable STOP, preserves its
  history, recalibrates the same Project, selects a fresh immutable Take per
  Shot, and starts the replacement FARM queue.
- Stage checkpoint attachment includes engine proof and output asset IDs.
- Shot retry/select actions preserve versions and reopen downstream locks.
- Export validates master receipt, writes checksums, and returns authenticated
  downloads from the creator-owned vault.

## 10. Status language and microcopy

Use production nouns and plain recovery verbs. Do not use generic AI optimism.

| State | Exact pattern |
|---|---|
| no idea | `Write the picture, paste a script, or bring a project.` |
| Bible review | `Lock story, identity, and visual grammar before camera.` |
| camera queued | `Waiting for Wan2.2 A14B on [site].` |
| camera rolling | `[Shot] · Wan2.2 A14B · denoise [n/40]` |
| negative ready | `720p camera negative · not final` |
| QC hold | `[Shot] stopped: [failed check].` |
| retry wait | `Attempt [n] waits [duration]. Nothing completed will repeat.` |
| finish | `[Take] · diony_sr_v1 · 1920×1080` |
| mix | `Mixing to [−24/−16] LUFS · −1 dBTP.` |
| ready | `1080 master ready · checksum verified` |
| disconnected | `The line dropped. The run may still be rolling.` |
| cost threshold | `[Cost Center] crossed its monitoring threshold. Production remains open.` |
| unrated | `Use recorded. Cost not rated yet.` |

Progress describes the current action and evidence, not an invented remaining
time. Loading states retain the board skeleton. Empty states name the action
that creates content. Partial failures keep successful stages visible.

### 10.1 Production monitoring contract

Production answers four questions without requiring log interpretation: is the
run working, which clip is active, what has completed, and what needs attention.
`GET /api/run` owns those answers. The browser never promotes a clip, picture
master, mix, or program master from animation or elapsed time.

- **Clips** is a durable workprint ledger. Every selected clip exposes Camera
  720, QC, Finish 1080, and Ready milestones; exactly one milestone may be
  active. A successful 720 artifact remains visible while later clips work.
- The active clip exposes structured denoise or frame progress, including the
  measured engine rate and engine ETA when the runtime provides them. No
  remaining time is fabricated from the wall clock. Unknown tqdm placeholders
  such as `?it/s` and `? left` are never showrunner copy; the board says that
  denoise is starting until measured values exist.
- Camera totals come from landed project artifacts. An adopted/restarted worker
  may reset its in-memory `done` counter, but Production must not regress from
  two landed negatives to `0 / N` or relabel the third clip as clip one.
- **Activity** begins with one bounded live signal in plain language, then a
  checkpoint timeline. GPU load, memory use, connection sample time, source,
  clip/take identity, and the original technical line remain attached as
  evidence. Opening Activity exposes the live-work ledger, measured stage and
  compute facts, message count, newest-first event status, and the filtered raw
  technical message without requiring a second disclosure click.
- Current work is stage-correct: a Finish frame is never labeled Denoise, a
  Finish-working clip is never repeated as a Camera-working clip, missing GPU
  telemetry never renders as measured zero load, and the post-clip gate names
  program-master verification while the complete decoded master is scanned.
- Repeated HTTP access logs, duplicate polling rows, and raw high-frequency
  encoder noise are not primary activity. They never enter the ARIA live region.
- Working, waiting, attention, and ready states use text and shape as well as
  color. A live pulse means a recent measured heartbeat, not merely that a run
  flag is true.
- Opening or reloading Production resets its report viewport to the top. Long
  titles wrap inside a safe line box; the toolbar, current action, tally, and
  elapsed clock may not be clipped at the supported desktop viewport.
- The Production report scales as an in-window observatory: both report columns
  use shrinkable tracks, long status text wraps within its column, and compact
  headers preserve room for Clips and Activity. Outer, clip-ledger, and
  activity scrollbars remain fully operable while rendering as 2 px indicators
  at near-transparent rest opacity, gaining contrast only while hovered or
  dragged.

## 11. Accessibility and responsive behavior

- All floors, status changes, dialogs, and menus
  are keyboard reachable with visible focus.
- Live status uses bounded `aria-live=polite`; raw high-frequency denoise output
  does not flood assistive technology.
- Failure is conveyed by text and icon/shape, never color alone.
- The Master Rail uses a definition list or equivalent labeled values.
- Desktop Production uses report columns; at ≤760 px the Master Rail becomes a
  vertical technical slate and activity follows pipeline in DOM order.
- Motion is transform/opacity only, purposeful, and removed under
  `prefers-reduced-motion`.
- Minimum touch target is 44 CSS pixels for primary actions.
- Long model names, Profile Names, paths, and Cost Center names wrap without
  horizontal document overflow.

## 12. Edge-case acceptance matrix

| Edge | Required result |
|---|---|
| idea under eight characters and no reference | reject with the two valid entry choices |
| pasted script plus idea | retain both; ask which source controls before lock |
| imported project schema newer than this Producer | read-only inspection/export; never downgrade in place |
| missing reference bytes | block affected Shot, not unrelated script work |
| duplicate browser Produce | one idempotent run; second response points to it |
| existing GPU farm busy | do not start a second show |
| browser closed during generation | run continues; replay persisted progress on return |
| selected Take is failed QC | picture lock and finish reject it |
| all Takes fail | stop after four and require human review |
| proxy is only media | Gallery remains empty; export cannot label it master |
| finish output 1920×1088 | reject and conform; never stretch |
| missing SR pack on live site | block finish with exact repair; keep negatives |
| leased GPU address changes | Studio Connect updates line; no cached stale tunnel |
| mix target changed | rebounce mix against same picture and version receipt |
| member removed mid-run | run retains attribution and completes; control transfers per policy |
| Cost Center assignment removed mid-run | frozen dispatched usage remains; future dispatch resolves again |
| cost threshold/exhaustion language | warning only; no production paywall |
| controller or worker restart | resume checkpoint without repeated successful stage |
| packaged App opens while remote FARM or Finish is active | defer source replacement until an idle Take boundary; never kill the active stage |
| optional cloud mirror fails | master remains in vault and export works locally |
| checksum fails after export | package is rejected and rebuilt from verified source |
| Distribution not locked | master may be reviewed/exported for review; not marked distributed |

## 13. Implementation order

1. Add the `production_standard` read model and Master Rail.
2. Enforce selected-Take QC before picture lock/finish.
3. Extend finish/mix/package validation receipts and generated JSON Schema.
4. Complete typed script/Bible/Shot version APIs with optimistic concurrency.
5. Surface pause/resume/retry/recovery from the durable workflow engine in
   Production without duplicating the legacy run.
6. Complete measured audio QC and post-finish QC evidence.
7. Add portable import/version compatibility and master-receipt verification.
8. Exercise each compute profile on its signed site and record evidence.

## 14. Final quality checklist

### Workflow completeness

- [ ] New idea, pasted script, import, and existing project enter one Project model.
- [ ] Script, Bible, rundown, camera, QC, finish, mix, package, and
  Distribution have durable states and next actions.
- [ ] Bible lock and Distribution remain the only human gates.
- [ ] Non-destructive versions preserve every parent and human note.

### Picture and sound

- [ ] Every final program is exactly 1920×1080 at 23.976 or 24.000 fps.
- [ ] Every Wan output is labeled 720p negative, never master.
- [ ] A14B is the default deliverable camera; 5B is previz only.
- [ ] Failed hard QC cannot enter finish.
- [ ] `diony_sr_v1`, temporal/legal/color/conform, and post-finish QC are proven.
- [ ] Dialogue, score, SFX, LUFS, true peak, and lipsync meet the locked Bible.
- [ ] Gallery and professional export require a verified real master.

### Trust, reliability, and ownership

- [ ] Master Rail values and blockers come from server production state.
- [ ] Model, prompt, seed, references, QC, site, cost, and lineage are inspectable.
- [ ] Retry is idempotent and completed stages never repeat after reconnection.
- [ ] Partial failure preserves valid work and offers a scoped recovery.
- [ ] Studio membership and Cost Center permissions are enforced at dispatch.
- [ ] Thresholds warn but never sell, throttle, or block production.
- [ ] `.diony.zip` contains contracts, media, lineage, audit, costs, and checksums;
  secrets never enter it.
- [ ] Local and each leased profile pass separate signed-site evidence.

### Product quality

- [ ] Idea, Production, and Gallery remain scannable and stable.
- [ ] Empty/loading/success/failure/retry/disconnected states are actionable.
- [ ] Keyboard, focus, screen reader, reduced-motion, and mobile checks pass.
- [ ] No copy claims native 1080 Wan, instant completion, or a cloud fallback.
- [ ] A serious producer can determine what is happening, why, what it costs,
  what is safe to do next, and whether the master is truly ready.

## Acceptance evidence boundary

Laptop tests prove contracts, UI read models, hard-QC enforcement, retries,
authorization, schemas, and dry-run behavior. Browser verification proves the
rendered Master Rail, responsive layout, keyboard path, and error states. A live
signed-site acceptance must separately prove real A14B negatives, `diony_sr_v1`
1080 output, post-finish inspection, measured mix, a checksummed master, export,
and profile-specific recovery. No lower proof layer may be reported as a live
GPU or final-master result.
