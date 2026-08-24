# Blueprint 14 — Compute paths (Quad-Spark, Dual-Spark, B300, B200)

Status: Day 2  
Owner: Diony AI  
Implements: Studio Site (`path`) and profiles `spark-4x-v1`, `spark-2x-v1`, `b300-v1`, `b200-v1`. `lease-gpu-v1` remains in infra and is **not** a Studio Site. Does not replace Blueprint 01.

## Goal

The same Diony product on four Studio sites (one crate, **three leases**):

1. **Local Quad-Spark Cluster** (`spark-4x-v1`) — four GB10 on the floor. Default. Never pre-select a lease.
2. **Leased Dual-Spark Cluster** (`spark-2x-v1`) — two rented GB10 with interconnect between them. Signed lease exception. The COMPUTE agent uses Enverge (`https://spark.enverge.ai/`) SKU **2× NVIDIA DGX Spark**. A Runpod Instant Cluster remains an expert/manual recovery path, not the one-touch default.
3. **Leased B300** (`b300-v1`) — one rented Blackwell B300 (288 GB class). Signed lease exception. Catalog: Runpod Deploy **NVIDIA B300** first; Enverge Cloud GPU Options SKU **NVIDIA B300** second.
4. **Leased B200** (`b200-v1`) — one rented Blackwell B200 (**180 GB**). Signed lease exception. Catalog: Runpod Deploy **NVIDIA B200** first; Enverge Cloud GPU Options SKU **NVIDIA B200** second. Not a drop-in B300.

All four produce 1080p finish-intent masters. All four use DeepSeek as the brain. All four mount `/diony/vault`. All four run BRAIN / SPLIT / FARM / FINISH (layout differs). Producer is the same UI and the same gates.

Leased Dual-Spark, Leased B300, and Leased B200 are **not** Dashscope, not hosted prompt APIs, not silent failovers, **not** a four-node lease (`lease-gpu-v1`), and **not** the desk crate. Four-node `lease-gpu-v1` is not offered in Studio. A single DGX Spark GB10, RTX Pro 6000, H100, H200, and a B200 Instant Cluster are **not** Studio Sites. B200 is a Studio Site only as **one** GPU (`b200-v1`), never as Dual-Spark.

## COMPUTE is line 01

Studio `path` is recorded first (**Continue**, not Sign). The operator sees one Local group and one Lease group. The option labels are **DGX Quad-Spark Cluster**, **Enverge DGX Dual-Spark Cluster**, **Runpod NVIDIA B300**, and **Runpod NVIDIA B200**; they do not repeat Local or Leased because the group headings already carry that meaning. The Local card previews both physical networks before confirmation: Spark 01–04 CX-7 port 2 → QSFP 200G → MikroTik fabric, and every Spark RJ-45 → Cat6 → 10GbE management with Studio and NAS/vault. Selecting an option opens **Confirm option?**; Continue starts the COMPUTE setup agent. Until the choice is recorded, later lessons do not exist.

| Site | Profile | What the operator records |
|---|---|---|
| `local` | `spark-4x-v1` | Local Quad-Spark Cluster. |
| `dual` | `spark-2x-v1` | Provider (Runpod or Enverge), named human. GPU class is `2×GB10`. |
| `b300` | `b300-v1` | Provider (Runpod or Enverge), named human. GPU class is `1×B300`. |
| `b200` | `b200-v1` | Provider (Runpod or Enverge), named human. GPU class is `1×B200`. |

`Project.compute` (`ClusterSite`) records the choice. Dual-Spark, B300, and B200 without `exception_by` are invalid. The local crate is never `spark-2x-v1`, `b300-v1`, or `b200-v1`. Sign is only the last Studio line (Producer).

## COMPUTE setup agent

The lease agent owns provider acquisition through Studio handoff. Its progress state is resumable and explicit: option confirmed → provider account → capacity and price/allocation → GPU and storage → Studio handoff. A user checkpoint is not an error. It names the exact action and preserves the completed steps.

Runpod B300/B200 use supported provider APIs. Diony checks the live GPU catalog and price before the billing confirmation, then creates one on-demand Pod with the exact GPU ID, one GPU, official Runpod PyTorch image, SSH TCP, container disk **800 GB**, persistent Pod volume **1000 GB** mounted at `/workspace`, and the matching `b300-v1` or `b200-v1` profile. The 800 GB floor holds the pinned ~628 GB model pack, image/runtime layers, and dispatch headroom; 400–600 GB can pull successfully and still strand Production below its safety floor. After the pinned pull, Studio verifies byte-identical shared Wan T5/VAE assets and hard-links only exact matches, preserving every model path while reclaiming duplicate overlay storage. It polls the Pod until `publicIp` and the port mapping for container port `22` exist, records those values into Connect, and continues through the existing idempotent **Check Site** engine. B300 API GPU ID is `NVIDIA B300 SXM6 AC`; B200 is `NVIDIA B200`.

Enverge Dual-Spark currently has no published provisioning API. Diony opens the Enverge Spark account/allocation surface and pauses for the human-owned sign-in, billing, terms, CAPTCHA/2FA, and **2× NVIDIA DGX Spark** allocation. When Enverge issues the two fabric-connected SSH hosts and private storage address, the operator records that provider result once; Diony validates exactly two hosts and takes over Connect, the pinned pack, vault, and Producer setup. The UI must never imply that this provider checkpoint was automated.

Provider keys and `HF_TOKEN` are password inputs. Provider keys live in process memory only and are cleared after Studio handoff/reset. Neither secret may appear in public API state, Activity, workbook, `site.json`, signed reports, or error text. Studio never creates or displays a provider password. Provider account recovery and credentials remain in the provider/password manager.

### Connect to Existing Compute

The bottom of both the Local and Lease groups exposes **Connect to Existing Compute**. This is discovery, not a shortcut that trusts a typed profile. The operator supplies only SSH hosts, user, and port; a lease also records its provider and named responsible human. The private vault is not a required form value. Diony identifies it through that read-only SSH connection, preferring the mounted `/diony/vault` source (including private NFS) and then `/workspace`, and records the detected source into Connect. If no private storage is visible—or clustered hosts resolve different sources—the agent stops with a named vault check instead of asking the operator to guess an address. Diony runs the same read-only probe on every host and identifies only these supported shapes:

- Local: exactly four NVIDIA GB10 / DGX Spark hosts with visible CX-7/RDMA fabric → `spark-4x-v1`.
- Lease: two NVIDIA GB10 / DGX Spark hosts with visible fabric → `spark-2x-v1`.
- Lease: one NVIDIA B300/GB300 → `b300-v1`.
- Lease: one NVIDIA B200 → `b200-v1`.

Discovery checks SSH, GPU identity, node count, fabric where multi-node, `/diony/vault` or `/workspace`, absence of Dashscope keys, DeepSeek + Wan + `diony_sr_v1`, the Wan runtime, and Producer. Unsupported hardware, isolated Sparks, a public vault, a missing host, or a cloud prompt key stops the agent with a named failed check. The user never chooses the detected profile.

If the pack is already prepared, no Hugging Face token is requested and no model pull runs. If the pack or runtime is missing, Studio requests a masked `HF_TOKEN`, runs the same idempotent Check Site/apply engine, installs only the missing pieces, and replicates `/models` to the other Spark hosts. Long SSH checks are serialized so polling cannot launch duplicate validation or repair work. While validation or repair is running, the Setup Agent owns the action area: a steady **Setup in progress** element replaces the Ready-page Continue control. The Continue control appears only after polling has stopped with every required check passed; it then opens Producer. The agent never flashes an inactive Continue between status polls, and it never signs or offers Producer after a failed check.

## Compatibility (must stay isomorphic)

| Contract | Quad-Spark | Dual-Spark | Leased GPU | Leased B300 | Leased B200 |
|---|---|---|---|---|---|
| Nodes | `spark-01`…`04` | `spark-01`, `spark-02` | `node-01`…`04` | `b300-01` | `b200-01` |
| Fleet mode names | BRAIN / SPLIT / FARM / FINISH | Same | Same | Same names | Same names |
| SPLIT layout | 2 brain + 2 Wan | **Sequential** on the pair (drain ↔ load). Not 2+2. | 2+2 | **Sequential** on one GPU | **Sequential** on one GPU |
| BRAIN layout | TP=4 across four Sparks | **TP=2** on this pair | TP=4 across four matched nodes | **TP=1** on this GPU | **TP=1** on this GPU |
| `max_model_len` | 1048576 | 1048576 | datacenter pin | 1048576 (tune on OOM) | **16384** (180 GB, tight KV) |
| `gpu-memory-utilization` | Spark pin | 0.85 | datacenter pin | 0.90 | **0.95** |
| Transitions | drain → unload → load → healthgate | Same | Same | Same | Same |
| Vault | `/diony/vault` on 10GbE NAS | `/diony/vault` on private NFS | `/diony/vault` on private NFS/block/object | `/diony/vault` on private mount | `/diony/vault` on private mount |
| Weights | `infra/models/manifest.yaml` | Same | Same | Same | Same |
| Brain | DeepSeek-V4-Flash-0731 | Same. No Dashscope. | Same | Same | Same |
| Camera | Wan2.2 720p negative | Same | Same | Same | Same |
| Finish | Diony 1080 | Same | Same | Same | Same |
| Producer | `http://producer.diony.local` | Same | Same | Same | Same |
| Human gates | bible / picture / mix / distribution | Same | Same | Same | Same |
| Studio rail | Site → Unbox → … → Vault → Producer | Site → Connect → App → Ready → Producer | **Not a Studio Site** | Site → Connect → App → Ready → Producer | Site → Connect → App → Ready → Producer |
| Day 2 | dry-run fabric | **App Continue pulls** (pytest dry) | dry-run | **App Continue pulls** (pytest dry) | **App Continue pulls** (pytest dry) |
| Serving file | `spark-4x-v1.serving.md` | `spark-2x-v1.serving.md` | `lease-gpu-v1.serving.md` | `b300-v1.serving.md` | `b200-v1.serving.md` |

Spark serving flags are not GB300/H100 flags. Dual-Spark TP=2 is not Quad-Spark TP=4 and not B300/B200 TP=1. B200 is not B300: weights ~155.4 GiB vs 180 GB VRAM. Pin each profile separately (`04`).

Do not advertise Quad-Spark BRAIN 1M context on Dual-Spark, B300, or B200 until KV is measured. Do not paste B300 `1048576` onto B200. Do not quote the Quad-Spark 2–5 day 1-hour SLA on Dual-Spark, B300, or B200.

## What four-node `lease-gpu-v1` is

A profile for four matched accelerators (SPLIT 2+2, BRAIN TP=4). It is **not** a Studio Site. Do not offer it as Where?. Dual-Spark is two GB10 (`dual`). A **single** B300 is Site `b300`. A **single** B200 is Site `b200`.

## What Leased Dual-Spark is allowed to be

Two rented DGX Spark GB10 (256 GB unified) that share **high-speed interconnect**. The COMPUTE agent opens Enverge Spark (`https://spark.enverge.ai/`): choose **2× NVIDIA DGX Spark** (node0 = spark-01, node1 = spark-02) and private persistent storage. Do not launch two isolated Sparks. A Runpod Instant Cluster with **2 nodes**, cluster fabric, and a 1000 GB Network Volume remains an expert/manual recovery path. Brain is the Blueprint 04 Spark **TP=2** recipe. Wan and brain **time-share** the pair: drain → unload → load. Same vault, same manifest, same Producer. Provider already owns the boxes; Studio does not unbox a crate.

Isolated Sparks or isolated Runpod Pods with no fabric between them **fail**. Co-resident DeepSeek + Wan A14B is **forbidden**. This is not a shrink of `spark-4x-v1`. Do not record an H100 / H200 / B200 Instant Cluster as Dual-Spark.

## What Leased B300 is allowed to be

One rented Blackwell B300 (or a matched GB300-class node) that can hold DeepSeek TP=1 and Wan by **mode switch**, not by pretending to be Quad-Spark. Launch from Runpod (`https://www.runpod.io/`) first: Pods → Deploy → GPU **NVIDIA B300**, GPU count **1**, On-Demand, official Runpod PyTorch, SSH on. Storage on that page: **Container disk** ≥800 GB (`/models`; wiped on Stop — do not leave 30 GB). **Persistent storage** = **Volume disk** ≥1000 GB at `/workspace` (survives Stop; **Terminate deletes the vault**). Skip **Network volume** unless it is already in this GPU’s exact data center. Dual-Spark still needs a shared Network Volume. If not Runpod, Enverge Cloud GPU Options: SKU **NVIDIA B300**. On-package interconnect. Same vault, same manifest, same Producer.

Co-resident DeepSeek + Wan A14B is **forbidden** until Blueprint 03 records a measured yes for this SKU. Picking **Leased B200** for a B300 pod is a fail.

## What Leased B200 is allowed to be

One rented Blackwell B200 (**180 GB** VRAM). Not B300 (288 GB). Not Dual-Spark. Not a B200 Instant Cluster. Launch from Runpod first: Pods → Deploy → GPU **NVIDIA B200**, GPU count **1**, On-Demand, official Runpod PyTorch, SSH on. Storage: **Container disk** ≥800 GB (`/models`; Stop wipes it). **Persistent storage** = **Volume disk** ≥1000 GB at `/workspace` (Stop keeps it; Terminate deletes it). Skip Network Volume unless it is already in this B200’s exact data center. Enverge second: SKU **NVIDIA B200**.

Brain is TP=1 with a tight KV pin: `max_model_len 16384`, `gpu-memory-utilization 0.95`. Weights are **166,878,536,440 bytes** (~155.4 GiB). Do not paste `b300-v1` `1048576` onto this card. Sequential SPLIT: drain → unload → load. Never co-resident DeepSeek + Wan. Studio App does not start vLLM. Same pack, same `/diony/vault`, same Producer.

Picking **Leased B300** for a B200 pod is a fail.

## Runpod → Studio Connect (B200 and B300 fallback)

The COMPUTE agent normally reads `publicIp`, the mapped TCP port for container port `22`, and `/workspace` from the Runpod API and records Connect automatically. The manual map below is the recovery path if a Pod was created outside Studio. App Continue rsyncs; the proxy SSH line does not support SCP/SFTP. START HERE §5 (B300) / §6 (B200).

Worked example (your IP and `-p` will differ — do not treat these as defaults):

```
ssh root@198.13.252.68 -p 28231 -i ~/.ssh/id_ed25519
```

| RunPod console | Studio |
|---|---|
| GPU you Deployed (Details: 1x B200 or 1x B300) | Site **Leased B200** or **Leased B300**. Must match. |
| Company | Provider: `Runpod`. Named human on Your name. |
| Connect → **HTTP services** (Port 8888 Jupyter) | Skip. Not a Diony box. |
| Connect → **SSH** `ssh LOGIN@ssh.runpod.io` | Skip as Host. Proxy, no SCP. LOGIN is often not `root`. |
| Connect → **SSH over exposed TCP** `ssh root@IP -p PORT -i ~/.ssh/id_ed25519` | **Use this.** Paste the full line into **Runpod quick connect**; Studio separates Host, SSH user, SSH port, and identity. Manual entry remains available below. |
| Connect → **Web terminal** | Skip. Leave off. |
| Connect → **Direct TCP ports** `SSH → IP:PORT → :22` | Same Host and SSH port as the TCP line. |
| Details → Pod volume Mount path `/workspace` | Vault: `/workspace` |
| Network Volume in this GPU’s DC | Vault: that volume’s NFS/path. START HERE: skip unless already in that exact DC. |
| Container disk `/models` | Weights. Stop wipes. **Not** the vault. |
| Template Runpod PyTorch | Expected class. Location and cost are not Studio fields. |
| Identity `~/.ssh/id_ed25519` | Public key from START HERE → RunPod Settings. |

Paste the whole `ssh root@IP -p PORT -i KEY` line only into **Runpod quick connect**. Never paste it into one manual field or use the account email as SSH user. The identity path remains process-local and is excluded from public state and the signed report. Stop keeps Volume disk. Terminate deletes Volume disk. Diony always mounts the vault at `/diony/vault`.

**App Check Site** SSHes as `DIONY_SSH_USER` on `DIONY_SSH_PORT` with `DIONY_SSH_KEY` (BatchMode), or runs locally on this GPU. Before any pull it checks every pinned DeepSeek/Wan directory, `diony_sr_v1`, `/opt/wan22`, and Producer state. A complete pack skips Hugging Face. If Producer is stopped, Diony starts it without pulling models; on Dual-Spark, an existing primary pack may be replicated to a missing second node. Only missing primary-pack content enters setup. Hugging Face token is `HF_TOKEN`: open https://huggingface.co/settings/tokens, Create new token +, paste it on App or Ready. Setup pulls DeepSeek-V4-Flash-0731 + Wan2.2 A14B + `diony_sr_v1`, clones `/opt/wan22`, installs `diony-finish`, and starts `diony-producer`. The compact state stays visible; the full apply log is under Activity. App does not start vLLM. Sequential: never brain and Wan together.

**Sign** writes `{vault}/.cluster/site.json` including Connect SSH (`host`, `ssh_user`, `ssh_port`). `gpu_ready` from Ready Test GPU means the site is live even if this desk has no `/models`. Then open `http://producer.diony.local`. That handoff replaces the Studio document and stops its background apply poll; returning or refreshing a signed Studio must show the signed report, never flash or restore the unsigned Start / Join entry sheet. Desk Producer is the UI; Wan **and** `diony_sr_v1` finish farm on GPU Producer through that SSH line. Do not score the 1080 pack against the laptop. After a pod restart, Studio Connect is the live TCP line — Producer must not keep a dead tunnel to the previous IP/`-p`.

## What every exception is forbidden to be

- Default (the desk crate is default)
- Failover when local health fails (Blueprint 08)
- Dashscope / hosted Qwen / OpenAI prompt extend
- One public VM with an open 8000
- Shipping previz as a master because the box is expensive
- Bouncing local ↔ dual ↔ B300 ↔ B200 mid-production without a new Studio report
- Mixing Spark TP=4 onto Dual-Spark, Spark flags onto B300/B200, B300 `1048576` onto B200, or B200 16k onto B300 as if they were the same card
- Two Sparks with no CX-7 path, or two Runpod Pods with no Instant Cluster fabric (that is not Dual-Spark)

## Studio stays the runbook

After Site is recorded, Studio Files is enough to finish Day 2 prep. Do not send the operator to NVIDIA docs, MikroTik manuals, or Hugging Face CLI pages outside Diony.

The **rail depends on Site**:

| Site | Rail |
|---|---|
| Local Quad-Spark Cluster | Compute → Unbox → Nodes → Cables → Switch → Sync → Fabric → Vault → Producer |
| Leased Dual-Spark Cluster | Compute → agent → App → Ready → Producer |
| Leased B300 | Compute → agent → App → Ready → Producer |
| Leased B200 | Compute → agent → App → Ready → Producer |

Lease rails do **not** unbox a crate, seat QSFP, force MikroTik 200G, enroll NVIDIA Sync, or dual-half iperf. The provider already did that. Dual-Spark still **records** that interconnect exists between the pair (Runpod Instant Cluster fabric, or Enverge CX-7 RoCE).

**Connect** records hosts, SSH user (the GPU login — the word before `@`), SSH port on B200/B300 (optional, default 22; RunPod TCP `-p`), and a private vault. Existing Lease Compute offers **Runpod quick connect** first: it accepts the exact **SSH over exposed TCP** command, rejects proxy/Jupyter/remote-command input, separates host/user/port/identity, and feeds those values into the same inspection and apply path. The identity file is used by SSH/rsync but excluded from public state and the signed report. Manual fields remain below. The confirmation line below those fields is computed from their current values on every keystroke, before save or Continue; it must not display stale host, user, port, or vault data. Dual-Spark: two hosts. B300 / B200: one host. Prefer RunPod **SSH over exposed TCP** for App Continue (rsync). Field map is above. Diony mounts the vault at `/diony/vault`. **App Continue** starts that apply (share at `/diony/vault`, pinned pack, `/opt/wan22`, `diony-finish`, `diony-producer`) and opens Ready without waiting for Hugging Face. Brain is auto: Dual-Spark TP=2, B300 TP=1 (`max_model_len` 1048576), B200 TP=1 (`max_model_len` **16384**). Sequential: do not start Wan and brain together. **Ready** **Test GPU** SSHes the Connect line and reads `nvidia-smi` (must match Site). Pytest never opens a socket. A real IP requires a green Test GPU before Continue. **Producer** confirms Idea / Production / Edit / Gallery without a redundant product-URL field or reachability quiz. The fixed address is recorded automatically. **Sign** writes `{vault}/.cluster/site.json` on the Studio process vault and copies it to `/diony/vault/.cluster/site.json` on the GPU when live. Then open Producer (`http://producer.diony.local`).

On Local Quad-Spark, the pack is recorded when Producer is continued. There is no Models page.

| Line | What Studio serves |
|---|---|
| Connect (leases) | Plan card + vault fstab example (`infra/storage/vault.fstab.example`) |
| App (leases) | **Check Site** probes first. Complete pack: skip pull. Incomplete pack: pull missing setup and open Ready. Plan and commands are under Details; full log is under Activity. |
| Ready (leases) | Test GPU checklist. Pack pull in progress (bar + latest apply line + elapsed) while Hugging Face runs. SSH + `nvidia-smi` must match Site. Pack red → **Pull pack** on this line, then Test GPU. |
| Switch (local) | Snippet from `infra/switch/` |
| Vault (local) | `infra/storage/vault.fstab.example` |
| Producer | Site pack: profile + fstab + manifest + serving notes + Producer URL + node list |

Day 2 still does not mutate provider fabric. App Continue and Ready **Pull pack** SSH and pull weights when `wizard_live()` **or** Connect Host is a real IP (laptop desk still has `DIONY_DRY_RUN=1`). Pytest never SSHes. The pack is not a printed homework sheet.

## Studio lines after Site

| ID | Local Quad-Spark | Leased Dual-Spark | Leased B300 | Leased B200 |
|---|---|---|---|---|
| `unbox` | Crate before power, UPS | *not on rail* | *not on rail* | *not on rail* |
| `connect` | *not on rail* | Two hosts + vault. Cluster fabric. | One host + SSH port + vault. On-package. | One host + SSH port + vault. On-package. 180 GB. |
| `inventory` | Spark serials | *filled by Connect* | *filled by Connect* | *filled by Connect* |
| `cables` | CX-7 port 2 + 10GbE | *not on rail* | *not on rail* | *not on rail* |
| `switch` | MikroTik 200G snippets | *not on rail* | *not on rail* | *not on rail* |
| `sync` | NVIDIA Sync, four fabric IPs | *not on rail* | *not on rail* | *not on rail* |
| `roce` | Dual-half iperf | *not on rail* (pair fabric is a Connect fact) | *not on rail* | *not on rail* |
| `vault` | NAS NFS | *filled by Connect* | *filled by Connect* | *filled by Connect* |
| `weights` | *filled by Producer* (TP=4) | *filled by App* (TP=2) | *filled by App* (TP=1) | *filled by App* (TP=1, 16k) |
| `brain_smoke` | *filled with the pack* | *filled with the pack* | *filled with the pack* | *filled with the pack* |
| `app` | *same install on Producer* | Continue pulls pack + starts `diony-producer` | Continue pulls pack + starts `diony-producer` | Continue pulls pack + starts `diony-producer` |
| `prove` | *not on rail* | **Test GPU** (`nvidia-smi` over SSH) | **Test GPU** (must be B300) | **Test GPU** (must be B200, 180 GB) |
| `producer` | Floors + **Sign** | Floors + **Sign** | Floors + **Sign** | Floors + **Sign** |

## Failure modes

| Symptom | Cause | Action |
|---|---|---|
| Cloud-by-default | Site skipped or pre-ticked a lease | Site is unsigned until a human picks; leases need a name |
| Dashscope leftover | `DASH_API_KEY` on the box | Fail health. Brain is DeepSeek on this site |
| 50G interconnect | Autoneg / cheap SKU | Fail. 50G is not a Diony cluster |
| Dual-Spark TP=4 | Pasted Quad-Spark BRAIN flags | Use `spark-2x-v1.serving.md` TP=2 |
| Spark flags on B300/B200 | Pasted Blueprint 04 Spark recipe | Use `b300-v1.serving.md` or `b200-v1.serving.md` TP=1 |
| B300 1M on B200 | Pasted `max_model_len 1048576` onto 180 GB | Use `16384` and `gpu-memory-utilization 0.95` |
| B200 vs B300 site | Picked the other Blackwell GPU | Fail Site. Match the GPU you Deployed |
| One host on Dual-Spark | Copied B300/B200 Connect | Fail `connect`. This site is two Sparks |
| Two isolated Sparks | Provider LAN only, no cluster fabric, two isolated Runpod Pods, or two single GB10 | Fail. TP=2 will not load. Launch a 2-node Instant Cluster or **2× DGX Spark GB10** |
| Wrong lease SKU | RTX / H100 / H200 / single GB10 | Fail Site. Dual-Spark is a 2-node GB10 pair. B300 is one B300. B200 is one B200 |
| B200 Instant Cluster as Dual-Spark | Recorded a B200 cluster as `dual` | Fail. Dual-Spark is GB10. B200 Instant Cluster is not a Studio Site |
| Runpod Storage defaults | Container 30 GB, Volume disk 50 GB, Network Volume 10 GB | Fail. Container disk ≥800 GB. Persistent storage = Volume disk ≥1000 GB (B200/B300) or shared Network Volume ≥1000 GB (Dual-Spark) |
| Runpod Network Volume not in GPU data center | Volume DC ≠ this GPU’s DC | B200/B300: Volume disk ≥1000 GB at `/workspace` (Terminate deletes it). Dual-Spark still needs a shared Network Volume |
| Four hosts on B300 or B200 | Copied a four-node lease | Fail `connect`. This site is one GPU |
| Pasted `ssh root@IP -p PORT -i KEY` | Whole Runpod TCP command | Use **Runpod quick connect**. It separates Host / user / port / identity. Manual fields still take one value each |
| ssh.runpod.io as Host | Used proxy SSH (no SCP) | Fail. Host is the IP before `-p` on SSH over exposed TCP |
| App rsync on port 22 | TCP SSH maps another `-p` | Set SSH port to that number. Not Jupyter 8888 |
| Connection refused on SSH port | Pod Stopped or RunPod remapped TCP IP/`-p` | Resume the pod. Open Connect. Copy SSH over exposed TCP. Pull pack |
| Skipped App on a lease | Connect used to auto-fill weights | Fail until App Continue pulls the site pack |
| Ready red row | Wrong GPU, SSH refused, Dashscope set, missing pack | Pack: Pull pack on Ready, then Test GPU. Do not Sign |
| `mount: /diony/vault: permission denied` | Pod denies bind onto `/diony/vault` | Symlink `/workspace` → `/diony/vault`. Do not NFS-mount a Volume disk path |
| `externally-managed-environment` | Debian PEP 668 on the official RunPod image | `$DIONY_REPO/.venv`, then `--break-system-packages`. Not `apt install python3-xyz` for Diony |
| Concurrent brain+Wan on dual / B300 / B200 | Copied desk SPLIT | Plan is sequential until measured |
| Public bucket bible | Object store left open | Fail `connect`. Vault is private |
| Mid-production bounce | Local died, jobs sent to a lease | Forbidden. New Site, new report, human gate |

## Day 2 vs Week 2+

Laptop pytest is dry-run (`DIONY_DRY_RUN=1`): no SSH, no Hugging Face, no fabric. On Dual-Spark, B300, or B200 with `DIONY_WIZARD_LIVE=1` (or `/models` present), App Continue SSHes as `DIONY_SSH_USER` on `DIONY_SSH_PORT` (default 22), puts vault at `/diony/vault` (bind or `/workspace` symlink), uses a venv (PEP 668 fallback `--break-system-packages`), pulls the pack including `diony_sr_v1`, clones Wan2.2, starts Producer. Sign writes `site.json`. Produce farms real Wan2.2 720p clips. Picture lock runs `diony-finish` to `takes/{id}/finish_1080.mov` and `program/{timeline}/master_1080.mov`. Fabric netplan remains Week 2+.
