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

## Pages (three)

Idea → Production → Gallery

Producer is a **single notebook sheet** on the desk. Idea is that blank page: one field for the picture. Its empty state says only **Write an idea. Add a reference if you have one.** Length remains a labeled control rather than another explanatory paragraph. Produce starts the autonomous run (expand, Wan2.2 720p farm, stitch, 1080 finish, mix) and opens **Production**. **Sample** uses the same idea and reference bench to make a time-framed 5–10s finished clip through that same run. Production is the **run report**: what is rolling, how many of the 5s quality-lane takes are live. It is not a shot picker. **Gallery** is the inventory of produced programs and Samples. It stays empty until real finished media exists. Human gates stay: bible lock on Idea, distribution on Gallery. Picture and mix are Production steps. Producer exposes no Edit page or prompt-to-edit action.

Playbooks (director / showrunner / agency / …) stay in contracts. They are not a first-page control. Day 2 defaults to series coverage.

## What Produce does

The clapboard **Produce** (or ⌘↩ on the idea) is one motion:

1. Cast the studio from the brief (words and/or refs on the page)
2. Roll Wan2.2 **5s quality-lane** 720p negatives (live on a prepped Dual-Spark / B300 / B200; stub on a laptop without `/models`)
3. Open Production on the run report. Do not open a shot list.

Do not leave the showrunner on Idea with no run. Do not skip Production or treat Gallery as the run.

From a brief (idea + customer + **length goal**) the studio brain writes:

- Bible (title, logline, world, grammar, lead character)
- Rundown with cinematic grammar on every Shot
- Typed Wan2.2 prompt plans compiled autonomously in Subject → Motion → Camera → Scene/Lighting order (Blueprint 20)
- Timeline with handles
- Mix target from the playbook

Idea has a length picker: **30s, 1m, 3m, 5m, 10m, 15m, 20m, 30m, 40m, 1hr**. That is a **program TRT goal**, not wall-clock and not a stopwatch. Each take stays on the Wan **5s quality lane** (Blueprint 07, 10). Shot-count scales so the overlap stitch is near the goal up to **5m** (60 takes). 10m–15m cap at 60 takes this pass, with an explicit note. Target >15 minutes: **first reel only** (12 takes), with a note that a 1-hour rundown is BRAIN-mode (Blueprint 07). Do not fake 800 shots. Do not lengthen a take past 5s to fake TRT.

## What Sample does

**Sample** is production, not demo content and not a preview shortcut. The maker
chooses 5 or 10 seconds on Idea, then Sample uses the current idea and every
attached reference. Its `Brief.production_kind` is `sample` and
`Brief.sample_seconds` owns the requested 5–10 second timeframe.

- 5 seconds is one bounded Wan take. Above 5 seconds is two bounded takes whose
  frame counts add to the requested duration; Sample assembly uses a hard cut so
  the timeline does not silently lose frames to overlap.
- Every Sample Shot retains the same cinematic grammar, prompt, negative prompt,
  deterministic seed, reference IDs, A14B engine choice, and
  `finish=deliverable` contract as Produce.
- Sample opens Production and proceeds through Wan 720p, AutoQC, time-frame
  assembly, `diony_sr_v1` 1080 finish, measured mix, and master verification.
- A Sample appears in Gallery only after the same playable-media and checksum
  lineage required of a program master. Gallery labels it `1080 sample` and keeps
  its requested seconds inspectable.
- A laptop stub may prove intake and orchestration, but it cannot create a Gallery
  Sample or claim final media. Real output requires the signed production site.

## Production (the run)

`GET /api/run` is the Production floor. It reports phase, now line, live-720p tally, and the pipeline (bible → voice/performance lock for spoken Shots → Wan 720p → stitch → 1080 finish → mix → distribution). `POST /api/program/assemble` (and Produce/Roll) still writes `Timeline` with:

- start frames that **subtract** incoming overlap (8–16 frames)
- selected take on every event
- `keyframe_from_take_id` on I2V / S2V / Animate (frame-to-clip from the previous print)

**1080 finish** here is `diony_sr_v1`, run automatically after the Wan farm. Before a spoken negative, Producer locks the Character VoiceProfile, compiles the PerformanceScript, writes and verifies the processed clean/final clip audio plus timing/performance metadata, then routes the Shot through S2V. Live Wan writes vault `.mp4` 720p negatives and the tally climbs. Finish writes `finish_1080.mov` and the program master `master_1080.mov`. Mix follows without a click: it assembles verified per-clip voice packages on frame-authoritative timeline starts, measures LUFS/true peak, and muxes the rail without re-encoding picture. Mix never synthesizes dialogue. A missing voice backend/profile/package blocks the spoken Shot instead of substituting noise or unsynced TTS. Only a truly visual-only Idea receives neutral room tone, and it never receives invented dialogue merely because a coverage template has a VO slot. A laptop without `/models/wan` uses **stub camera**. Producer names that camera state directly; it does not display an `Unsigned` badge. A signed Dual-Spark / B300 / B200 with Studio Test GPU is **not** stub: desk Producer uses the Connect SSH line and farms Wan **and** `diony_sr_v1` on GPU Producer. The laptop is not the finish box. Title cards on a signed lease mean the tunnel is down, not that Wan or SR is missing on this desk.

`GET /api/run` is a **live monitor**: now line, tally, elapsed/total clock, per-shot clip states, pipeline, and a camera-report Activity column (WAN / GPU / QC / SR). It keeps polling while Production is open. **Elapsed** ticks while Wan, 1080 finish, or mix is still in the run. **Total** is the frozen duration after mix lock. A farm QC stop freezes Elapsed; it does not become Total. Activity never dumps uvicorn `GET /api/farm 200` polls. GPU pulse is nvidia-smi plus `generate.py` elapsed. QC fails are one human line (`Host Python is missing dashscope`), not a traceback, a bare `AssertionError`, or a tqdm `Loading checkpoint` / `0/40` bar. The float32 `to()` warning is not a clip fail. After a re-roll, Activity drops superseded QC for clips that are queued again. I2V/S2V without a still farm as `t2v-A14B`; the WAN line names that task. Production shows denoise `step/40` while `generate.py` is rolling. Produce refuses a second show if Wan is already on the GPU. Refresh (`#run-refresh`, **Start over**) posts `/api/studio/new` — not `/api/reset` — and SIGTERM the farm-owned `generate.py` only. Negatives live under `projects/{id}/takes/` so two Ideas cannot share `sh-010-t1`. An adopted `generate.py` is not complete merely because its MP4 path has appeared; recovery waits for process exit and two stable file-size observations before media probing or QC. **Produce** opens Production immediately; `/api/studio/open` and `/api/studio/roll` continue in the background. If the farm is already busy, Produce does not start a second show. A QC fail is **failed**, not “next.” `generate.py` must run on the host Python that can `import torch` (`DIONY_PYTHON`, usually `/usr/bin/python3` on a RunPod image) — not the Diony FastAPI venv. `generate.py --help` runs on the farm thread so Produce is not blocked on torch import.

Prompt strategy v3 supersedes the earlier compatibility fallback described above:
I2V without a still and S2V without both a still/prior frame and locked processed audio fail before GPU
dispatch. Diony never relabels the requested engine as T2V behind the receipt.

Desk Producer is the UI. GPU Producer is the camera. After a **pod restart** the SSH over exposed TCP IP and `-p` change. Studio Connect is the live line: Producer prefers that workbook over a stale `site.json` host, persists the new line, drops a dead `:18788` tunnel, and SSHes before farming. `pgrep diony-producer` is not health — GPU `:8788` `/api/site` is. A wedged Producer is freed with `fuser -k 8788/tcp` (never `pkill` by name). Desk rsyncs Producer + Wan Python onto `/opt/diony-ai` when the stamp changes; `generate.py --task` keeps running and boot **adopts** it. Producer start **reclaims** landed 720p files and watches a live generate; it does **not** auto-farm leftover takes of a blank or previous Idea. Overlay disk under 512 MB or vault under 2 GB is a farm stop, not a silent tqdm fail. Title cards on a signed lease mean the tunnel is down, not that Wan is missing on this desk.

Shot lists and version ladders stay on **Edit**.

## Gallery

`GET /api/gallery` lists **finished programs and Samples only** — one card per produced Project, and only when a real program file exists (playable `.mp4` / `.mov`). Stub title cards, coverage takes, and rundown-only shows are not Gallery clips. Coverage stays in Production lineage. 1080 is part of the Production run. A Sample card remains a full verified Project master; its shorter timeframe does not weaken Gallery lineage.

“Real” includes provenance: the program path is under the same unique Project run,
its SHA-256 matches the recorded master, and it names one verified source artifact
checksum per Shot. Gallery never guesses from a shared Shot ID and never adopts a
legacy global file. Its browser preview is derived from that verified master; the
download remains the production master.

## Engines

Coverage uses T2V-A14B, I2V-A14B (identity), S2V-14B (spoken/sung). Finish intent is `deliverable`. Previz is labeled if TI2V-5B is used later.

If the Idea explicitly identifies an attached image as “this character,” “this
person,” “this subject,” “the attached character,” or equivalent, that image is the subject lock. Every Shot
uses I2V-A14B with the same checked asset, staged to 1280×720 without distortion.
The first Shot uses the canonical still and later Shots use the prior selected
print's last frame while retaining the canonical asset in QC lineage. The farm
samples the landed raster through time and enforces an SSIM floor of `0.75` for
the binding reference/continuity contract; the first exhausted failure stops the
remaining queue and keeps finish/mix/Gallery closed.

## Cloud

Idea expansion is DeepSeek on the signed site when vLLM is up (Week 2+). Else local grammar (`diony_contracts.studio`). Never Dashscope.

## Dual-Spark (and B300)

Producer reads `{vault}/.cluster/site.json` after Studio Sign. Mast shows the site. `Project.compute` is Dual-Spark (`spark-2x-v1`, TP=2, sequential SPLIT). Persist is `{vault}/projects/{id}/project.json`. **Produce** expands on DeepSeek if vLLM is up, drains brain, then farms Wan2.2 (`generate.py`, 1280×720). **Retake** is the same camera, cap 4. Do not quote the Quad-Spark 2–5 day SLA. Do not ship the 720p negative as a master. Picture lock is the 1080 finish gate.

On spark-01: `DIONY_VAULT=/diony/vault DIONY_BIND=0.0.0.0 diony-producer`. Product URL is `http://producer.diony.local`.

## Failure modes

| Symptom | Cause | Action |
|---|---|---|
| Empty idea signed | <8 characters and no refs | API 400; words or a file on the bench |
| Previz exported as master | finish flag | contracts reject |
| Hour episode in 12 shots | user expected full TRT | copy + Blueprint 07; extend in BRAIN later |
| Edit treated as the master | Production skipped | Open Production; it is the run. Picture lock still requires selects |
| Social 9:16 bible | wrong product | keep 1080 16:9 hero |
| Quoted 2–5 day SLA on Dual-Spark | Copied Quad-Spark clock | Sequential site. Measure this pair. |
| 1080 needs diony_sr_v1 on this site | Desk scored `/models` on the laptop | Studio App pack + `gpu_ready` means SR is on the GPU. Restart desk Producer. |
| Production stuck on next 0/N | Wan QC failed; board hid it | Activity shows the traceback. Host `DIONY_PYTHON` must import torch. |
| `No module named 'dashscope'` | Official `generate.py` imports DashScopePromptExpander at load | Host pip of `dashscope`. Keys stay empty. Never `--use_prompt_extend`. Prove `generate.py --help` once before farming N clips. |
| Production says 0/N while GPU is at 100% | Desk farm memory was reset; `generate.py` still denoise | Adopt live `generate.py`. Show denoise `step/40`. Do not Produce a second show. |
| Connection refused after pod restart | RunPod TCP IP/`-p` changed; `site.json` still has the old line | Save SSH over exposed TCP on Studio Connect. Producer prefers that workbook over the stale lock. Test GPU, then Produce. |
| Produce on a new Idea while another generate.py owns the GPU | Two shows, one `takes/sh-010-t1` | Namespaced `projects/{id}/takes/`. 409 if the camera is rolling. Refresh aborts the farm-owned generate only. |
