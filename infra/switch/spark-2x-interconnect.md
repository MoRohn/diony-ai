# Leased Dual-Spark interconnect

Two rented DGX Spark GB10. Same Diony product as Quad-Spark. Not a crate. Not one B300. Not four nodes.

## Pass class

- Training traffic is **RoCE on ConnectX-7 between this pair**. Force 200G. Autoneg 50G is a fail.
- spark-01 and spark-02 must share that fabric. Two isolated Sparks on management LAN will not load DeepSeek TP=2.
- Management (Wizard, Producer, vault NFS) is a separate NIC. Never NCCL on a public address.
- Record two hostnames (`spark-01`, `spark-02`) in Wizard Connect. The provider already owns the box and the pair fabric.
- Weights live on NVMe at `/models`. Replicate spark-01 → spark-02 after pull.

## Fail

- Treating this pair as Quad-Spark (four fabric IPs, MikroTik crate, TP=4)
- Treating this pair as Leased B300 (one host, on-package, TP=1)
- Treating this pair as `lease-gpu-v1` (four matched datacenter GPUs)
- Two Sparks with no CX-7 path between them
- Co-resident DeepSeek + Wan A14B
- Open 8000 / 8010 to the world
- Public bucket as `/diony/vault`

## Record in Wizard `inventory`

Interconnect: CX-7 RoCE  
Class: 200G pair

Live checks (Week 2+):

```bash
ibstat || rdma link
# both nodes CX-7 ACTIVE, 200G class
```

Day 2 dry-run: sign that this is two leased Sparks with fabric between them, not a desk crate and not a single GPU.
