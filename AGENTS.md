# AGENTS.md — Diony AI

This file is the operating system for humans and coding agents working in this repository. Read it before changing product behavior, cluster profiles, serving recipes, or quality gates.

## Project overview

Diony AI is a proprietary packaged product: software + Infrastructure-as-Product cluster profiles + crate-to-cluster Studio + Producer. The SKU is a **4× NVIDIA DGX Spark** local cluster that produces **1-hour 1080p TV/movie-ready audiovisual** programs. Humans review, give notes, and distribute. Everything else is autonomous.

North Star: 1920×1080, 23.976 or 24.000 fps, Rec.709 legal, dialogue + score + SFX, stereo or 5.1 stems, broadcast (−24 LUFS) or streaming (−16 LUFS) loudness, true peak −1 dBTP.

## Repository structure

| Path | Role |
|---|---|
| `blueprints/` | Numbered constitution. Operational: checklists, commands, failure modes. |
| `infra/profiles/` | Versioned desired state (`spark-4x-v1`, `spark-2x-v1`, `lease-gpu-v1`, `b300-v1`, `b200-v1`). |
| `infra/compose/` | Brain / Wan / Finish service stubs. |
| `infra/switch/` | MikroTik 200G snippets, lease interconnect policy, leased-B300 / B200 on-package notes. |
| `infra/models/` | Weight pull manifests (sizes, checksums when known). |
| `apps/desk` | Installed-app and source-checkout startup authority. Customer Mac/Windows builds are self-contained; source launchers install the venv once. Both open Studio and Producer through the Desk. |
| `apps/wizard` | Crate-to-system Studio. Site first: Local Quad-Spark Cluster, Leased Dual-Spark Cluster, Leased B300, or Leased B200. Local: Unbox through Producer. Leases: Connect → App → Ready → Producer. App Continue starts the pinned DeepSeek + Wan + `diony_sr_v1` pack pull and opens Ready. Ready is Test GPU (`nvidia-smi` over the Connect SSH line). Brain is auto. Sign writes `{vault}/.cluster/site.json`. Sign is the last line only. **Refresh** on Site (and **Start over** in the mast) returns to Site and reloads the page; a browser refresh does not. |
| `apps/producer` | Idea-to-Studio. Customer playbooks, bays, gates. Idea → Production (the run) → Gallery. Production runs 1080 finish and mix. Reads the site lock. Signed Dual-Spark / B300 / B200: desk UI; GPU farms Wan and `diony_sr_v1`. |
| `apps/finish` | `diony-finish` 720→1080 (`diony_sr_v1`, Rec.709 legalizer, program master). |
| `apps/conductor` | DeepSeek tool loop. |
| `packages/contracts` | Machine API including Brief, Asset, Take versions, ClipEdit, `expand_idea`, `apply_clip_edit`, `assemble_program`. |
| `packaging/` | Self-contained PyInstaller payload plus Mac/Windows installer definitions. |
| `scripts/release/` | Platform signing, notarization, installer verification, and two-download manifest finalization. |

## Tech stack

- Cluster OS: NVIDIA DGX OS on GB10 (aarch64). Developer shells may run on a laptop.
- Brain: DeepSeek-V4-Flash-0731 via vLLM + DSpark speculative decoding. Encoding uses upstream `encoding_dsv4`, not a Jinja chat template.
- Camera: Wan2.2 official `generate.py` / Diffusers paths, LightX2V later for speed. Native output is **720p**.
- Finish: Diony 720→1080 super-res + legalizer + grade + conform (proprietary).
- Apps: Python 3.11+, FastAPI, static HTML/CSS/JS (no SPA framework on Day 1).
- Contracts: Pydantic v2 + JSON Schema.
- Infra: YAML profiles, compose/quadlet stubs, health probes. Not Terraform-for-cloud.

## Build and run

Customer first open: install the signed Mac DMG or signed Windows Setup artifact and open Diony AI. The application bundles its runtime and never asks for Python or `pip`. Until those artifacts are published, open **START HERE.txt**, then use the source launcher (`Open Diony AI.command`, `open-diony.sh`, or `Open Diony AI.bat`). START HERE names hardware: Local Quad-Spark crate, or a lease from Runpod first (`https://www.runpod.io/`) then Enverge (`https://enverge.ai/`) for Dual-Spark / B300 / B200. Both paths open the Desk lobby, then Continue is Studio at `http://studio.diony.local`.

Mac packaged-process check: double-click **Build Diony AI.command**. It builds or reuses `dist/desktop/{version}/development/Diony AI.app` and opens that exact build as a new instance; the Desk replaces an older local build and its services automatically. This is an ad-hoc local development artifact, not a publishable or notarized DMG.

Engineers:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e packages/contracts -e apps/wizard -e apps/wan -e apps/finish -e apps/producer -e apps/conductor -e apps/desk
diony-desk        # lobby progress; Studio http://studio.diony.local (binds :8787)
diony-wizard      # http://studio.diony.local (binds :8787)
diony-wan         # :8010  (cluster FARM; needs /opt/wan22 + /models/wan)
diony-finish      # :8020  (cluster FINISH; needs /models/diony/diony_sr_v1)
diony-producer    # http://producer.diony.local (binds :8788)
```

Tests (when present): `python -m pytest packages/contracts apps/wizard apps/wan apps/finish apps/producer apps/conductor apps/desk`.

Laptop pytest does **not** download weights or mutate RoCE fabric. On a signed Dual-Spark, Studio App pulls the pack. Produce farms Wan2.2 720p negatives. Picture lock writes the 1080 program master.

## Fleet modes

Never treat the cluster as a static TP=4 box.

| Mode | Nodes | Use |
|---|---|---|
| `BRAIN` | 4× DeepSeek TP=4 | Bible, hour script, continuity (256K–1M context) |
| `SPLIT` | 2× DeepSeek TP=2 + 2× Wan | Default production |
| `FARM` | 4× Wan | Generation sprint after rundown lock |
| `FINISH` | SR / NVENC / audio | 1080 masters |

Transitions: **drain → unload → load → healthgate**. Studio and Conductor use the same profile API (`infra/profiles/spark-4x-v1.yaml` locally, `spark-2x-v1.yaml` on a signed dual Spark lease, `lease-gpu-v1.yaml` on a signed four-node lease, `b300-v1.yaml` on a signed leased B300, `b200-v1.yaml` on a signed leased B200). On Dual-Spark, B300, and B200, SPLIT is sequential (one engine at a time), not 2+2 Sparks. Dual-Spark brain is TP=2. B300 brain is TP=1. B200 brain is TP=1 with `max_model_len` 16384.

## Human gates (do not automate away)

1. **Bible lock** — story, characters, visual grammar.
2. **Distribution** — who gets the master, and how.

Picture and mix run inside Production after Wan 720p (1080 finish, then mix). They are not clicks. Retakes reopen them; the run does them again.

Human notes attach to Shot/Take IDs in contracts, never only to chat logs.

## Quality bar (non-negotiable)

Full text: `blueprints/10-quality-mandate.md`. Summary:

- Every generation is **1080p finish-intent**. Wan is 720p native; Diony finish is mandatory for deliverables. Previz may stay 720p and must be labeled `previz`.
- Cinematic grammar on every Shot: focal length, stop, lighting ratio, camera move, duration, seed, references.
- Continuity-locked characters and wardrobe. Identity drift is a QC fail, not a note.
- Reject-and-retake on QC fail. Do not ship morphing faces, flicker, illegal broadcast levels, or desynced lips.
- Never cloud-by-default. No Dashscope, no hosted Qwen extend. DeepSeek on the signed site does prompt expansion. Leased Dual-Spark (`spark-2x-v1`), Leased B300 (`b300-v1`), and Leased B200 (`b200-v1`) are signed Studio Site exceptions, not hosted LLMs and not failovers. `lease-gpu-v1` is not a Studio Site.

## Key patterns

- **Contracts first.** If a field is not on a Shot/Take/QC object, it does not exist for production.
- **Blueprints are source of truth** for BOM, cabling, serving flags, and SLA math. Code implements Blueprints; it does not quietly contradict them.
- **NVIDIA Sync owns fabric.** Diony overlay owns storage, models, fleet modes, Producer. Do not reimplement Cluster Assistant.
- **Pin Spark recipes separately from GB300.** DeepSeek’s model card 4×GB300 command is not the Spark command.
- **aarch64 on box, any arch for Day 1 shells.** GB10 containers must be ARM64 when serving is implemented.

## Adding a new feature

1. Update the relevant Blueprint (or add a numbered Blueprint if the domain is new).
2. Update `packages/contracts` if the machine API changes.
3. Update `infra/profiles` if desired state changes.
4. Update Studio steps if crate-to-cluster changes; Producer if showrunner UX changes.
5. Update `NOTICE` if a new upstream license is introduced.
6. Keep the quality mandate intact.

## Common pitfalls

- Treating 720p Wan output as the deliverable.
- Running DeepSeek TP=4 during FARM (starves generation).
- Autoneg on the MikroTik (lands at 50G). Force 200G.
- Storing dailies on node NVMe until it fills. Vault is `/diony/vault` on NAS.
- Using Dashscope for prompt extend (cloud leak).
- Treating leased Dual-Spark, Leased B300, or Leased B200 as Dashscope, a default, or a failover when the local cluster is down.
- Treating Dual-Spark as Quad-Spark TP=4, or two isolated Sparks with no CX-7.
- Pasting Spark vLLM flags onto H100/B200/B300 (or GB300 4-way flags onto Spark, Spark TP=4 onto Dual-Spark, Spark TP=4 onto one B300, or B300 `1048576` onto B200).
- Assuming 1 hour is real-time. SLA is 2–5 days on `spark-4x-v1` only.
- Mixing DGX OS / firmware across nodes (NVIDIA clustering assumes matched nodes).
- Sending jobs to the internet “just this once.”

## CI / CD

Day 1: local pytest on contracts and dry-run APIs. Cluster CI is later (health probes against live Spark). Do not add cloud GPU runners as a substitute for the SKU.
