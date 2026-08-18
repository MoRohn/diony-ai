# Changelog

All notable changes to Diony AI are documented here.

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
