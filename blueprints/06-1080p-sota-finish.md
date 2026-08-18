# Blueprint 06 — 1080p SOTA finish

Status: Week 2+ live (`diony-finish`)  
This is the proprietary picture line. Do not pretend Wan is 1080p.

## Truth

Wan2.2 official generation is **480p / 720p**. TI2V-5B 720p@24 is the fast native HD. T2V/I2V-A14B 1280×720 is the quality native HD.

Diony deliverables are **1920×1080**. The missing 1.5× is a **finish pipeline**, not a resolution dropdown.

## Pipeline (picture)

1. **Generate native 720p24** (or 23.976 by generating 24.000 and conform — prefer generating at bible fps if Wan accepts; else generate 24.000 and pull-down/conform in finish).
2. **Temporal overlap / first-last frame** so adjacent takes share identity.
3. **Prompted edit** in 720p (VACE/Animate) until QC pass at 720p.
4. **Video-aware super-res** 1280×720 → 1920×1080
   - Runtime: `diony-finish` (`diony_sr_v1`). Tiled RRDB x2 (tile 192, overlap 64) → 2560×1440, Farneback warp blend, then zscale bicubic conform to 1920×1080 Rec.709 limited.
   - Bootstrap checkpoint: `ai-forever/Real-ESRGAN` `RealESRGAN_x2.pth` at `/models/diony/diony_sr_v1` (BSD-3). Wizard App pulls it. The pipeline (tiles, temporal warp, legalizer, overlap stitch) is Diony.
   - Do not use naive ffmpeg `scale=1920:1080:flags=lanczos` for deliverable (allowed for labeled proxies only)
5. **Grain-aware denoise / optional film grain** per bible (`grain: none|fine|35mm`)
6. **Color:** bible LUT or conductor-stated grade. Rec.709.
7. **Legalizer:** luma 16–235, chroma 16–240 (studio). No super-white unless bible HDR (out of scope Day 1).
8. **Conform:** 1920×1080, exact fps, even frame count, handles per Timeline. Picture lock writes `takes/{id}/finish_1080.mov` then `program/{timeline}/master_1080.mov`.

## What “SOTA” means here

Not “type 1080p in the Wan CLI.” It means:

- Cinematic Shot grammar (mandate `10`)
- A14B quality lane for deliverable picture (5B is previz)
- Identity lock via stills + I2V + Animate
- Reject-and-retake at 720p **before** spending SR compute
- Temporal SR, not still-image upscale per frame
- Broadcast-legal 1080 master (`master_1080.mov`). Mix lock refuses without it when finish is live.

## Proxies vs masters

| Label | Size | Allowed scale |
|---|---|---|
| `previz` | 720p | native Wan |
| `proxy` | 1080p | lanczos OK |
| `deliverable` | 1080p | `diony_sr_v1` only |

## Compute placement

`FINISH` mode. Drain Wan first on Dual-Spark / B300. `diony-finish` on :8020, or in-process from Producer after picture lock. NVENC on GB10 for H.265 proxy. ProRes via ffmpeg `prores_ks` profile 3 (HQ), libx264 fallback. Do not ProRes on a laptop and call it the cluster master unless the checksum matches cluster bounce.

## Failure modes

| Symptom | Cause | Action |
|---|---|---|
| Soft 1080, no detail | lanczos shipped as deliverable | Reject; rerun SR |
| Boiling grain | per-frame still SR | Temporal SR only |
| Identity change after SR | SR model drift | QC after SR, not only before |
| 1920×1088 / odd sizes | 16-alignment | Crop/pad in conform, never stretch faces |
| Picture lock with no SR weights | Wizard App skipped or dry-run | App Continue must pull `RealESRGAN_x2.pth` |
| Mix lock before 1080 | Finish still running | Wait for `master_1080.mov` |

