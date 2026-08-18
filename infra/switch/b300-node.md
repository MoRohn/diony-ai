# Leased B300 interconnect

One rented Blackwell B300. Same Diony product as Spark Cluster (4). No four-node loom. Not a crate.

## Pass class

- Training traffic stays **on-package** (NVLink / the GPU’s own interconnect).
- Management (Wizard, Producer, vault NFS) is a separate NIC or the host LAN. Never NCCL on a public address.
- Record one hostname (`b300-01`) in Wizard Connect. The provider already owns interconnect.
- Autoneg to 50G on a leftover NIC is still a fail if that NIC is used for NCCL.

## Fail

- Treating this box as Spark Cluster (4) (four fabric IPs, MikroTik, CX-7 port 2)
- Treating this box as Leased GPU (`4×B300` / four hosts)
- Pasting Spark TP=2 / TP=4 flags, or a 4×GB300 `--data-parallel-size 4` card command
- Co-resident DeepSeek + Wan A14B until Blueprint 03 records a measured yes
- Open 8000 / 8010 to the world
- Public bucket as `/diony/vault`

## Record in Wizard `inventory`

Interconnect: On-package or NVLink  
Class: on-package

Live checks (Week 2+):

```bash
nvidia-smi
nvidia-smi nvlink -s || true
```

Day 2 dry-run: sign that this is one leased B300, not a desk crate and not a four-node lease.
