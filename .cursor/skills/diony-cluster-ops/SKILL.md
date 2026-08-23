---
name: diony-cluster-ops
description: Operates the 4x DGX Spark Diony cluster — fleet modes BRAIN/SPLIT/FARM/FINISH, NVIDIA Sync wrap, RoCE 200G, vault NAS, vLLM DSpark vs Wan serving. Use when changing infra profiles, Wizard steps, compose, switch snippets, or health probes.
---

# Diony cluster ops

## Instructions

1. Read `blueprints/01`–`04`, `08`–`09`, `14` plus `infra/profiles/spark-4x-v1.yaml` (Local Quad-Spark), `spark-2x-v1.yaml` (Leased Dual-Spark), `b300-v1.yaml` (Leased B300), or `b200-v1.yaml` (Leased B200). `lease-gpu-v1.yaml` is not a Wizard Site.
2. Wizard wraps NVIDIA Sync on the desk; on a lease it wraps Sync or the provider. Do not reimplement Cluster Assistant.
3. Mode changes: drain → unload → load → healthgate. One lock file.
4. DeepSeek Spark TP=2 is Dual-Spark BRAIN and Quad-Spark SPLIT brain; TP=4 is Quad-Spark BRAIN long-context. Do not use the GB300 `data-parallel-size 4` card command as-is. Do not paste Spark TP=4 onto Dual-Spark. Do not paste Spark flags onto H100/B200/B300. Leased B300 is TP=1 (`b300-v1.serving.md`). Leased B200 is TP=1 with `max_model_len 16384` (`b200-v1.serving.md`).
5. Pin Spark / Dual-Spark / B300 `max_model_len` at 1048576. Leased B200 pins **16384**. Do not advertise 1.5M. Do not paste B300 1048576 onto B200.
6. Autoneg 50G is a fail. Dual-half iperf (or full advertised lease fabric) is the bandwidth pass.
7. Never default to Dual-Spark, B300, or B200. Never fail over local → a lease.

## Examples

- New switch SKU: add snippet under `infra/switch/` and list it in Blueprint 09.
- New health probe: extend `infra/health/check_cluster.py` and keep laptop dry-run passing without `/diony/vault`.
