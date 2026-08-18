# Blueprint 08 — Ops autonomy

Status: Day 1 locked  
Goal: run, maintain, and recover **near autonomously**. Humans: gates + hardware hands.

## Who does what

| Actor | Owns |
|---|---|
| Conductor (DeepSeek) | rundown, expand, dispatch, QC, retake, request mode, disk pressure pause |
| Wizard | first boot, later “repair crate” replay of failed steps |
| systemd/compose | restart policy on brain/wan/finish |
| Human | bible / picture / mix / distribution; cable reseat; NAS disks; legal |

## Health

Probes (Day 1 scripts record dry-run; Week 2 hit live endpoints):

- `infra/health/check_cluster.py` — mode file, vault write, optional HTTP
- Brain: `GET /v1/models`
- Wan worker: `GET /health`
- Finish: `GET /health`
- Disk: vault >15% free; `/models` present; `/scratch` <70%
- Fabric: skip full iperf every minute; daily or on fail

## Self-heal (allowed)

- Restart crashed vLLM/Wan worker **once**, then page the operator
- Drain a failed Wan lane and requeue Shots
- Pause FARM on disk pressure
- Refuse mode change without drain

## Self-heal (forbidden)

- Skipping a human gate
- Shipping previz as deliverable
- Cloud failover
- Reimaging a node without operator confirm
- Deleting vault selects

Leased Dual-Spark (`spark-2x-v1`) and Leased B300 (`b300-v1`) are **chosen sites**, recorded in Wizard `path` with a named human (Blueprint 14). They are not failovers. Do not bounce jobs because local health failed. `lease-gpu-v1` is not a Wizard Site.

## Updates

- DGX OS OTA: operator-initiated. Wizard records versions. Matched four nodes.
- Diony software: versioned with profiles (`spark-4x-v1` / `spark-2x-v1` / `lease-gpu-v1` / `b300-v1` + app semver).
- Weights: pin revision hashes in `infra/models/manifest.yaml`. Do not `latest` in production.

## Air-gap

Default: **no outbound** except operator-approved weight pull (Hugging Face / ModelScope) during Wizard App (leases) or Producer (desk). After pull, cluster can run disconnected. Producer must not call Dashscope or other hosted LLMs.

If the site is air-gapped from day one, weights arrive on encrypted disk; Wizard App / Producer verifies checksums only.

## Logging

- Cluster: `/diony/vault/.cluster/logs/`
- Site lock: `/diony/vault/.cluster/site.json` (Wizard Sign). `mode.lock` beside it.
- Job: beside the Take
- No secrets in logs (HF tokens)

Mode changes write `mode.lock`. Dual-Spark and B300 refuse concurrent `brain` + `wan`. Drain → unload → load → healthgate. Producer and Conductor read the same files.

## Failure modes

| Symptom | Cause | Action |
|---|---|---|
| Restart loop | bad flags / OOM | stop; do not burn the UPS |
| Split brain two conductors | two Producer instances | single lease on spark-01 |
| Silent cloud | env leftover | scan `DASH_API_KEY`, OpenAI keys; fail health |
