# Changelog

All notable changes to Diony AI are documented here.

## Unreleased

### Added

- Producer Sample is now a real 5s/10s Idea action. It uses the current words and references, runs bounded Wan2.2 A14B camera takes through AutoQC, exact time-frame assembly, mandatory `diony_sr_v1` 1080 finish and mix, and enters Gallery only with the normal checksum-verified master lineage. The prior Night Press fixture shortcut is no longer wired to the Idea page.
- Subject-defining image references are now binding production locks: singular characters and plural character teams keep every Shot on Wan I2V, same-world shots hold one uninterrupted composition, the negative prompt reaches Wan's true `n_prompt` conditioning, five-point clip evidence is recorded (with a strict same-background floor), and I2V cannot silently downgrade to T2V. Adjacent locked Shots condition on the prior selected print's last frame, must pass boundary QC, and assemble through an eight-frame temporal blend while preserving requested Sample TRT. Picture conforms to the exact timeline frame count and the padded mix rail cannot trim locked picture frames. Retakes refresh the strict direction, and a landed-but-failed negative remains a visible QC hold instead of appearing to enter finish.
- Retake now repairs persisted pre-reference-detector Projects in place: the full Shot chain is promoted to I2V, reference keys, strict negatives, prior-frame continuity, and overlap before a new Take rolls. Binding I2V uses style-neutral technical negatives rather than the generic live-action `cartoon` exclusion, so illustrated references are not told to abandon their own medium. Finish rejects legacy or stale `PASS` receipts unless their engine, reference ids, five checkpoints, and prior-Take boundary evidence match the current Shot contract. Any retake invalidates and hides the old Gallery master until a newly verified finish succeeds; failed bytes remain in the vault for audit. Producer restart adoption resumes only currently selected missing Takes, never obsolete aborted/rejected rows.
- Gallery program playback, poster, and download URLs are now versioned by the verified program checksum. Re-finishing the same Project therefore displays the new master immediately instead of letting Safari replay the prior invalid video and thumbnail from cache.
- Gallery Details Copy Prompt now keeps one accessible overlapping-squares icon and copies synchronously from the visible prompt selection before any async browser boundary, fixing Safari on Mac while retaining the modern Clipboard API fallback for Windows and Mac. The modal shows visible success, or leaves the prompt selected with Command-C/Ctrl-C guidance if browser policy blocks both paths.
- Gallery is now an independent persistent library. It remains navigable when Producer reopens on a blank/new Idea, loads finished programs from the vault rather than the active Project, and shows explicit loading or failure status instead of disabling the floor.

### Changed

- The Production Shots ledger now expands through the unused desktop folio height, scrolls within that lane, and keeps bottom safe-area breathing room instead of stopping at a fixed `18rem` cap.
- Producer now has three pages: **Idea → Production → Gallery**. The non-working Edit page, its navigation/state, cutting-room DOM/JavaScript/CSS, Wizard promises, catalog entry, and prompt-to-edit API route were removed. Historical `ClipEdit` lineage remains readable so existing Project records are not corrupted. Producer UI stamp is `run60`; Studio UI stamp is `ready55`.

## 0.22.0 — 2026-08-20

### Added

- Wizard `ready31` replaces the undersized navigation and floating 3D notebook treatment with a 112px stage mast, 288px desktop cue rail, and integrated production-folio workspace; the exact B200 form and 390px mobile layout remain unclipped while Wizard objectives and site choices are unchanged.
- Repository-root `Build Diony AI.command` creates or reuses a deterministic native Mac development app, selects the stable `dist/desktop/{version}/development/Diony AI.app` path in Finder, and emits explicit non-publishable local proof without confusing it with Developer ID/notarization.
- Self-contained desktop release path for the future two-button webpage: notarized/stapled/Gatekeeper-verified Universal Mac DMG and Authenticode-signed/timestamped Windows installer, deterministic secret-free payload staging, and a stable manifest that refuses incomplete platform proof.
- Installed-app startup hardening: bundled service dispatch with no system Python or first-run `pip`, standard per-user state directories, immediate Desk feedback, semantic Wizard/Producer health and UI-stamp checks, safe unknown-port handling, persistent logs, and downloadable secret-free diagnostics. Blueprint 17 defines the acquisition, install, startup, trust, and publish gates.

- Launch control-plane contracts for users, workspaces, teams, departments, Cost Centers, governed rate cards, memberships, durable workflow runs, generation jobs, provider configuration, immutable usage/cost-allocation/audit records, render lineage, and replayable progress.
- Transactional local-first SQLite controller with WAL, strict workspace isolation, role and cost-admin gates, soft-delete support, fixed-point internal-credit and cost allocation, exact reconciliation, monitor-only budgets, scoped and organization rollups, metrics, and correlation IDs.
- Provider-neutral local registry preserving DeepSeek-V4-Flash-0731, Wan2.2 native 720p, assembly, mandatory `diony_sr_v1` finish, and creator-owned packaging. No hosted provider is registered by default.
- Retryable, resumable one-stage-at-a-time workflow orchestration with exponential backoff, cross-process dispatch leases, a `diony-control-worker`, signed-site engine checkpoint proof, explicit optional-stage skips, fallbacks, pause/cancel/retry controls, SSE progress, and actionable errors. Required stages stay `waiting_provider` until an engine proves output; no asset is invented.
- AES-256-GCM BYOK storage, optional AWS S3/Cloudflare R2 media adapter, and verifiable `.diony.zip` project export that excludes secrets while preserving project cost lineage and dimension snapshots.
- Workspace project inventory, team/membership administration, input/output safety checkpoints, JSON correlation logs, success/latency/cost-per-video-minute metrics, and confirmed project erasure across controller lineage, unshared vault media, exports, and configured object mirrors.
- Blueprint 16 defines the Open Source Content Producer philosophy, operational boundaries, API, failure modes, and proof layers. The repository license and Wizard/Producer objectives are unchanged.

## 0.21.0 — 2026-08-20

### Changed

- Desk, Wizard, and Producer now share the Diony Chorus creative system: Deep Void / Parchment / Diony Crimson / Stage Amber, Archivo Black display type, IBM Plex work and machine type, an original surface-specific stage spirit, a single crimson workflow ribbon, visible keyboard focus, responsive layouts, and reduced-motion fallbacks. Workflow gates, site truth, and Producer behavior are unchanged. Wizard UI stamp is `ready30`; Producer UI stamp is `run13`; Desk is `desk4`.
- Blueprint 15 is the source of truth for brand, motion, accessibility, surface density, and the originality boundary.

## 0.20.0 — 2026-08-19

### Changed

- Lease Wizard is **Site → Connect → App → Ready → Producer**. Ready is **Test GPU**: SSH over the Connect line, `nvidia-smi` must match the Site (B200 / B300 / Dual-Spark). **Pack red** means App did not pull (desk `DIONY_DRY_RUN=1`). **Pull pack** on Ready SSHes the same line. Pytest records the same rows and never opens a socket. A real IP requires a green Test GPU before Continue.
- Desk restarts Wizard when `:8787` is healthy but still serving an old HTML stamp (`app.js?v=`). Open Diony AI is enough to pick up UI. Hard-refresh `http://wizard.diony.local` once after restart.
- Wizard UI stamp is `ready29`. Producer UI stamp is `run12`. Floors are **Idea → Production → Edit → Gallery**. Produce opens Production immediately; open/roll continue in the background. If Wan is already farming, Produce does not start a second show. Production is a **live monitor** (tally, denoise step/40, elapsed/total clock, per-shot clips, camera-report activity — WAN / GPU / QC / SR — not uvicorn access logs). After Wan 720p, Production runs **1080 finish and mix** with no picture-lock or mix-lock click. Human gates stay bible and distribution. Edit and Gallery stay empty until a real clip exists. On a signed Dual-Spark / B300 / B200, desk Producer farms Wan **and** `diony_sr_v1` through the Connect SSH line onto GPU Producer. After a **pod restart** Producer prefers live Wizard Connect over a stale `site.json` IP/`-p`, drops a dead tunnel, health-checks GPU `:8788` (not `pgrep`), rsyncs Producer/Wan onto `/opt/diony-ai`, and adopts a live `generate.py` instead of auto-farming leftover takes. `generate.py` uses the host Python that has torch (`DIONY_PYTHON`), not the Diony FastAPI venv. Official `generate.py` still `import dashscope` at load — that is a host pip stub, not a Dashscope API call; keys stay empty and `--use_prompt_extend` is never passed. Farm proves `generate.py --help` on the farm thread, once, before N clips. Leased B200 / B300 farm with `--offload_model False` (upstream defaults True on one GPU). Spark keeps offload + dtype convert. Official Wan `flash_attention` asserts FlashAttention-2; lease host pip skips that wheel, so Diony applies Wan’s own SDPA path on `[B, L, N, C]` before FA2 flatten (SDPA after flatten is a `self.o` shape crash, not a clip QC fail). Activity never treats tqdm `Loading checkpoint` / `0/40` / the float32 `to()` warning as a clip QC fail. A QC fail is **failed**, not “next.” **Refresh** on Production (and **Start over** in the mast) posts `/api/studio/new` and aborts the farm-owned `generate.py`; it does not load Night Press. Wizard Sign + Test GPU is the pack lock — the laptop is not the camera and not the finish box. A signed lease with Test GPU is not a stub camera. Edit is optional coverage after the farm. Gallery is the clip inventory.
- Producer sheet uses IBM Plex Sans (same as Wizard) and fills the window.
- Length goal uses the Wan **5s quality lane**. Shot-count scales so stitched TRT is near 30s–5m. Do not lengthen a take past 5s. >15m is still first reel.
- Ready shows **Pack pull in progress** (moving bar + latest apply line + elapsed) while Hugging Face is on the GPU. Continue stays off until the pull finishes. Landing on Ready resumes the poll.
- **Refresh** on Site clears this Wizard and reloads the page (walk, apply log, Connect SSH user/port, local `site.json`). **Start over** in the mast does the same. Browser refresh does not. HF_TOKEN stays.
- App and Ready have **HF_TOKEN**. Open https://huggingface.co/settings/tokens, Create new token +, paste it. The token is not written to site.json or Wizard state.
- RunPod `Connection refused` on Ready means the pod is down or the TCP IP/`-p` changed. Connect stays editable after pass. Pull pack uses the Connect Host, not a stale inventory copy.
- Lease App Continue starts the pack pull and opens Ready. It does not wait on Hugging Face. A second Continue no longer deadlocks the Wizard.
- Lease App on RunPod: if `mount --bind /workspace /diony/vault` is denied, symlink `/workspace` so the product path stays `/diony/vault`. Python install uses `$DIONY_REPO/.venv`, then `pip --break-system-packages` (PEP 668). Apply skips `pip install -U pip` when `huggingface_hub` is already in the GPU venv (that listing was a false FAIL). Apply errors stay on App / Ready, not Connect.

## 0.19.0 — 2026-08-19

### Changed

- Wizard Site is four choices: **Local Quad-Spark Cluster**, **Leased Dual-Spark Cluster**, **Leased B300**, **Leased B200**. Three leases stay separate. Local is never pre-selected. `lease-gpu-v1` is still not a Wizard Site.
- Runpod → Wizard Connect map for B200 and B300: the Connect page lists the RunPod Connect headings in order (**HTTP services** skip, **SSH** skip, **SSH over exposed TCP** use, **Web terminal** skip, **Direct TCP ports** same). Host / SSH user / SSH port come from `ssh root@IP -p PORT`. Vault is Details → Pod volume Mount path `/workspace`. Blueprint 14.
- Leased B200 (`b200-v1`) is TP=1, 180 GB, `max_model_len 16384`, `gpu-memory-utilization 0.95`. Not a drop-in B300. Do not paste `1048576`.
- START HERE names hardware first: local crate, or a lease from Runpod (`https://www.runpod.io/`) then Enverge (`https://enverge.ai/`). Dual-Spark still uses a shared Network Volume. B200/B300 skip Network Volume unless it is already in that GPU’s data center.
- Connect SSH user: GPU login (word before `@` on RunPod Connect). Prefer the TCP line (`root`). Wizard refuses a pasted `ssh user@host -p PORT` line. Optional SSH port on B200/B300 (`DIONY_SSH_PORT`) for rsync.
- Open Producer and Continue use the product URLs `http://wizard.diony.local` and `http://producer.diony.local` on every site. Day 2 still binds `:8787` / `:8788`. Open Diony AI runs `python -m diony_wizard.gateway ensure` after Wizard/Producer start (hosts + `:80` Host router). First-open and Continue open those names, not `127.0.0.1:8787`. The Mac password helper runs `gateway install` in the foreground (it detaches `serve`); it does not `nohup` inside AppleScript. If names are not live, the lobby stays up (`need_names`) instead of opening a URL that does not resolve.

## 0.18.0 — 2026-08-18

### Added

- First open without a terminal: **START HERE.txt**, then double-click **Open Diony AI**. A lobby page lays `.venv` once and starts Wizard + Producer. Continue is Wizard.

## 0.17.0 — 2026-08-18

### Changed

- Wizard 0.17. Sign writes `site.json` with vault mount `/diony/vault` and copies it onto the GPU vault when live. App Continue mounts the recorded NFS share. Local How installs `diony-finish`. Open Producer uses host:8788 when live (laptop dry-run stays `127.0.0.1:8788`). Retry pack on a failed App apply.

## 0.16.0 — 2026-08-18

### Added

- Wizard Dual-Spark / B300 App Continue is live apply: SSH BatchMode (or local on spark-01), Hugging Face pack pull (DeepSeek, Wan A14B, `diony_sr_v1`), clone `/opt/wan22`, install `diony-finish`, start Producer. Pytest stays `DIONY_DRY_RUN=1` and never SSHes.
- `diony-finish` (`apps/finish`, :8020): tiled Real-ESRGAN x2 temporal SR → zscale 1920×1080 Rec.709 legal. Picture lock writes `takes/{id}/finish_1080.mov` and `program/{timeline}/master_1080.mov`. Mix lock refuses without those masters when finish is live. Never lanczos as a master.
- Producer Idea **length goal**: 30s, 1m, 3m, 5m, 10m, 15m, 20m, 30m, 40m, 1hr. Not a stopwatch. >15m is first reel.

### Changed

- Dual-Spark App is not a printed homework sheet. How is the log of what Wizard runs. Sequential SKU still starts Producer only (Produce starts Wan; picture lock starts finish).
- Producer 0.8. Idea length goal picker. Picture lock on a live site without `diony_sr_v1` is refused.

## 0.15.0 — 2026-08-18

### Added

- Live Wan2.2 camera: `diony-wan` wraps official `generate.py` (`1280*720`, Spark offload). Never Dashscope.
- Producer 0.6 FARM: after Wizard pack on Dual-Spark, Produce expands on DeepSeek if vLLM is up, drains brain, then writes 720p mp4 negatives into the vault. Retake uses the same path. Cap 4. Picture lock still refuses.
- Laptop without `/models/wan` stays stub so pytest does not call GPU.

### Changed

- Dual-Spark / B300 App starts `diony-wan`, clones Wan2.2 to `/opt/wan22`, installs `requirements.txt`, sets `PYTHONPATH`, `DIONY_PYTHON`, `DIONY_WAN_URL`, and `DIONY_DRAIN_BRAIN`.

## 0.14.0 — 2026-08-18

### Added

- Wizard Sign writes `{vault}/.cluster/site.json` and `mode.lock`. Producer and Conductor read the same lock.
- Producer 0.5 is Dual-Spark usable: `GET /api/site`, persist `projects/{id}/project.json`, dry-run **Retake** (cap 4), sequential SPLIT copy, `DIONY_BIND` / `DIONY_VAULT` on spark-01.
- Conductor refuses concurrent brain+Wan when the signed site is Dual-Spark or B300.

### Changed

- Dual-Spark and B300 App start Producer with `DIONY_VAULT=/diony/vault` and `DIONY_BIND=0.0.0.0`. Product URL stays `http://producer.diony.local`.
- New projects inherit `ClusterSite` from the site lock. Do not quote the Quad-Spark 2–5 day SLA on this pair.
- Live Wan / vLLM remain Week 2+. Day 2 still dry-run negatives.

## 0.13.0 — 2026-08-18

### Added

- Wizard Site **Leased Dual-Spark Cluster** (`dual` → `spark-2x-v1`): two GB10, Spark TP=2 brain, sequential SPLIT, CX-7 RoCE between the pair. Named human required. Not a shrink of Quad-Spark. Not B300. Not `lease-gpu-v1`.
- Profile `infra/profiles/spark-2x-v1.yaml`, serving notes, compose `brain-spark-2x.yml` / `split-spark-2x.yml`, interconnect `infra/switch/spark-2x-interconnect.md`
- `ComputePath.DUAL` on `ClusterSite`

### Changed

- Site labels: **Local Quad-Spark Cluster**, **Leased Dual-Spark Cluster**, **Leased B300**
- Lease rails are Site → Connect → App → Producer. App holds the pinned pack and Producer start. Desk records the pack on Producer. Models is not a Wizard page
- Do not quote the `spark-4x-v1` 2–5 day SLA on Dual-Spark or B300

## 0.11.3 — 2026-08-18

### Changed

- Connect Vault now teaches what it is, what to type, and where to find it (provider Storage / NFS). Notes show the fstab example. Desk Vault names the NAS admin UI and 10GbE address

## 0.11.2 — 2026-08-18

### Changed

- Producer and Wizard use product addresses: `http://producer.diony.local` and `http://wizard.diony.local`. Same URL on the laptop and on the GPU. Day 2 still binds `:8788` / `:8787`

## 0.11.1 — 2026-08-18

### Changed

- Wizard lines use choice buttons instead of empty “Choose” dropdowns. Site no longer stacks a Compare panel on top of the live hint
- Models auto-opens pull commands. Connect is host + vault against a short profile card. Producer is a floors card + URL + Sign. The signed report is a lock sheet, not a JSON dump

## 0.11.0 — 2026-08-18

### Changed

- Wizard Models is the pinned site pack (DeepSeek-V4-Flash-0731 + Wan2.2 A14B). Continue records it. No model shopping, no Hugging Face `latest` tags. TI2V-5B stays previz and is not in the pack
- Brain is no longer a Wizard page. The pack auto-records BRAIN (`spark-4x-v1` TP=4 on the desk, `b300-v1` TP=1 on Leased B300)
- Leased B300 Wizard is **Site → Connect → Models → App → Producer**. Local crate is ten lines (Site through Producer, no Brain line)

## 0.10.0 — 2026-08-18

### Changed

- Leased B300 Wizard is **Site → Connect → Models → Brain → App → Producer**. App installs and starts `diony-producer`. Producer confirms Idea / Dailies / Program / Lock, then Sign
- Producer adds **Program**: overlap stitch (8–16 frames), frame-to-clip on I2V, 16:9 screening. Dailies prompt-to-edit (optional in/out) stays on the take. Produce rolls and stitches so the cut is ready

## 0.9.0 — 2026-08-18

### Changed

- Leased B300 Wizard is **Site → Connect → Models → Brain → Producer**. Connect no longer auto-fills weights, BRAIN, or the Producer URL. Models is the DeepSeek + Wan pull. Brain is `b300-v1` TP=1. Sign still only on Producer, then open the showrunner

## 0.8.1 — 2026-08-18

### Changed

- Wizard Site is two choices: **Local DGX Cluster** or **Leased B300**. Four-node leased GPU is not a Wizard Site
- **Continue** on every line except the last. The clapboard **Sign** is only Ready / Producer

## 0.8.0 — 2026-08-18

### Changed

- Leased GPU and Leased B300 Wizard is **Site → Connect → Ready**. Connect is hosts + vault (defaults filled). Interconnect, matched image, weight pull, BRAIN, and Producer URL are recorded from the signed Site — not quizzes. Ready shows the plan and locks the pack
- Signing Site is the named-human authorization. The extra “Authorize this lease?” field is gone

## 0.7.1 — 2026-08-18

### Changed

- Single B300 is labeled **Leased B300** and uses the same six-line software rail as Leased GPU (Site → Nodes → Vault → Weights → Brain → Producer). No Unbox, Cables, Switch, Sync, or Fabric. Still `b300-v1` / TP=1 — not a four-node `4×B300` lease

## 0.7.0 — 2026-08-18

### Changed

- Leased GPU Wizard is a **software rail**: Site → Nodes → Vault → Weights → Brain → Producer. No Unbox, Cables, Switch, Sync, or Fabric. Provider already owns the box; Wizard records hosts, vault, weights, serving, and the Producer URL
- Until Site is signed, the menu is only Site. Desk still expands to the eleven-line crate sequence

## 0.6.0 — 2026-08-18

### Added

- Wizard Site third path: single B300 (`b300-v1`). Same rail and Producer as the desk crate. Named human required. TP=1 serving; SPLIT is sequential, not 2+2 Sparks
- Cluster profile `b300-v1`, on-package interconnect `infra/switch/b300-node.md`, compose stubs `brain-b300.yml` / `split-b300.yml`
- Per-site serving notes and Wizard **site pack** (profile + fstab + manifest + serving + Producer URL) so lease and B300 prep stay inside Diony

### Changed

- Lease Site is complete for Day 2: Files expose profile, serving notes, and pack; GPU class list includes 4×B300 (four-node lease, not the single-B300 Site)
- `ComputePath` adds `b300`. Blueprint 14 covers three sites

## 0.5.0 — 2026-08-17

### Added

- Wizard Site (`path`): local 4× Spark crate **or** leased GPU. Same rail, same Producer, same 1080 mandate. Lease is never the default and requires a named human exception
- Cluster profile `lease-gpu-v1` and interconnect policy `infra/switch/lease-interconnect.md`
- `ClusterSite` / `ComputePath` on `Project.compute`
- Blueprint 14 (compute paths). Site is Wizard line 01

### Changed

- Blueprints 00, 01, 08, 11: never cloud-by-default still holds; lease is a chosen site, not failover or Dashscope

## 0.4.0 — 2026-08-17

### Added

- Wizard crate-to-system preparation: Unbox as line 01 (before power), signed report, switch snippets, health dry-run, weight manifest and vault fstab in the report
- Blueprint 01/11: ten-line sequence starting at Unbox

## 0.3.0 — 2026-08-17

### Added

- Multimodal Brief: idea refs as image, video, or document (`Asset`, cutting bench in Producer)
- Clip edits: feedback on a take becomes a child version via Wan VACE Fun A14B — parent negative is never overwritten
- Version ladder and vaulted clip artifacts (720 negative / 1080 finish / proxy)
- Blueprint 13 (idea refs, mark-the-print edits, immutable versions)

## 0.2.0 — 2026-08-17

### Added

- Wizard camera school: learn + do workbook, check questions, interactive port-2 loom (Blueprint 11)
- Producer Idea-to-Studio: customer stamps, cast-from-idea, bible / rundown / dailies / lock bays (Blueprint 12)
- `diony_contracts.studio` playbooks for feature, series, commercial, music video, education, social

## 0.1.0 — 2026-08-17

### Added

- Product constitution: proprietary LICENSE, NOTICE (Wan Apache 2.0, DeepSeek MIT), README, AGENTS.md
- Blueprints 00–10 (crate-to-cluster through quality mandate)
- Infrastructure-as-Product profile `spark-4x-v1` (compose stubs, MikroTik 200G snippets, model manifest, health probe)
- `diony-contracts` (Project/Bible/Shot/Take/QC/Timeline/MixLock/Deliverable)
- Wizard dry-run (`diony-wizard` :8787)
- Producer showrunner shell (`diony-producer` :8788)
- Conductor stub (`diony-conductor` :8789)
- Cursor rules and skills for quality, cluster ops, and Blueprints
