# Diony AI

Diony AI is the packaged product that turns a **4× NVIDIA DGX Spark** desk cluster into a local TV/movie production appliance. Unbox the hardware, run the **Wizard**, and the cluster becomes the **Producer**: a showrunner tool that can spend days making a **1-hour 1080p** audiovisual program with dialogue, score, and SFX. Humans stay in the loop only for review, notes, and distribution.

This is not a notebook, not ComfyUI, and not a cloud API wrapper. It is **Infrastructure as a Product**: versioned cluster profiles, a crate-to-cluster Wizard, and a Producer that treats the fleet as a mode-switching camera-and-cutting-room.

## What you get

| Surface | Who | Job |
|---|---|---|
| **Wizard** | Technician, first boot | Site first: Local Quad-Spark Cluster, Leased Dual-Spark Cluster, or Leased B300. Local: crate-to-system. Leases: Connect → App → Producer. App applies the pinned site pack; Brain is auto. Sign locks the last line. |
| **Producer** | Director / showrunner / agency / artist / instructor / creator | Idea-to-Studio: bench, bible, rundown, dailies, Program stitch, version ladder, gates |
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

Leased Dual-Spark (`spark-2x-v1`) is a Wizard **Site** exception: two GB10 with CX-7 RoCE, Site → Connect → App → Producer, sequential SPLIT, Spark TP=2 serving. Leased B300 (`b300-v1`) is one Blackwell GPU, same rail, sequential SPLIT, TP=1 serving. Neither is Dashscope or a failover. Four-node `lease-gpu-v1` is not a Wizard Site. See [blueprints/14-compute-paths.md](blueprints/14-compute-paths.md).

## Models (local, never cloud by default)

- **Brain:** [DeepSeek-V4-Flash-0731](https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash-0731) (MIT) — showrunner, prompt expansion, QC, retakes. ~167 GB. 2-node TP=2 default; 4-node TP=4 for hour-long bible context.
- **Camera:** [Wan2.2](https://github.com/Wan-Video/Wan2.2) (Apache 2.0) — T2V-A14B, I2V-A14B, TI2V-5B, S2V-14B, Animate-14B. Native **720p**. Prompted edit via Wan2.2-VACE-Fun-A14B.
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

Mode changes are drain → unload → load → healthgate. Same API for Wizard and Conductor.

## Honest clock

A 1-hour episode is **~2–5 wall-clock days** on this desk cluster, including retakes, 1080 finish, and mix. GB10 bandwidth is 273 GB/s. This is “given enough time and energy,” not real-time.

## Day 1 vs later

Laptop shells stay dry-run (no weights). After Wizard App Continue on Dual-Spark, the pair has `/models/wan`, `/opt/wan22`, and `/models/diony/diony_sr_v1`. Produce farms Wan2.2 720p negatives. Picture lock writes the 1080 program master.

## Repo map

```
AGENTS.md                 How humans and agents operate this product
blueprints/               Numbered operational constitution (00–14)
infra/                    Cluster profiles, compose stubs, switch snippets
apps/wizard               Crate-to-system Wizard (Site, then unbox through Producer URL)
apps/producer             Idea-to-Studio (customer bays, dailies, gates)
apps/wan                  Wan2.2 generate.py worker (`diony-wan` :8010)
apps/finish               720→1080 diony_sr_v1 (`diony-finish` :8020)
apps/conductor            DeepSeek tool loop (dispatch, QC, retakes)
packages/contracts        Project / Shot / Take / QC JSON schemas
.cursor/rules             Persistent agent rules
.cursor/skills            Cluster ops, quality, blueprint skills
```

## Run the shells (developer workstation)

Wizard and Producer are Day 1 dry-run apps on a laptop (`DIONY_DRY_RUN=1`). They do not mutate fabric. Dual-Spark App Continue is live apply when `DIONY_WIZARD_LIVE=1`.

Requires **Python 3.11+** (GB10 cluster is aarch64; Day 1 shells run on a laptop).

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e packages/contracts -e apps/wizard -e apps/wan -e apps/finish -e apps/producer -e apps/conductor

# Terminal A — Wizard on :8787
diony-wizard

# Terminal B — Producer on :8788
diony-producer
```

Open Wizard at `http://wizard.diony.local` and Producer at `http://producer.diony.local`. Day 2 laptop binds are `:8787` and `:8788`.

Wizard: Site is line 01 (Local Quad-Spark, or a named Dual-Spark / B300 lease). Then Unbox before power on the local path. Left column teaches, right column is the signed report. Empty fields and missed checks cannot be signed. Pending lines cannot be skipped.

Producer: pick who hired the floor, lay stills/clips/pages on the **cutting bench**, write the idea, **Cast the studio**. In Dailies, **mark the print** to edit a take (new version on the ladder). Over 15 minutes you get a first reel, not a fake 800-shot hour.

## License

Diony AI software and Blueprints are **proprietary**. See [LICENSE](LICENSE). Wan2.2 remains Apache 2.0. DeepSeek-V4-Flash-0731 remains MIT. Real-ESRGAN x2 (bootstrap `diony_sr_v1`) remains BSD-3. NVIDIA DGX OS / Sync remain NVIDIA’s. Attribution and terms: [NOTICE](NOTICE).

## Contributing

This is a proprietary product. Internal agents and authorized engineers follow [AGENTS.md](AGENTS.md), the Blueprints, and `.cursor/rules`. Do not weaken the 1080p mandate. Do not send jobs to the cloud by default. Human gates stay at bible, picture lock, mix lock, and distribution.
