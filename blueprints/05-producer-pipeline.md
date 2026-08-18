# Blueprint 05 — Producer pipeline

Status: Week 2+ camera (Wan2.2 FARM). Human gates unchanged.  
Contracts: `packages/contracts`

## Showrunner loop

```
brief → bible (DeepSeek, BRAIN or SPLIT)
     → HUMAN GATE: bible lock
     → rundown (Shot[] JSON)
     → keyframe lock (stills / I2V refs)
     → generate (Wan T2V / I2V / S2V)
     → prompted edit (VACE / Animate) if the Shot asks
     → AutoQC
        fail → DeepSeek retake (new Take, same Shot id)
        pass → Diony 1080 finish
     → timeline conform
     → dialogue / score / SFX / mix
     → HUMAN GATE: picture lock + mix lock
     → masters
     → HUMAN GATE: distribution
```

Producer UI exposes this as **Idea, Dailies, Program, Lock**. Idea records a **length goal** (30s–1hr, default 5m). That is a goal, not a stopwatch. Coverage shot-count scales; >15 min is first reel (Blueprint 12). Dailies include a **version ladder** and **mark the print** (Blueprint 13). Program is the overlap stitch and the screening of the cut.

## Objects (names are the product language)

| Object | Meaning |
|---|---|
| `Project` | One production or episode container |
| `Bible` | World, characters, visual grammar, fps, loudness target |
| `Shot` | One camera event: duration, lens, light, move, prompt, refs, seed |
| `Take` | One generated **or edited** attempt at a Shot. Versions are children, never overwrites. |
| `Artifact` | Immutable file for a Take (720 negative, 1080 finish, proxy) |
| `Asset` | Idea or edit reference: still, clip, or document in the vault |
| `ClipEdit` | Instruction that produced a child Take via VACE |
| `QCReport` | Machine verdict on a Take |
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
- `character_ref_ids[]`, `keyframe_asset_ids[]`
- `engine` (`t2v-A14B` | `i2v-A14B` | `ti2v-5B` | `s2v-14B` | `animate-14B` | `vace-fun-A14B`)
- `finish`: `previz` or `deliverable` (deliverable implies 1080 path)

## Engine routing

| Intent | Engine |
|---|---|
| Establishing / cinematic, no lock | T2V-A14B |
| Character or wardrobe lock | I2V-A14B from locked still |
| Fast blocking | TI2V-5B, `finish=previz` |
| Talking / singing to picture | S2V-14B + CosyVoice or licensed VO |
| Motion transfer / replacement | Animate-14B |
| Inpaint, outpaint, camera, prompted edit | VACE Fun A14B |

Overlap windows: adjacent shots share 8–16 frames of last/first keyframe when continuity is required. `assemble_program` writes those handles onto `Timeline`. I2V / S2V / Animate inherit the previous selected print (`keyframe_from_take_id`) — frame-to-clip, not a new generation from a blank prompt.

## AutoQC (minimum Day 1 spec; models later)

Fail (block finish) on:

- identity drift vs character still
- morphing / melting faces or hands (heuristic + conductor VLM later)
- temporal flicker above threshold
- duration mismatch > 1 frame vs Shot
- Rec.709 illegal after finish
- lipsync offset > 40 ms on S2V
- loudness out of spec on mix

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

Producer `POST /api/studio/roll` and `POST /api/shots/{shot_id}/retake` farm Wan2.2 when `/models/wan` (or `DIONY_LIVE=1`) is present. Official `generate.py`, 720p mp4 negatives in the vault. Cap 4. Picture lock refuses retake. Picture lock with live finish writes 1080 masters and the program master. Laptop without weights stays stub. Dual-Spark drains brain (`DIONY_DRAIN_BRAIN`) then FARM; drains Wan then FINISH.

## Site lock

Wizard Sign writes `{vault}/.cluster/site.json`. Producer stamps `Project.compute` from that lock. Dual-Spark and B300 are sequential: one engine at a time. Do not quote the Quad-Spark 2–5 day SLA on those sites.

## Failure modes

| Symptom | Cause | Action |
|---|---|---|
| Chat-only notes | notes not on Shot | Producer forbids; API rejects |
| Previz shipped as master | `finish=previz` slipped | Deliverable requires `finish=deliverable` + 1080 asset |
| Infinite retakes | QC too strict / seed thrash | Cap retakes per Shot (default 4) then human |
| Timeline gaps | Shot duration != Take | Conform refuses; retake |
| Cut with no stitch | Dailies treated as the show | Fail Program. Assemble the overlap cut |
