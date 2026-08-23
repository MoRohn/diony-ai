# Blueprint 01 — Crate to cluster

Status: Day 2 locked  
Studio implements this as a step machine. Fabric mutation (netplan, Sync) stays dry-run on a laptop. **Leased Dual-Spark / B300 / B200 App Continue is live:** SSH (or local on the GPU), pull the pinned pack, clone Wan2.2, install diony-finish, start Producer. Pytest sets `DIONY_DRY_RUN=1` and never SSHes.

## Goal

Site → unbox → four healthy nodes on fabric → Diony overlay → Producer URL.

On a laptop or spark-01, first software open is **START HERE.txt** then **Open Diony AI** (Blueprint 11). START HERE names the hardware: Local Quad-Spark crate, or a lease from Runpod first (`https://www.runpod.io/`) then Enverge (`https://enverge.ai/`) for Dual-Spark, B300, and B200. No `pip` in that path.

**Local Quad-Spark Cluster** (`spark-4x-v1`) is the SKU this Blueprint cabling describes. **Leased Dual-Spark** (`spark-2x-v1`), **Leased B300** (`b300-v1`), and **Leased B200** (`b200-v1`) skip Unbox/Cables/Switch/Sync/Fabric — software rail in Blueprint 14. Do not treat a lease as the default. `lease-gpu-v1` is not a Studio Site.

NVIDIA Sync Cluster Assistant configures ConnectX-7, netplan, SSH trust, and `ib_write_bw` / `ib_write_lat` on the desk. Diony does not reimplement that. The Studio **wraps** it. On a lease, the fabric owner is Sync **or** the provider; Diony still does not replace them.

## Physical checklist (before power)

- [ ] Spark Cluster (4) (or matched GB10 OEM: ASUS Ascent GX10 / MSI EdgeXpert / Founders). Same DGX OS train.
- [ ] Four 240 W bricks seated; PDU/UPS live
- [ ] Fabric switch: CRS804-4DDQ-HRM **or** CRS812-8DS-2DQ-2DDQ
- [ ] QSFP-DD ↔ Spark cables seated **to Spark port 2** (standardize; do not mix ports)
- [ ] 10GbE Cat6 from each Spark RJ-45 to management switch
- [ ] NAS on 10GbE (LACP if two NICs)
- [ ] One HDMI or USB-C display on **spark-01** for first boot (or Sync from a laptop)
- [ ] Switch management port on the 10GbE network

### Cable map (canonical)

```
spark-01 CX7 port2  ----QSFP----  switch fabric port A
spark-02 CX7 port2  ----QSFP----  switch fabric port B
spark-03 CX7 port2  ----QSFP----  switch fabric port C
spark-04 CX7 port2  ----QSFP----  switch fabric port D

spark-01..04 RJ-45  ----Cat6----  mgmt switch
NAS 10GbE           ----Cat6----  mgmt switch
Studio laptop       ----Cat6----  mgmt switch
```

Breakout variant (CRS812): two QSFP-DD→2×QSFP56 DACs. Switch port 1 → spark-01 + spark-02. Switch port 2 → spark-03 + spark-04.

Label every cable both ends: `S1-P2`, `S2-P2`, …

## Studio steps (product order)

| Step id | Name | Pass criteria |
|---|---|---|
| `path` | Site | Local Quad-Spark, Leased Dual-Spark, Leased B300, or Leased B200. Leases record a named human (14). |
| `connect` | Hosts + vault | Lease only. Dual-Spark: two hosts + cluster fabric (Runpod Instant Cluster, or Enverge CX-7 RoCE). B300 / B200: one host + vault (Runpod field map in 14), then App. |
| `unbox` | Crate on the desk, before power | Local crate only. Dual-Spark / B300 / B200 skip this line (14). |
| `inventory` | Serials / instance IDs, matched OS | Desk: four Sparks. Lease is filled by Connect. |
| `cables` | Operator confirms cable / fabric map | Local crate only. Dual-Spark / B300 / B200 skip this line (14). |
| `switch` | Advertised class forced, autoneg off | Local: MikroTik 200G snippets. Dual-Spark / B300 / B200 skip this line (14). |
| `sync` | Fabric owner reports healthy | Local: NVIDIA Sync. Dual-Spark / B300 / B200 skip this line (14). |
| `roce` | Diony health: full fabric class + NCCL | Local crate only. See Blueprint 02. |
| `vault` | Mount `/diony/vault` | Same path on every site |
| `weights` | Pack (not a Studio page) | Pinned DeepSeek 0731 + Wan2.2 A14B + `diony_sr_v1`. Recorded on App (leases) or Producer (desk). Brain recipe auto. Dual/B300/B200 App **pulls** it. |
| `app` | Pack + Producer running | Dual-Spark, B300, and B200: Continue mounts `/diony/vault`, pulls, starts `diony-producer` (14). Desk records this on Producer. |
| `producer` | Print Producer URL | Floors: Idea, Production, Gallery. Sign writes `site.json`. Open `http://producer.diony.local`. |

Laptop pytest is `dry-run`. Live Dual-Spark/B300/B200 App pulls weights. The Studio starts at **Site**. On the local path, do not power Spark Cluster (4) until **Unbox** is signed. Fabric netplan remains Week 2+.

## NVIDIA Sync handoff

Do **not** script Sync internals. The Studio shows:

1. Open NVIDIA Sync on a machine that already enrolled Spark Cluster (4).
2. Cluster Assistant: readiness (OTA, sudo) → CX-7 topology probe → IP plan → netplan → `ib_write_bw` / `ib_write_lat` → SSH over CX-7.
3. Paste Sync’s cluster name + node IPs into the Studio.
4. Continue to Diony RoCE gates (those are ours).

Official refs (operators follow NVIDIA’s current docs, not a fork):

- DGX Spark clustering guide
- NVIDIA Sync Cluster Assistant
- NVIDIA blog: multi-node clustering on DGX Spark (Computex 2026 / June 2026 software)

## Switch: force 200G

Autoneg often lands at **50G**. That is a fail.

CRS804: set each connected QSFP-DD port to 200G in RouterOS / Winbox.  
CRS812 breakout: `breakout-mode=2x200G`, then disable autoneg on the 200G legs, `speed=200Gbps`, `mtu=9000`, `fec=rs-fec`.

Exact snippets: `infra/switch/crs804-200g.rsc` and `infra/switch/crs812-200g.rsc`.

## Power budget (desk)

| Item | Typical |
|---|---|
| 4× Spark PSU | 4×240 W = 960 W ceiling |
| CRS804 | ~92 W typical, ~123 W load |
| CRS812 | check SKU; budget 150 W |
| NAS | 30–80 W (HDD NAS higher) |
| **Plan** | **1.5–2.0 kW** UPS |

## Failure modes

| Symptom | Likely cause | Action |
|---|---|---|
| Sync cannot see a node | Mgmt 10GbE, not fabric | Fix RJ-45 / DHCP / hostname |
| Link up, 50G | Autoneg | Force 200G, disable autoneg |
| iperf ~100G single flow | CX-7 two logical halves | Drive **both** halves (`enp1s0f1np1` + `enP2p1s0f1np1`) |
| NCCL Socket not IB | leftover `/etc/nccl.conf` | Remove `NCCL_IB_DISABLE=1` |
| One node different OS | imaging drift | Reimage to matched train; Sync assumes matched firmware |
| Vault mount only on spark-01 | fstab not shipped | Studio step `vault` must pass on all four |
| OOM during weight pull | pulling to rootfs | Pull to NVMe model dir per manifest |

## Hostnames and roles

| Host | Role |
|---|---|
| `spark-01` | Primary. Studio + Producer UI. Sync seed. |
| `spark-02` | Peer. TP rank 1 in BRAIN/SPLIT brain. |
| `spark-03` | Peer. Wan lane A in SPLIT; TP rank 2 in BRAIN. |
| `spark-04` | Peer. Wan lane B in SPLIT; TP rank 3 in BRAIN. |

Do not rename after first healthgate without rewriting SSH, netplan, and the profile.
