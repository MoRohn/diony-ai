---
name: diony-quality
description: Enforces Diony AI 1080p SOTA quality mandate, reject-and-retake QC, and no-previz-as-master rules. Use when generating prompts, QC logic, finish pipeline, Producer copy, or any video quality behavior.
---

# Diony quality

Read `blueprints/10-quality-mandate.md` and apply it verbatim in spirit. Blueprints 24–33 add: every clip reviewed before the cut, identity against the approved sheet, unmeasured never `pass`, the likeness guard, the speech guard, words never trimmed.

## Instructions

1. Providers render 720p; the Google Flow server-side finish (Blueprint 30) or a conform at the cut delivers 1920×1080.
2. Require Shot grammar before dispatch: focal_mm, t_stop, lighting_ratio, camera_move, duration, seed, refs.
3. Identity drift, morph, flicker, illegal levels, lipsync >40 ms = QC fail. Do not finish failed takes.
4. Cap retakes (default 4) then stop for a human.
5. The screenwriter brain is any OpenAI-compatible endpoint (default Grok); prompts fit the 4096 limit before submit.
6. UI copy must not say "native 1080 Wan" or "instant hour movie".

## Examples

- Adding a new engine route: set `finish=previz` unless the 1080 path is wired.
- Producer export: refuse `previz` as ProRes HQ.
