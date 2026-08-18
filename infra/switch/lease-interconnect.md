# Lease interconnect policy

Same Diony fabric rules as the desk crate. The switch is whoever provisioned the four GPUs.

## Pass class

- Four matched nodes on a **private** GPU fabric: InfiniBand, RoCE, or NVLink.
- Force the advertised class. Autoneg that lands at 50G is a fail.
- Jumbo MTU on the fabric path (9000 when the provider allows it).
- Persist the provider config. A reboot that drops to 50G is a fail.
- NCCL on IB / RoCE / NVLink. Never Socket. Never the public NIC.

## Fail

- Public internet as the training fabric
- One fat VM pretending to be four unmatched GPUs
- Open 8000 / 8010 to the world
- `NCCL_IB_DISABLE=1` leftover
- Vault traffic on the NCCL NIC

## Record in Wizard `switch`

Interconnect SKU: InfiniBand | RoCE | NVLink  
Class: 200G, HDR, or NDR (50G is not in the accept list)

Live checks (Week 2+):

```bash
ibstat || rdma link || nvidia-smi nvlink -s
```

Day 2 dry-run: sign the class you reserved. Do not pretend a public VM is a cluster.

Wizard Files on Producer is the rest of the runbook: `lease-gpu-v1` profile, vault fstab, weight manifest, serving notes. Stay in Diony.
