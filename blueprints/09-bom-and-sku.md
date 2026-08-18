# Blueprint 09 — BOM and SKU

Status: Day 1 locked  
SKU name: **Diony AI Cluster `spark-4x-v1`**

This is what the crate contains, plus what the customer must add. Wizard assumes this BOM for **Local Quad-Spark**. Leased Dual-Spark (`spark-2x-v1`) and Leased B300 (`b300-v1`) are signed Site exceptions in Blueprint 14, not this crate.

## Compute (required)

| Qty | Item | Notes |
|---|---|---|
| 4 | NVIDIA DGX Spark (GB10), **4 TB NVMe** SKU | 128 GB unified LPDDR5x, ConnectX-7, 10GbE, 240 W brick. Matched firmware. OEM GB10 (ASUS Ascent GX10, MSI EdgeXpert, Founders) OK if clustering-compatible. |
| 4 | Power cables to PDU/UPS | Do not daisy-chain cheap strips |

## Fabric (required)

Pick **one** switch class:

| Option | SKU | Why |
|---|---|---|
| A (desk) | MikroTik **CRS804-4DDQ-HRM** | 4×400G QSFP56-DD + 10GbE mgmt, quiet, Exxact-documented Spark 4-node |
| B (forum-proven) | MikroTik **CRS812-8DS-2DQ-2DDQ** | 4-node Spark + breakout DACs widely reported |

Cables (pick one pattern, do not mix):

| Pattern | Qty | Example |
|---|---|---|
| 1:1 | 4× QSFP-DD → QSFP56 DAC | CRS804 one port per Spark |
| Breakout | 2× QSFP-DD → 2× QSFP56 200G DAC | NADDOD **Q2Q56-400G-CU2** (2 m) class |

**Standardize Spark CX-7 port 2 → fabric.**

Force **200G**, autoneg **off**, MTU **9000**, RS-FEC. Save switch config.

## Management and vault (required)

| Qty | Item | Notes |
|---|---|---|
| 1 | 10GbE switch | Spark RJ-45 ×4 + NAS + laptop. Can be a small MikroTik CRS3xx. |
| 6+ | Cat6/Cat6a | Mgmt + NAS |
| 1 | NAS | **≥50 TB usable** RAID, 10GbE (LACP preferred). NFS export `/diony/vault`. Example class: 8-bay NAS (e.g. ASUSTOR AS6808T class) with enterprise HDD. |
| 1 | UPS + PDU | Budget **1.5–2.0 kW**. 4×240 W + switch + NAS. |

## Optional

| Item | Why |
|---|---|
| HDMI/USB-C display + keyboard | spark-01 first boot |
| Second NAS or LTO | select archive |
| Rack shelf / desk tray | Spark Cluster (4) + switch are small but cable-heavy |
| Spare DAC | fabric is the fragile bit |

## Not in the SKU (NVIDIA’s)

DGX OS, NVIDIA Sync, Cluster Assistant, CUDA, NCCL — preinstalled / NVIDIA license on the hardware. Diony wraps, does not ship.

## Software SKU (this repo)

- Blueprints 00–14
- `infra/profiles/spark-4x-v1.yaml` (crate). Signed exceptions: `lease-gpu-v1`, `b300-v1`
- Wizard, Producer, Conductor
- Contracts
- Weight **manifest** (not the 167 GB in git)

## Power / thermal (desk)

- Spark: 240 W PSU, GB10 TDP 140 W, compact 150×150×50.5 mm
- Four units + switch still a **normal office circuit** if NAS is not a full rack
- Ventilation: do not enclose in an unvented cabinet

## Procurement checklist (Wizard `inventory` expects)

- [ ] Four serial numbers
- [ ] Switch SKU A or B
- [ ] Cable pattern 1:1 or breakout
- [ ] NAS IP and export path
- [ ] UPS present

## Failure modes

| Symptom | Cause | Action |
|---|---|---|
| 1 TB Spark SKU | wrong BOM | weights + scratch will hurt; vault still required |
| 1GbE NAS | “it mounted” | hour-long dailies will stall; require 10GbE |
| Mixed Spark OS | one node skipped OTA | reimage; Sync assumes match |
| QSFP in port 1 | non-canonical | recable to port 2 |
