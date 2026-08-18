# Blueprint 03 — Fleet profiles

Status: Day 1 locked  
Desired state files: `infra/profiles/spark-4x-v1.yaml` (Local Quad-Spark), `spark-2x-v1.yaml` (Leased Dual-Spark), `lease-gpu-v1.yaml` (signed four-node lease), `b300-v1.yaml` (Leased B300). Same mode **names**. Spark flags are not H100/B200/B300 flags. Dual-Spark is TP=2 sequential SPLIT. Leased B300 is TP=1 sequential SPLIT — not 2+2 Sparks.

## Principle

The cluster is a **mode-switching appliance**. One mode at a time. Transitions are:

```
drain jobs → unload weights → load target services → healthgate → accept work
```

Wizard and Conductor call the same transition. Never half-load DeepSeek and Wan on the same 128 GB Spark. On `spark-2x-v1` and `b300-v1`, never co-resident DeepSeek and Wan A14B until this Blueprint records a measured yes.

## Modes

### `BRAIN`

- **Layout:** DeepSeek-V4-Flash-0731, tensor parallel 4, Quad-Spark (4)
- **Why:** hour-long bible, series continuity, 256K–1M context
- **When:** new project, bible rewrite, episode outline, heavy QC batch
- **Do not:** generate Wan video in this mode
- **Not on Dual-Spark:** BRAIN there is TP=2 on the pair. Do not paste TP=4.

### `SPLIT` (default production)

- **Layout (desk / four-node lease):** spark-01 + spark-02 = DeepSeek TP=2 (director). spark-03 + spark-04 = two Wan lanes (or one Wan FSDP-2 if a job needs it)
- **Layout (leased Dual-Spark):** sequential on the pair. Drain brain → load Wan, or drain Wan → load brain. Brain while resident is still TP=2.
- **Layout (leased B300):** sequential on one GPU. Drain brain → load Wan, or drain Wan → load brain. The **name** is still SPLIT so Producer and Conductor stay isomorphic.
- **Why:** concurrent planning/QC and generation on four nodes; time-shared planning and generation on one B300
- **When:** after bible lock, during dailies
- **DeepSeek context:** shorter than BRAIN; still enough for shot prompts and QC

### Leased Dual-Spark (`spark-2x-v1`)

- **BRAIN:** DeepSeek TP=2 on spark-01 + spark-02. Do not paste Quad-Spark TP=4. Fabric is CX-7 RoCE between this pair.
- **SPLIT / FARM / FINISH:** same names; one resident engine at a time until measured. FARM is at most two Wan lanes.
- **Context:** Quad-Spark BRAIN 1M assumes 512 GB unified. Do not advertise that number on this pair until KV is measured.
- **SLA:** do not quote `spark-4x-v1` 2–5 days.

### Leased B300 (`b300-v1`)

- **BRAIN:** DeepSeek TP=1 on this GPU. Do not paste Spark TP=4. Do not paste 4×GB300 `--data-parallel-size 4`.
- **SPLIT / FARM / FINISH:** same names; one resident engine at a time until measured.
- **Context:** Spark BRAIN 1M assumes 512 GB unified. Do not advertise that number on one B300 until KV is measured. Lower `max_model_len` on first OOM.

### `FARM`

- **Layout:** all four nodes on Wan (four independent 720p jobs, or two multi-GPU jobs)
- **Why:** maximize picture throughput after rundown lock
- **When:** generation sprint. Conductor must finish in-flight brain calls or pause them.
- **Do not:** leave vLLM resident

### `FINISH`

- **Layout:** super-res + NVENC + audio mix. Typically spark-01 coordinator; others as SR workers
- **Why:** 720→1080, conform, loudness, masters
- **When:** picture selects exist. Can overlap last retakes if memory allows — prefer draining Wan first on GB10.

## Transition API (conceptual)

```
POST /cluster/mode  { "target": "FARM", "reason": "rundown-locked" }
```

Implementation Week 2+. Day 1 Producer/Wizard **display** current mode from the profile stub.

## Drain rules

1. No new jobs in the old mode.
2. Wait for in-flight Wan steps to finish **or** checkpoint to `/scratch` (if implemented). Day 1: wait/fail.
3. `systemctl stop` / compose down brain or wan services.
4. Confirm unified memory free enough for the next load (health probe).
5. Start new compose project.
6. Healthgate: HTTP 200 on brain `/v1/models` and/or Wan worker `/health`.
7. Record mode + timestamp on the cluster profile status file.

## Memory budget (order of magnitude)

| Workload | Resident |
|---|---|
| DeepSeek-V4-Flash-0731 weights | ~162–167 GB **before** KV |
| DeepSeek TP=2 (2×128 GB = 256 GB) | weights + KV; community-tested |
| DeepSeek TP=4 (512 GB) | long context KV headroom |
| Wan T2V-A14B FP8 experts | tens of GB per lane; fits one Spark with offload flags if needed |
| Never | DeepSeek + A14B on the same Spark |
| Never (until measured) | DeepSeek + A14B co-resident on `b300-v1` |

## Failure modes

| Symptom | Cause | Action |
|---|---|---|
| Mode flip during a take | Conductor raced Wizard | Single lock file `/diony/vault/.cluster/mode.lock` |
| OOM on SPLIT brain | KV too long on TP=2 | Lower `max_model_len` or switch BRAIN for that job |
| Wan job killed mid-mode-change | drain skipped | Refuse mode change until queue depth 0 or explicit abort |
| UI shows SPLIT, vLLM still TP=4 | status file stale | Healthgate writes status last |
