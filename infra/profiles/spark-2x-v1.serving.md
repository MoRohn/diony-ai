# Serving — spark-2x-v1

Two leased DGX Spark GB10. Same Diony as Quad-Spark, on two machines. Sequential SPLIT. Not the desk crate. Not B300.

- Brain: **TP=2** Spark recipe (Blueprint 04). **Not** TP=4. **Not** B300 TP=1. **Not** 4×GB300 `--data-parallel-size 4`.
- Compose: `infra/compose/brain-spark-2x.yml` (BRAIN), `split-spark-2x.yml` (named SPLIT, sequential), `wan.yml`, `finish.yml`
- SPLIT on this SKU is **time-share**: drain brain → load Wan on one or both nodes, or drain Wan → load brain. Do not co-resident DeepSeek and Wan A14B.
- 1M context is a Quad-Spark BRAIN claim (512 GB / TP=4). On this pair, start from 1048576 intent and lower `max_model_len` on first OOM.
- Fabric: ConnectX-7 RoCE **between this pair**. Two isolated Sparks will not load TP=2.
- Weights: same `infra/models/manifest.yaml` → `/models` on spark-01, replicate to spark-02.
- Vault: `/diony/vault` via `infra/storage/vault.fstab.example`.
- Producer: `http://producer.diony.local` (binds `:8788` on spark-01)
- Camera: `diony-wan` `:8010` after drain brain. Official `generate.py`, `--size 1280*720`, `--offload_model True --convert_model_dtype --t5_cpu`. `PYTHONPATH=/opt/wan22`. Never `--use_prompt_extend` / Dashscope.
- After pull, drop outbound. No Dashscope.
- Do not quote the `spark-4x-v1` 2–5 day 1-hour SLA. Measure this pair first.

## BRAIN / SPLIT brain intent (Week 2+ pin)

```
vllm serve /models/deepseek/DeepSeek-V4-Flash-0731 \
  --trust-remote-code \
  --kv-cache-dtype nvfp4_ds_mla \
  --block-size 256 \
  --tensor-parallel-size 2 \
  --distributed-executor-backend mp \
  --nnodes 2 \
  --enable-expert-parallel \
  --max-model-len 1048576 \
  --max-num-seqs 6 \
  --gpu-memory-utilization 0.85 \
  --speculative-config '{"method":"dspark","num_speculative_tokens":3,"draft_sample_method":"probabilistic"}'
```

Replace encoding with upstream `encoding_dsv4`. Do not invent a chat template. Pin NCCL to Spark port 2.
