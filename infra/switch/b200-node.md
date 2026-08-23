# Leased B200 interconnect

One rented Blackwell B200 (180 GB class). Same Diony product as Spark Cluster (4). No four-node loom. Not a crate. Not B300.

## Pass class

- Training traffic stays **on-package** (NVLink / the GPU’s own interconnect).
- Management (Studio, Producer, vault NFS) is a separate NIC or the host LAN. Never NCCL on a public address.
- Record one hostname (`b200-01`) in Studio Connect. The provider already owns interconnect.
- Autoneg to 50G on a leftover NIC is still a fail if that NIC is used for NCCL.

## Fail

- Treating this box as Spark Cluster (4) (four fabric IPs, MikroTik, CX-7 port 2)
- Treating this box as Leased GPU (`4×B200` / four hosts)
- Treating this box as Leased B300 (`b300-v1`, 1M context start)
- Pasting Spark TP=2 / TP=4 flags, or a 4×GB300 `--data-parallel-size 4` card command
- Co-resident DeepSeek + Wan A14B until Blueprint 03 records a measured yes
- Open 8000 / 8010 to the world
- Public bucket as `/diony/vault`

## Record in Studio `inventory`

Interconnect: On-package or NVLink  
Class: on-package

Live checks (Week 2+):

```bash
nvidia-smi
nvidia-smi nvlink -s || true
```

Day 2 dry-run: sign that this is one leased B200 (180 GB), not a desk crate, not B300, and not a four-node lease.
