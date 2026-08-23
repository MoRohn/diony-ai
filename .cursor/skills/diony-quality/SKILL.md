---
name: diony-quality
description: Enforces Diony AI 1080p SOTA quality mandate, reject-and-retake QC, and no-previz-as-master rules. Use when generating prompts, QC logic, finish pipeline, Producer copy, or any video quality behavior.
---

# Diony quality

Read `blueprints/10-quality-mandate.md` and apply it verbatim in spirit.

## Instructions

1. Treat Wan output as **720p camera negative**. Deliverable path is `diony_sr_v1` to 1920×1080 Rec.709 legal.
2. Require Shot grammar before dispatch: focal_mm, t_stop, lighting_ratio, camera_move, duration, seed, refs.
3. Identity drift, morph, flicker, illegal levels, lipsync >40 ms = QC fail. Do not finish failed takes.
4. Cap retakes (default 4) then stop for a human.
5. Prompt expansion is DeepSeek on the signed site only (Local Quad-Spark, Dual-Spark, leased B300, or leased B200). Never Dashscope.
6. UI copy must not say "native 1080 Wan" or "instant hour movie".

## Examples

- Adding a new engine route: set `finish=previz` unless the 1080 path is wired.
- Producer export: refuse `previz` as ProRes HQ.
