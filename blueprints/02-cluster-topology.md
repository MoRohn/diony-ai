# Blueprint 02 — Cluster topology

Status: Day 1 locked  
Profile: `spark-4x-v1`

## Two networks, never one

| Network | Hardware | Purpose |
|---|---|---|
| **Fabric** | ConnectX-7, Spark **port 2**, RoCE v2, 200 GbE class | NCCL, vLLM TP, Gloo, SSH-over-fabric |
| **Management / vault** | RJ-45 10GbE | Studio, Producer browsers, NFS/SMB to NAS, OTA, Sync enrollment |

Do not put NAS traffic on the RoCE fabric. Do not put NCCL on 10GbE.

## Node identity

Canonical IPs are **examples**. Studio records the actual Sync IP plan.

```
# Fabric (RoCE) — /24 example
spark-01 fabric0  192.168.200.11
spark-02 fabric0  192.168.200.12
spark-03 fabric0  192.168.200.13
spark-04 fabric0  192.168.200.14

# If dual-half addressing is required (CRS812 breakout community recipe):
# each node also has fabric1 on the second logical half of the same QSFP.
# Assign a second /24 (e.g. 192.168.201.1x) and never disable IPv6 GID
# until RoCE GIDs are documented as stable. Community practice: disable
# IPv6 on CX-7 to keep GID indices consistent — do that only after
# measuring, and record GID in the cluster profile.
```

Management IPs live on the 10GbE switch (DHCP reservation preferred).

## MTU

End-to-end **9000** on:

- switch fabric ports
- Spark CX-7 netplan
- (not required on 10GbE unless NAS jumbo is enabled; default 1500 on mgmt is OK)

Mismatch = silent performance death.

## NCCL / vLLM environment (Spark)

Pin in the compose stubs; do not rely on shell memory.

```
NCCL_IB_HCA=mlx5
NCCL_NET=IB
NCCL_IB_GID_INDEX=<measured, often 11 on Spark port2>
NCCL_SOCKET_IFNAME=<mgmt iface, for bootstrap only>
GLOO_SOCKET_IFNAME=<fabric iface>
```

Fail if `/etc/nccl.conf` forces `NCCL_IB_DISABLE=1` or `NET=Socket`.

Interface names on GB10 vary. Studio records the live names from Sync. Community CRS812 recipe used Spark **port2** names like `enP2p1s0f1np1` / `roceP2p1s0f1`.

## Health gates (Studio step `roce`)

Run from spark-01 after Sync claims healthy.

### 1. Link

```
ibstat || rdma link
# expect CX-7 ACTIVE, 200 Gbps class (or 2×100G halves that aggregate)
```

### 2. Bandwidth

NVIDIA Sync already ran `ib_write_bw` / `ib_write_lat`. Diony re-checks:

```
# Dual-half iperf3: two parallel sessions per pair
# Pass: ~196–198 Gb/s aggregate per node-pair (community CRS812)
# Fail: ~50 Gb/s (autoneg) or ~100 Gb/s with only one half driven
```

Single-flow iperf topping out near 100G is **expected** if only one logical half is used. That is not a pass for fabric.

### 3. NCCL

```
# 4-node all_reduce 256MiB, NET=IB + RDMA plugin
# Pass class: ~23 GB/s avg busbw (~190 Gb/s class) as seen on CRS812 Spark clusters
# Fail: Socket backend or <10 GB/s busbw
```

Day 1 Studio records these as **planned probes** and can ingest pasted numbers.

## Storage topology

```
/models            local NVMe per node (weights; replicated or rank-sharded)
/scratch           local NVMe (generation scratch; wipeable)
/diony/vault       NAS NFS  (projects, dailies, timelines, masters)
```

fstab stub: `infra/storage/vault.fstab.example`

Write test:

```
touch /diony/vault/.diony-health/${HOSTNAME}
```

Must succeed on all four nodes.

## SSH

Passwordless SSH **over fabric** as configured by Sync. Conductor and torchrun use that mesh. Do not add a second SSH key story.

## Failure modes

| Symptom | Cause | Action |
|---|---|---|
| TP hang on load | NCCL on 10GbE | Bind NCCL to CX-7 |
| Intermittent GID errors | IPv6 / GID index drift | Pin GID index; document in profile |
| Vault 10 MB/s | NAS on Wi-Fi or 1GbE | 10GbE wired, LACP |
| Node A can ping fabric B, not C | switch VLAN / breakout mapping | Recable to canonical map |
| After reboot, 50G | switch did not persist 200G | Save RouterOS config; Studio re-verify |
