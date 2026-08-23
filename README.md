# Diony AI

Diony AI is the packaged product that turns a **4× NVIDIA DGX Spark** desk cluster into a local TV/movie production appliance. Unbox the hardware, run the **Studio**, and the cluster becomes the **Producer**: a showrunner tool that can spend days making a **1-hour 1080p** audiovisual program with dialogue, score, and SFX. Humans stay in the loop only for review, notes, and distribution.

Diony is an **Open Source Content Producer** in spirit: a maker-first production system where prompts, assets, model lineage, costs, and masters stay visible and portable. Your studio is not a black box and your project has an exit door. A complete `.diony.zip` carries its contracts, media, lineage, audit history, and checksums without credentials. This creator-sovereignty promise does not change the repository license; see [LICENSE](LICENSE) and [NOTICE](NOTICE).

This is not a notebook, a loose ComfyUI graph, or a cloud API wrapper. It is **Infrastructure as a Product**: versioned cluster profiles, a crate-to-cluster Studio, and a Producer that treats the fleet as a mode-switching camera-and-cutting-room. A validated official ComfyUI Wan workflow can be selected as a controlled camera backend; Diony still owns contracts, gates, logging, finish, and distribution.

## Open Diony AI (no terminal)

Customer desktop distribution is now defined as two future webpage downloads: **Download for Mac** (`.dmg`) and **Download for Windows** (`Setup.exe`). Those installed applications bundle their own runtime—no Python, `pip`, terminal, or source checkout—and keep state in the normal per-user application-data directory. Signed release builders and the machine-readable `downloads.json` contract live under `packaging/` and `scripts/release/`; see [Blueprint 17](blueprints/17-desktop-distribution.md).

Until those signed artifacts are published, the source-checkout path remains:

Open **START HERE.txt**, then double-click **Open Diony AI** in this folder.

On a Mac developer checkout, **Build Diony AI.command** goes one step further: it produces the real self-contained local application at `dist/desktop/{version}/development/Diony AI.app` and opens that exact build as a new instance. The current Desk replaces an older local build and its services automatically, so validation cannot silently fall back to stale bundled UI. This local artifact is ad-hoc signed for the current Mac and is never represented as notarized or publishable.

START HERE names the hardware first: Local Quad-Spark crate, or a lease from Runpod first (`https://www.runpod.io/`) then Enverge (`https://enverge.ai/`). Dual-Spark is a 2-node pair with fabric (Runpod Instant Clusters, or Enverge **2× DGX Spark GB10**). B300 is one **NVIDIA B300**. B200 is one **NVIDIA B200** (180 GB).

| You are on | Open this |
|---|---|
| Mac | `Open Diony AI.command` |
| Windows | `Open Diony AI.bat` |
| Linux / DGX OS | `open-diony.sh` |

A page opens. First time it lays the desk (Python 3.11, a private environment, Studio and Producer). That is once. **Continue** is Studio at `http://studio.diony.local`. Producer waits until you Sign (`http://producer.diony.local`).

If macOS says the file cannot be opened: right-click it, choose Open, then Open. If it asks for Python 3.11, install that, then double-click again. Leave the small Terminal window open until it says you can close it.

Engineers who already live in a shell can still `pip install` and run `diony-desk` / `diony-wizard` / `diony-producer`.

## What you get

| Surface | Who | Job |
|---|---|---|
| **Studio** | Technician, first boot | Site first: Local Quad-Spark Cluster, Leased Dual-Spark Cluster, Leased B300, or Leased B200. Local: crate-to-system. Leases: Connect → App → Ready → Producer. App applies the pinned site pack; Ready is Test GPU; Brain is auto. Sign locks the last line. |
| **Producer** | Director / showrunner / agency / artist / instructor / creator | Idea-to-Studio: Idea → Production (the run) → Gallery |
| **Conductor** | Autonomous (DeepSeek) | Shot planning, dispatch, QC, retakes, fleet mode, disk pressure |
| **Blueprints** | Operators and coding agents | The constitution: BOM, topology, serving recipes, quality bar |

North Star deliverable: **one hour of TV/movie-ready audiovisual**, 1920×1080, 23.976 or 24.000 fps, Rec.709 legal, stereo or 5.1 stems, broadcast or streaming loudness.

## Hardware SKU (`spark-4x-v1`)

Default site: the Local Quad-Spark crate.

- 4× NVIDIA DGX Spark (GB10, 128 GB unified memory, 4 TB NVMe, ConnectX-7)
- RoCE v2 fabric switch (MikroTik CRS804 or CRS812), **200G forced**, MTU 9000
- 10GbE management switch + NAS (≥50 TB) mounted at `/diony/vault`
- UPS/PDU, matched DGX OS across all four nodes

Full BOM and cabling: [blueprints/01-crate-to-cluster.md](blueprints/01-crate-to-cluster.md) and [blueprints/09-bom-and-sku.md](blueprints/09-bom-and-sku.md).

Leased Dual-Spark (`spark-2x-v1`) is a Studio **Site** exception: two GB10 with interconnect (Runpod Instant Clusters first, or Enverge CX-7 RoCE), Site → Connect → App → Ready → Producer, sequential SPLIT, Spark TP=2 serving. Leased B300 (`b300-v1`) is one Blackwell GPU, same rail, sequential SPLIT, TP=1 serving. Leased B200 (`b200-v1`) is one 180 GB Blackwell GPU, same rail, TP=1 with `max_model_len 16384`. None is Dashscope or a failover. Four-node `lease-gpu-v1` is not a Studio Site. See [blueprints/14-compute-paths.md](blueprints/14-compute-paths.md).

## Models (local, never cloud by default)

- **Brain:** [DeepSeek-V4-Flash-0731](https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash-0731) (MIT) — showrunner, prompt expansion, QC, retakes. ~167 GB. 2-node TP=2 default; 4-node TP=4 for hour-long bible context.
- **Camera:** [Wan2.2](https://github.com/Wan-Video/Wan2.2) (Apache 2.0) — T2V-A14B, I2V-A14B, TI2V-5B, S2V-14B, Animate-14B. Native **720p**.
- **Finish:** Diony proprietary 720→1080 super-res, legalizer, grade, conform.

Wan2.2 does not ship a 1080p checkpoint. 1080p is a **Diony finish line**, not an upstream toggle. See [blueprints/06-1080p-sota-finish.md](blueprints/06-1080p-sota-finish.md).

## Fleet modes

The cluster is a mode-switching appliance, not a static 4-way tensor-parallel box.

| Mode | Layout | When |
|---|---|---|
| `BRAIN` | 4× DeepSeek TP=4 | Hour-long bible, shotlist, continuity |
| `SPLIT` | 2× DeepSeek TP=2 + 2× Wan | Default production |
| `FARM` | 4× Wan | Picture generation sprint |
| `FINISH` | Super-res / NVENC / mix | 1080 conform, loudness, masters |

Mode changes are drain → unload → load → healthgate. Same API for Studio and Conductor.

## Honest clock

A 1-hour episode is **~2–5 wall-clock days** on this desk cluster, including retakes, 1080 finish, and mix. GB10 bandwidth is 273 GB/s. This is “given enough time and energy,” not real-time.

## Day 1 vs later

Laptop shells stay dry-run (no weights). After Studio App Continue on Dual-Spark, the pair has `/models/wan`, `/opt/wan22`, and `/models/diony/diony_sr_v1`. Produce farms Wan2.2 720p negatives. Picture lock writes the 1080 program master.

## Repo map

```
AGENTS.md                 How humans and agents operate this product
blueprints/               Numbered operational constitution (00–17)
infra/                    Cluster profiles, compose stubs, switch snippets
START HERE.txt            First open for people who should not see pip
apps/desk                 First open (double-click Open Diony AI)
apps/wizard               Crate-to-system Studio (Site, then unbox through Producer URL)
apps/producer             Idea-to-Studio (customer bays, dailies, gates)
apps/wan                  Wan2.2 native / validated ComfyUI worker (`diony-wan` :8010)
apps/finish               720→1080 diony_sr_v1 (`diony-finish` :8020)
apps/conductor            DeepSeek tool loop (dispatch, QC, retakes)
packages/contracts        Project / Shot / Take / QC JSON schemas
.cursor/rules             Persistent agent rules
.cursor/skills            Cluster ops, quality, blueprint skills
```

The Producer launch control plane lives in `{vault}/.control/diony.sqlite3`: durable workflow checkpoints, workspace isolation, generation jobs, progress events, Team/Department/Cost Center allocation, fixed-point internal-credit and cost analytics, audit records, encrypted BYOK configuration, and optional S3/R2 delivery. There is no billing or payment rail. See [Blueprint 16](blueprints/16-launch-control-plane.md).

## Run the shells (engineers)

Studio and Producer are Day 1 dry-run apps on a laptop (`DIONY_DRY_RUN=1`). They do not mutate fabric. Dual-Spark App Continue is live apply when `DIONY_WIZARD_LIVE=1`.

Requires **Python 3.11+**. Prefer **Open Diony AI** above if you do not want this.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e packages/contracts -e apps/wizard -e apps/wan -e apps/finish -e apps/producer -e apps/conductor -e apps/desk

diony-desk         # first-open lobby, then Studio + Producer
diony-wizard       # :8787
diony-producer     # :8788
```

Open Studio at `http://studio.diony.local` and Producer at `http://producer.diony.local`. Day 2 laptop binds are `:8787` and `:8788`.

Studio: Site is line 01 (Local Quad-Spark, or a named Dual-Spark / B300 lease). Then Unbox before power on the local path. Left column teaches, right column is the signed report. Empty fields and missed checks cannot be signed. Pending lines cannot be skipped.

Producer: write the idea, **Produce**, screen **Production**, then open **Gallery** for finished clips. Over 15 minutes you get a first reel, not a fake 800-shot hour.

## License

Diony AI software and Blueprints are **proprietary**. See [LICENSE](LICENSE). Wan2.2 remains Apache 2.0. DeepSeek-V4-Flash-0731 remains MIT. Real-ESRGAN x2 (bootstrap `diony_sr_v1`) remains BSD-3. NVIDIA DGX OS / Sync remain NVIDIA’s. Attribution and terms: [NOTICE](NOTICE).

## Contributing

This is a proprietary product. Internal agents and authorized engineers follow [AGENTS.md](AGENTS.md), the Blueprints, and `.cursor/rules`. Do not weaken the 1080p mandate. Do not send jobs to the cloud by default. Human gates stay at bible and distribution. Picture and mix run inside Production.
