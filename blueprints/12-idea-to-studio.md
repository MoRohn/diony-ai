# Blueprint 12 — Idea to Studio

Status: Week 2+ camera  
Producer is an **Idea-to-Studio** floor, not a node graph.

## Who hires the studio

| Customer | Producer treats them as | Default loudness |
|---|---|---|
| Feature / short | director | broadcast −24 LUFS |
| Series / episode | showrunner | broadcast |
| Brand / commercial | agency | broadcast |
| Music video | artist | streaming −16 LUFS |
| Education / explainer | instructor | streaming |
| Social / short hero | creator | streaming; still 1920×1080 Rec.709 |

Vertical crop is later. Do not silently switch the bible to 9:16.

## Pages (four)

Idea → Dailies → Program → Lock

Producer is a **single notebook sheet** on the desk. Idea is that blank page. Bible writes onto the same sheet after Produce. Rundown lives inside Dailies as the shot list. **Program** is the overlap stitch and the screening of selected prints. Humans still own bible, picture, mix, distribution gates.

Playbooks (director / showrunner / agency / …) stay in contracts. They are not a first-page control. Day 2 defaults to series coverage.

## What Produce does

The clapboard **Produce** (or ⌘↩ on the idea) is one motion:

1. Cast the studio from the brief (words and/or refs on the page)
2. Roll Wan2.2 720p negatives (live on a prepped Dual-Spark; stub on a laptop without `/models`) so Dailies already has takes
3. Open Dailies on the first shot. Program already has an overlap stitch of those prints.

Do not leave the showrunner on Idea with no picture. Do not require a separate Roll to see coverage. Do not skip Program — dailies are coverage, not the show.

From a brief (idea + customer + **length goal**) the studio brain writes:

- Bible (title, logline, world, grammar, lead character)
- Rundown with cinematic grammar on every Shot
- Timeline with handles
- Mix target from the playbook

Idea has a length picker: **30s, 1m, 3m, 5m, 10m, 15m, 20m, 30m, 40m, 1hr**. That is a goal, not a stopwatch. Default **5m**. Coverage shot-count scales with the goal (4–12 shots). Target >15 minutes: **first reel only**, with an explicit note that a 1-hour rundown is BRAIN-mode (Blueprint 07). Do not fake 800 shots in the UI. Short targets still get overlap stitch and optional prompt-to-edit.

## Program (stitch and screen)

`POST /api/program/assemble` (and Produce/Roll) writes `Timeline` with:

- start frames that **subtract** incoming overlap (8–16 frames)
- selected take on every event
- `keyframe_from_take_id` on I2V / S2V / Animate (frame-to-clip from the previous print)

The Program page screens the cut: 16:9 stage, shot list, TRT vs target. Live Wan plays vault `.mp4` 720p negatives. After picture lock, the stage prefers `finish_1080.mov` and the timeline preview is `master_1080.mov`. Laptop without `/models/wan` shows title cards.

## Engines

Coverage uses T2V-A14B, I2V-A14B (identity), S2V-14B (spoken/sung). Finish intent is `deliverable`. Previz is labeled if TI2V-5B is used later.

## Cloud

Idea expansion is DeepSeek on the signed site when vLLM is up (Week 2+). Else local grammar (`diony_contracts.studio`). Never Dashscope.

## Dual-Spark (and B300)

Producer reads `{vault}/.cluster/site.json` after Wizard Sign. Mast shows the site. `Project.compute` is Dual-Spark (`spark-2x-v1`, TP=2, sequential SPLIT). Persist is `{vault}/projects/{id}/project.json`. **Produce** expands on DeepSeek if vLLM is up, drains brain, then farms Wan2.2 (`generate.py`, 1280×720). **Retake** is the same camera, cap 4. Do not quote the Quad-Spark 2–5 day SLA. Do not ship the 720p negative as a master. Picture lock is the 1080 finish gate.

On spark-01: `DIONY_VAULT=/diony/vault DIONY_BIND=0.0.0.0 diony-producer`. Product URL is `http://producer.diony.local`.

## Failure modes

| Symptom | Cause | Action |
|---|---|---|
| Empty idea signed | <8 characters and no refs | API 400; words or a file on the bench |
| Previz exported as master | finish flag | contracts reject |
| Hour episode in 12 shots | user expected full TRT | copy + Blueprint 07; extend in BRAIN later |
| Dailies treated as the master | Program skipped | Assemble; picture lock still requires selects |
| Social 9:16 bible | wrong product | keep 1080 16:9 hero |
| Quoted 2–5 day SLA on Dual-Spark | Copied Quad-Spark clock | Sequential site. Measure this pair. |
