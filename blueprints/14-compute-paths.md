# Blueprint 14 — Compute paths (Quad-Spark, Dual-Spark, B300)

Status: Day 2  
Owner: Diony AI  
Implements: Wizard Site (`path`) and profiles `spark-4x-v1`, `spark-2x-v1`, `b300-v1`. `lease-gpu-v1` remains in infra and is **not** a Wizard Site. Does not replace Blueprint 01.

## Goal

The same Diony product on three Wizard sites:

1. **Local Quad-Spark Cluster** (`spark-4x-v1`) — four GB10 on the floor. Default.
2. **Leased Dual-Spark Cluster** (`spark-2x-v1`) — two rented GB10 with RoCE between them. Signed lease exception.
3. **Leased B300** (`b300-v1`) — one rented Blackwell B300. Signed lease exception.

All three produce 1080p finish-intent masters. All three use DeepSeek as the brain. All three mount `/diony/vault`. All three run BRAIN / SPLIT / FARM / FINISH (layout differs). Producer is the same UI and the same gates.

Leased Dual-Spark and Leased B300 are **not** Dashscope, not hosted prompt APIs, not silent failovers, **not** a four-node lease (`lease-gpu-v1`), and **not** the desk crate. Four-node `lease-gpu-v1` is not offered in Wizard.

## Site is line 01

Wizard `path` is recorded first (**Continue**, not Sign). Until it is recorded, later lessons do not exist.

| Site | Profile | What the operator records |
|---|---|---|
| `local` | `spark-4x-v1` | Local Quad-Spark Cluster. |
| `dual` | `spark-2x-v1` | Provider, named human. GPU class is `2×GB10`. |
| `b300` | `b300-v1` | Provider, named human. GPU class is `1×B300`. |

`Project.compute` (`ClusterSite`) records the choice. Dual-Spark and B300 without `exception_by` are invalid. The local crate is never `spark-2x-v1` or `b300-v1`. Sign is only the last Wizard line (Producer).

## Compatibility (must stay isomorphic)

| Contract | Quad-Spark | Dual-Spark | Leased GPU | Leased B300 |
|---|---|---|---|---|
| Nodes | `spark-01`…`04` | `spark-01`, `spark-02` | `node-01`…`04` | `b300-01` |
| Fleet mode names | BRAIN / SPLIT / FARM / FINISH | Same | Same | Same names |
| SPLIT layout | 2 brain + 2 Wan | **Sequential** on the pair (drain ↔ load). Not 2+2. | 2+2 | **Sequential** on one GPU |
| BRAIN layout | TP=4 across four Sparks | **TP=2** on this pair | TP=4 across four matched nodes | **TP=1** on this GPU |
| Transitions | drain → unload → load → healthgate | Same | Same | Same |
| Vault | `/diony/vault` on 10GbE NAS | `/diony/vault` on private NFS | `/diony/vault` on private NFS/block/object | `/diony/vault` on private mount |
| Weights | `infra/models/manifest.yaml` | Same | Same | Same |
| Brain | DeepSeek-V4-Flash-0731 | Same. No Dashscope. | Same | Same |
| Camera | Wan2.2 720p negative | Same | Same | Same |
| Finish | Diony 1080 | Same | Same | Same |
| Producer | `http://producer.diony.local` | Same | Same | Same |
| Human gates | bible / picture / mix / distribution | Same | Same | Same |
| Wizard rail | Site → Unbox → … → Vault → Producer | Site → Connect → App → Producer | **Not a Wizard Site** | Site → Connect → App → Producer |
| Day 2 | dry-run fabric | **App Continue pulls** (pytest dry) | dry-run | **App Continue pulls** (pytest dry) |
| Serving file | `spark-4x-v1.serving.md` | `spark-2x-v1.serving.md` | `lease-gpu-v1.serving.md` | `b300-v1.serving.md` |

Spark serving flags are not GB300/H100 flags. Dual-Spark TP=2 is not Quad-Spark TP=4 and not B300 TP=1. Pin each profile separately (`04`).

Do not advertise Quad-Spark BRAIN 1M context on Dual-Spark or B300 until KV is measured. Do not quote the Quad-Spark 2–5 day 1-hour SLA on Dual-Spark or B300.

## What four-node `lease-gpu-v1` is

A profile for four matched accelerators (SPLIT 2+2, BRAIN TP=4). It is **not** a Wizard Site. Do not offer it as Where?. Dual-Spark is two GB10 (`dual`). A **single** B300 is Site `b300`.

## What Leased Dual-Spark is allowed to be

Two rented DGX Spark GB10 (256 GB unified) that share **ConnectX-7 RoCE**. Brain is the Blueprint 04 Spark **TP=2** recipe. Wan and brain **time-share** the pair: drain → unload → load. Same vault, same manifest, same Producer. Provider already owns the boxes; Wizard does not unbox a crate.

Isolated Sparks with no fabric between them **fail**. Co-resident DeepSeek + Wan A14B is **forbidden**. This is not a shrink of `spark-4x-v1`.

## What Leased B300 is allowed to be

One rented Blackwell B300 (or a matched GB300-class node) that can hold DeepSeek TP=1 and Wan by **mode switch**, not by pretending to be Quad-Spark. On-package interconnect. Same vault, same manifest, same Producer.

Co-resident DeepSeek + Wan A14B is **forbidden** until Blueprint 03 records a measured yes for this SKU.

## What every exception is forbidden to be

- Default (the desk crate is default)
- Failover when local health fails (Blueprint 08)
- Dashscope / hosted Qwen / OpenAI prompt extend
- One public VM with an open 8000
- Shipping previz as a master because the box is expensive
- Bouncing local ↔ dual ↔ B300 mid-production without a new Wizard report
- Mixing Spark TP=4 onto Dual-Spark, Spark flags onto B300, or B300 flags onto Spark
- Two Sparks with no CX-7 path (that is not Dual-Spark)

## Wizard stays the runbook

After Site is recorded, Wizard Files is enough to finish Day 2 prep. Do not send the operator to NVIDIA docs, MikroTik manuals, or Hugging Face CLI pages outside Diony.

The **rail depends on Site**:

| Site | Rail |
|---|---|
| Local Quad-Spark Cluster | Site → Unbox → Nodes → Cables → Switch → Sync → Fabric → Vault → Producer |
| Leased Dual-Spark Cluster | Site → Connect → App → Producer |
| Leased B300 | Site → Connect → App → Producer |

Lease rails do **not** unbox a crate, seat QSFP, force MikroTik 200G, enroll NVIDIA Sync, or dual-half iperf. The provider already did that. Dual-Spark still **records** that CX-7 RoCE exists between the pair.

**Connect** records hosts, SSH user, and a private vault. Dual-Spark: two hosts. B300: one host. Find the vault in the provider console (Storage / NFS / attached volume). Diony mounts it at `/diony/vault`. **App Continue** pulls the pinned pack (DeepSeek-V4-Flash-0731 + Wan2.2 A14B + `diony_sr_v1`) over SSH BatchMode or locally on spark-01, clones `/opt/wan22`, installs `diony-finish`, and starts `diony-producer` with `DIONY_VAULT=/diony/vault` and `DIONY_BIND=0.0.0.0`. Brain is auto: Dual-Spark TP=2, B300 TP=1. Sequential: do not start Wan and brain together. **Producer** confirms Idea / Dailies / Program / Lock. **Sign** writes `{vault}/.cluster/site.json` and locks the site pack. Then open Producer.

On Local Quad-Spark, the pack is recorded when Producer is continued. There is no Models page.

| Line | What Wizard serves |
|---|---|
| Connect (leases) | Plan card + vault fstab example (`infra/storage/vault.fstab.example`) |
| App (leases) | Pinned pack card. Continue pulls (SSH/local). How is the apply log + serving command. |
| Switch (local) | Snippet from `infra/switch/` |
| Vault (local) | `infra/storage/vault.fstab.example` |
| Producer | Site pack: profile + fstab + manifest + serving notes + Producer URL + node list |

Day 2 still does not mutate provider fabric. App Continue **does** SSH and pull weights when `wizard_live()` (not pytest). The pack is not a printed homework sheet.

## Wizard lines after Site

| ID | Local Quad-Spark | Leased Dual-Spark | Leased B300 |
|---|---|---|---|
| `unbox` | Crate before power, UPS | *not on rail* | *not on rail* |
| `connect` | *not on rail* | Two hosts + vault. CX-7 RoCE. | One host + vault. On-package. |
| `inventory` | Spark serials | *filled by Connect* | *filled by Connect* |
| `cables` | CX-7 port 2 + 10GbE | *not on rail* | *not on rail* |
| `switch` | MikroTik 200G snippets | *not on rail* | *not on rail* |
| `sync` | NVIDIA Sync, four fabric IPs | *not on rail* | *not on rail* |
| `roce` | Dual-half iperf | *not on rail* (pair fabric is a Connect fact) | *not on rail* |
| `vault` | NAS NFS | *filled by Connect* | *filled by Connect* |
| `weights` | *filled by Producer* (TP=4) | *filled by App* (TP=2) | *filled by App* (TP=1) |
| `brain_smoke` | *filled with the pack* | *filled with the pack* | *filled with the pack* |
| `app` | *same install on Producer* | Continue pulls pack + starts `diony-producer` | Continue pulls pack + starts `diony-producer` |
| `producer` | Floors + **Sign** | Floors + **Sign** | Floors + **Sign** |

## Failure modes

| Symptom | Cause | Action |
|---|---|---|
| Cloud-by-default | Site skipped or pre-ticked a lease | Site is unsigned until a human picks; leases need a name |
| Dashscope leftover | `DASH_API_KEY` on the box | Fail health. Brain is DeepSeek on this site |
| 50G interconnect | Autoneg / cheap SKU | Fail. 50G is not a Diony cluster |
| Dual-Spark TP=4 | Pasted Quad-Spark BRAIN flags | Use `spark-2x-v1.serving.md` TP=2 |
| Spark flags on B300 | Pasted Blueprint 04 Spark recipe | Use `b300-v1.serving.md` TP=1 |
| One host on Dual-Spark | Copied B300 Connect | Fail `connect`. This site is two Sparks |
| Two isolated Sparks | Provider LAN only, no CX-7 | Fail. TP=2 will not load |
| Four hosts on B300 | Copied a four-node lease | Fail `connect`. This site is one GPU |
| Skipped App on a lease | Connect used to auto-fill weights | Fail until App Continue pulls the site pack |
| Concurrent brain+Wan on dual or B300 | Copied desk SPLIT | Plan is sequential until measured |
| Public bucket bible | Object store left open | Fail `connect`. Vault is private |
| Mid-production bounce | Local died, jobs sent to a lease | Forbidden. New Site, new report, human gate |

## Day 2 vs Week 2+

Laptop pytest is dry-run (`DIONY_DRY_RUN=1`): no SSH, no Hugging Face, no fabric. On a Dual-Spark or B300 with `DIONY_WIZARD_LIVE=1` (or `/models` present), App Continue SSHes as `DIONY_SSH_USER`, pulls the pack including `diony_sr_v1`, clones Wan2.2, starts Producer. Sign writes `site.json`. Produce farms real Wan2.2 720p clips. Picture lock runs `diony-finish` to `takes/{id}/finish_1080.mov` and `program/{timeline}/master_1080.mov`. Fabric netplan remains Week 2+.
