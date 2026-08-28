# Blueprint 05 — Producer pipeline

Status: Week 2+ camera (Wan2.2 FARM). Human gates: bible, distribution.
Contracts: `packages/contracts`

## Showrunner loop

```
brief → bible (DeepSeek, BRAIN or SPLIT)
     → HUMAN GATE: bible lock
     → rundown (Shot[] JSON)
     → keyframe lock (stills / I2V refs)
     → VoiceCasting lock + Wan speech request (spoken Shots)
     → Wan speech request lock (casting, line, window, direction)
     → generate (Wan T2V / I2V / audio-driven S2V)
     → AutoQC
        fail → DeepSeek retake (new Take, same Shot id)
        pass → Diony 1080 finish (Production)
     → timeline conform
     → verified per-clip audio assembly + score / SFX / mix (Production)
     → masters
     → HUMAN GATE: distribution
```

Producer UI exposes this as **Idea, Production, Gallery**. Idea records a **length goal** (30s–1hr, default 5m). That is a goal, not a stopwatch. Coverage uses the Wan **5s quality lane**; shot-count scales so stitched TRT is near the goal up to 5m (60 takes). >15 min is first reel (Blueprint 12). **Produce** starts the autonomous run (expand → Wan 720p farm → stitch → 1080 finish → mix). **Production** is the report for that run: now line, tally, pipeline steps. It is not a shot list. Coverage stays in Production lineage. Gallery is the persistent inventory of produced full programs and remains independently accessible when the active Project is blank or new. Humans own bible lock and distribution. There is no Edit floor or prompt-to-edit route.

## Gallery clip composition

Gallery may compose two through 32 finished programs or earlier joined versions into a new, immutable 1080p asset. Selection order is explicit and reorderable. Every adjacent pair owns one junction with independent left trim-out, right trim-in, transition type, and transition duration. Frames are authoritative; the UI also displays timecode and rounded milliseconds. A clean cut is the default.

Every Gallery card is a compact screening object, never an embedded full-page player. Producer extracts and caches the first decoded video frame as the card poster, then exposes explicit Watch, Full screen, Download, Select, and Delete actions. Watch opens a focused screening dialog; the inventory grid remains visible behind it. Stream responses stay inline while Download responses use an attachment filename. Program playback, poster, and download URLs carry the verified program checksum, so a replacement master for the same Project cannot reuse Safari's cached invalid bytes. Delete is recoverable removal from Gallery: it records soft-delete state and retains masters, source clips, and composition lineage in the vault. Requesting or selecting a retake immediately removes the stale program from Gallery while retaining its bytes and receipts for audit. Only a later successful finish may return the current program to Gallery.

Every finished-clip card also exposes a **Details** icon. Details opens a source slate without starting playback: the original Idea prompt is readable and copyable through one accessible overlapping-squares icon, and every original Brief reference is named and downloadable from the local vault. Prompt copy must attempt selected-text copy synchronously inside the original click gesture so Safari on Mac does not lose clipboard permission, then use the modern Clipboard API as the Windows/Mac fallback. A successful copy shows visible confirmation without replacing the icon. If both browser paths are blocked, the prompt stays selected and the visible status names the Mac (`Command-C`) and Windows (`Ctrl-C`) manual keys. A joined version resolves its immutable source lineage and shows each contributing Project's Idea prompt and reference files in source order. Reference download routes must authorize the requested asset against that clip's source lineage; a caller cannot use a Gallery URL to fetch an unrelated vault asset.

`ClipComposition` is the composition decision and lineage manifest. It records ordered `ClipJoinSource` rows, `ClipJoinPoint` junctions, parent composition, source asset ids, Profile Name, Cost Centers, output paths, status, and exact total frames. Revising a composition creates a child version; undo marks a version superseded without deleting its sources or manifest. The rendered master is ProRes 422 HQ, 10-bit, PCM audio, 1920×1080, project fps, and Rec.709. The Gallery uses a separate H.264 proxy.

Before render, Producer probes every source. Resolution, frame-rate, and codec mismatches are disclosed in the plan and conformed to the project finish. A transition may not exceed either retained side. Preview renders only the selected junction context so long programs do not require a full temporary render. Creation is cancellable; cancellation terminates the active encoder, preserves every source, and writes a cancelled manifest. A completed version becomes a normal Gallery source and remains exportable with its source relationship.

## Objects (names are the product language)

| Object | Meaning |
|---|---|
| `Project` | One production or episode container |
| `Bible` | World, characters, visual grammar, fps, loudness target |
| `Shot` | One camera event: duration, lens, light, move, prompt, refs, seed |
| `Take` | One generated attempt at a Shot, plus readable historical child versions. Versions are children, never overwrites. |
| `Artifact` | Immutable file for a Take (720 negative, 1080 finish, proxy) |
| `Asset` | Idea reference: still, clip, or document in the vault |
| `ClipEdit` | Legacy read-only lineage from Projects created before the Edit surface was retired |
| `QCReport` | Machine verdict on a Take |
| `VoiceCasting` | Immutable Project-scoped decision: which prompt clip Wan2.2 clones for a Character |
| `WanSpeechRequest` | The line, casting, resolved prompt clip, window, and performance direction handed to Wan's TTS lane |
| `SpeechClipPackage` | Locked `WanSpeechRequest`, the dialogue Wan delivered, its mix stem, the negative's hash, and speech QC |
| `Timeline` | Ordered Shot ids + handles |
| `MixLock` | Stem list + LUFS + picture-lock ref |
| `Deliverable` | Master files + checksums |

Human notes attach to `Shot.id` or `Take.id`.

## Shot grammar (required fields)

A Shot that lacks these is invalid; Conductor must fill them before dispatch:

- `duration_frames` and `fps`
- `size` native Wan size (720p quality, or labeled previz)
- `focal_mm`, `t_stop`, `lighting_ratio`, `camera_move`
- `prompt` (extended) + `negative_prompt`
- `seed` (stable retakes vary seed **only** when QC asks)
- `character_ref_ids[]`, `keyframe_asset_ids[]`, `reference_lock`
- `continuity_mode` (`independent` | `reference` | `previous_frame`)
- `engine` (`t2v-A14B` | `i2v-A14B` | `ti2v-5B` | `s2v-14B` | `animate-14B` | `vace-fun-A14B`)
- `finish`: `previz` or `deliverable` (deliverable implies 1080 path)

## Engine routing

| Intent | Engine |
|---|---|
| Establishing / cinematic, no lock | T2V-A14B |
| Character or wardrobe lock | I2V-A14B from locked still |
| Fast blocking | TI2V-5B, `finish=previz` |
| Talking / singing to picture | locked WanSpeechRequest → S2V-14B native TTS (recorded audio → S2V `--audio`) |
| Motion transfer / replacement | Animate-14B |
| Legacy VACE child Take import | VACE Fun A14B metadata, read-only |

Overlap windows: adjacent shots share 8–16 frames of last/first keyframe when continuity is required. Before dispatch, Producer extracts the previous selected print's last usable frame and makes it the incoming I2V conditioning frame; a missing boundary frame blocks the Shot instead of falling back to a canonical still or T2V. `assemble_program` writes the parent Take and handles onto `Timeline`. Assembly temporally conforms each selected print to its declared frame allotment, blends the declared handles, and clamps the encoded picture to `Timeline.total_frames`; it does not discard the incoming handles and hard-concatenate the clips. Mix pads its stem rail to picture duration before `-shortest`, so fractional-rate audio rounding can never remove locked picture frames.

## AutoQC (minimum Day 1 spec; models later)

Fail (block finish) on:

- identity drift vs character still
- reference/style drift sampled at first, quarter, middle, three-quarter, and last frame when the supplied image owns the character/world; a same-background lock requires every sample to retain SSIM ≥ 0.75
- reference exclusions reach true negative conditioning on every backend: ComfyUI receives its negative input; Diony's idempotent host overlay exposes the official native Wan `n_prompt` path through `generate.py`, retains Wan's pinned technical negatives, and appends the Shot exclusions. A binding reference owns its rendering medium, so a generic live-action `cartoon` exclusion is removed from reference-conditioned I2V instead of contradicting an illustrated source
- a reference-locked retake refreshes the current strict camera direction before dispatch, so an older Project cannot replay stale coverage/inserts language; persisted pre-detector Projects are atomically promoted to I2V, reference keys, previous-frame continuity, and overlap before the new Take is created
- historical `PASS` is never sufficient for finish: the receipt must match the Shot's current engine, current reference ids, all five reference checkpoints, and (for chained Shots) the current previous Take plus boundary evidence; an AutoQC failure remains the visible Production phase even when negative bytes landed
- after a Producer restart, camera adoption may resume only the Take currently selected on each Shot; missing negatives on aborted, rejected, or superseded Take rows remain audit history and are never inferred to be queued work
- first/last-frame boundary mismatch against the recorded parent Take
- morphing / melting faces or hands (heuristic + conductor VLM later)
- temporal flicker above threshold
- duration mismatch > 1 frame vs Shot
- Rec.709 illegal after finish
- delivered speech cut by the picture window, or absent, on S2V
- speaker cosine similarity < 0.92 or Project consistency < 0.90
- missing/unverified SpeechClipPackage for any spoken Shot
- voiced-speech presence < 0.20 or dialogue stem outside the Bible loudness/true-peak gates
- dialogue SNR < 30 dB, harshness/sibilance above 0.25, or loudness/peak outside the Bible target
- loudness out of spec on mix

Reference-lock and first/last boundary failures reject only the immutable Take,
not the Production run. Producer selects a newly seeded generation Take and
retries that Shot ahead of the remaining rundown. A passing retake resumes the
queue without repeating completed Shots. The failed print and QC receipt remain
in the vault for audit. Automatic quality retakes stop after the fourth
generation Take and then ask for human review; provider, disk, runtime, and
missing-model failures do not consume quality-retake attempts.

Warn (human visible, not auto-fail) on:

- minor color mismatch vs bible LUT
- audio bed missing (if picture-only take)

## Conductor tools (conceptual)

- `set_mode`
- `expand_prompt`
- `dispatch_shot`
- `ingest_take`
- `run_qc`
- `request_retake`
- `assemble_timeline` / `assemble_program`
- `bounce_mix`
- `pause_for_gate`

Day 1 Conductor is a stub that walks a fixture project through states so Producer UX is real.

Producer `POST /api/studio/roll` farms Wan2.2 when this process has `/models/wan` **or** when a signed Dual-Spark / B300 / B200 site has a Connect SSH line (desk Producer tunnels to GPU Producer). Official `generate.py`, 720p mp4 negatives in the GPU vault. Cap 4. Production then runs 1080 finish (`diony_sr_v1`) and mix without a click. Studio App already pulled Wan + `diony_sr_v1` onto that GPU; desk Producer must not look for `/models` on the laptop or say the 1080 pack is missing “on this site.” Unsigned laptop without weights stays stub (title cards, then auto 1080/mix gates). A signed lease with `gpu_ready` is not a stub camera. After a pod restart, desk Producer reads live Studio Connect (not a cached `site.json` TCP line), rsyncs Producer/Wan onto `/opt/diony-ai`, and health-checks GPU `:8788` — it does not trust `pgrep diony-producer`. Dual-Spark drains brain (`DIONY_DRAIN_BRAIN`) then FARM; drains Wan then FINISH. Retakes reopen picture/mix; the run does them again.

For spoken Shots, the farm first resolves the locked Character VoiceCasting,
locks the WanSpeechRequest (line, prompt clip, window, direction), and only then
enters S2V camera preflight; Wan2.2 performs the line and animates the face in
the same pass, and the delivered speech is measured on the negative. Final Mix
assembles those verified clip packages and never synthesizes dialogue. Full
contracts, dispatch arguments, and thresholds are Blueprint `22`.

## Site lock

Studio Sign writes `{vault}/.cluster/site.json`. Producer stamps `Project.compute` from that lock. Dual-Spark, B300, and B200 are sequential: one engine at a time. Do not quote the Quad-Spark 2–5 day SLA on those sites.

## Failure modes

| Symptom | Cause | Action |
|---|---|---|
| Chat-only notes | notes not on Shot | Producer forbids; API rejects |
| Previz shipped as master | `finish=previz` slipped | Deliverable requires `finish=deliverable` + 1080 asset |
| Infinite retakes | QC too strict / seed thrash | Cap retakes per Shot (default 4) then human |
| Timeline gaps | Shot duration != Take | Conform refuses; retake |
| 1080 pack missing on this site | Desk scored laptop `/models` | Studio `gpu_ready` means `diony_sr_v1` is on the GPU |
