# Blueprint 00 — Product constitution

Status: Day 1 locked  
Owner: Diony AI  
Applies to: every Blueprint, profile, app, and agent session

> **Amendment, 2026-08-31 (Blueprint 25).** Generation is **cloud by default**: Grok Imagine (xAI) and Google Flow (through useapi.net) are the cameras, the Director (`apps/director`) is the primary surface, and Studio's job is to choose the engine. Locked truth 5 below ("never cloud-by-default") and the DeepSeek/Wan/fleet-mode truths described the local lane, which was removed from the product on 2026-09-10; they are history, not doctrine. The human gates, the quality bar (`10`), the vault, and the contracts-first rule stand unchanged. Where this document and a Blueprint numbered 25 or above disagree on the Director lane, the higher number wins.

## What Diony AI is

Diony AI is a **packaged SKU**: software, versioned cluster profiles, crate-to-cluster Studio, Producer, Conductor, and these Blueprints. A customer takes the hardware cluster and this product and can make a **full 1-hour TV/movie-ready clip** given enough time and energy.

It is not:

- a research notebook
- a ComfyUI distribution
- a cloud video API
- a replacement for NVIDIA Sync / DGX OS

## Surfaces

| Surface | User | Success |
|---|---|---|
| Desk | First software open | START HERE: local crate, or Runpod then Enverge leases. Double-click Open Diony AI (11). Continue is Studio. |
| Studio | Technician at first boot | Site recorded. Local Quad-Spark: fabric green. Leased Dual-Spark, Leased B300, or Leased B200: Connect → App → Ready → Producer. App applies the pinned site pack; Ready is Test GPU; Brain is auto. Sign writes `{vault}/.cluster/site.json` and locks the last line. Then open Producer. |
| Producer | Showrunner | Idea → Production (the run) → Gallery. Production runs 1080 finish and mix. Reads the site lock. Notes on Shot/Take IDs |
| Conductor | Autonomous | Plans, dispatches, QC, retakes, mode changes. Stops at bible and distribution. |
| Blueprints | Operators and coding agents | Operational truth. Code implements; it does not contradict. |

## North Star deliverable

One hour of audiovisual program:

- 1920×1080
- 23.976 fps (broadcast) or 24.000 fps (theatrical). Default: **23.976** unless the bible says 24.000.
- Rec.709 legal (studio range 16–235 luma unless HDR is later authorized)
- Dialogue + music + SFX
- Stereo default; 5.1 stems when the bible asks
- Loudness: −24 LUFS (broadcast) or −16 LUFS (streaming). True peak −1 dBTP
- Masters: ProRes 422 HQ (or DNxHR HQ) + H.265 proxy
- Humans: review, notes, distribution only

## Locked technical truths

1. **Wan2.2 is native 720p.** There is no upstream 1080p Wan checkpoint. Deliverables go through Diony 1080 finish (`06`).
2. **DeepSeek-V4-Flash-0731 is the brain.** MIT. ~167 GB. Spark recipes are not GB300 recipes (`04`).
3. **1 hour is a multi-day job.** SLA 2–5 wall-clock days (`07`). Do not promise real-time.
4. **NAS is the vault.** Node NVMe holds weights and scratch. `/diony/vault` holds projects.
5. **Never cloud-by-default.** No Dashscope, no hosted prompt extend, no off-box inference unless a human explicitly authorizes it. Local Quad-Spark (`spark-4x-v1`) is the default. Leased Dual-Spark (`spark-2x-v1`), Leased B300 (`b300-v1`), and Leased B200 (`b200-v1`) are Studio **Site** exceptions with a named human (`14`). They are still DeepSeek on that site — not a hosted LLM and not a failover. `lease-gpu-v1` is not a Studio Site.
6. **NVIDIA Sync owns fabric.** Diony overlay owns storage, models, fleet modes, Producer.

## Human gates (cannot be removed)

1. Bible lock
2. Distribution

Picture and mix are Production steps, not clicks. After Wan 720p, the run finishes 1080 (`diony_sr_v1`) and locks mix (loudness from the bible). Retakes reopen picture and mix; the run does them again. Do not skip bible or distribution.

## Fleet modes (cannot be collapsed)

`BRAIN` | `SPLIT` | `FARM` | `FINISH`

See `03-fleet-profiles.md`. Default production mode is `SPLIT`.

## License posture

- Diony AI: proprietary (`LICENSE`)
- Wan2.2 and VACE Fun: Apache 2.0 (`NOTICE`)
- DeepSeek-V4-Flash-0731: MIT (`NOTICE`)
- NVIDIA stack: NVIDIA terms on the hardware; not redistributed here

Generated content is the licensee’s. They are accountable for lawful use under upstream restrictions.

## Change control

A change that affects BOM, cabling, serving flags, quality bar, or SLA must update the numbered Blueprint **in the same change** as the code. Contracts change when the machine API changes. `NOTICE` changes when a new upstream license appears.
