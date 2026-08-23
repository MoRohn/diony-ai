# Blueprint 07 — Hour-long audiovisual

Status: Day 1 locked  
Promise: a 1-hour clip **given enough time and energy**. Not real-time.

## Frame math

| Item | Number |
|---|---|
| 1 hour @ 24.000 fps | 86,400 frames |
| 1 hour @ 23.976 fps | 86,313.6 → **86,314** frames (drop/handle in conform) |
| Average TV shot length | 3–8 s |
| Shot count (planning band) | **400–800** generated shots |
| If all shots were 5 s | 720 shots |

Prefer fewer, longer held shots when the bible allows. Shot count drives wall clock more than runtime.

## Wall-clock SLA (honest)

GB10 memory bandwidth is **273 GB/s**. A14B 5-second 720p takes are **minutes to tens of minutes** each, not seconds. Until this exact cluster is measured, publish:

| Phase | Order of magnitude |
|---|---|
| Bible + rundown (BRAIN) | hours, not days |
| Generation (SPLIT or FARM), 400–800 shots, retake factor 1.3–1.5 | **1.5–4 days** |
| 1080 finish + conform | hours to a day |
| Dialogue, score, SFX, mix | hours to a day (S2V talking heads are the expensive audio-picture overlap) |
| **First 1-hour episode, including gates** | **~2–5 wall-clock days** |

Measure on spark-4x-v1 and replace this table with observed p50/p90. Until then, do not quote faster numbers in UI copy. Do **not** quote this 2–5 day band for `spark-2x-v1` or `b300-v1` — those sites are sequential and slower. Measure each profile.

Retake budget: default **4 takes per Shot**, then human. Assume 30–50% of shots take more than one take.

## Storage

Weights on NVMe: ~0.5–1 TB (DeepSeek ~167 GB + Wan family + SR + TTS).

Vault (NAS ≥50 TB usable):

| Content | Rough |
|---|---|
| 720p takes (many rejects) | 1–4 TB / episode |
| 1080 intermediates | 1–3 TB |
| ProRes HQ 1-hour master | ~100–200 GB class (varies) |
| Audio stems + sessions | tens of GB |
| **Plan per episode working set** | **multiple TB** |

Scratch on NVMe is wipeable. Never the only copy of selects.

## Audio hour

- Dialogue: script lines on Shot → CosyVoice (or licensed VO) → S2V when lips must match
- Score: ACE-Step (Blueprint pin) to picture duration + handles
- SFX: library on vault + generated fills
- Mix: −24 LUFS broadcast or −16 LUFS streaming; TP −1 dBTP; stem bounce

Picture lock **before** final mix. Temp mix allowed under dailies.

## Energy

4×240 W + switch + NAS on UPS. A 4-day FARM run is a **kWh** story, not a thermal crisis if ventilation is desk-sane. Do not disable UPS. Conductor should pause FARM on vault disk <15% or UPS on battery.

## Failure modes

| Symptom | Cause | Action |
|---|---|---|
| “Overnight movie” expectation | marketing vs GB10 | SLA table in Producer |
| Vault full at hour 40 | no NAS / small NAS | halt; Blueprint 09 BOM |
| 10,000 one-second shots | rundown too cutty | Conductor rewrite shot length |
| Audio after 1080 drift | recut | re-run 1080 finish and mix |
