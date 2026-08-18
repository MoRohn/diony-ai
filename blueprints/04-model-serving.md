# Blueprint 04 — Model serving

Status: Week 2+ camera live via `diony-wan` + official generate.py. Fabric mutation stays dry-run.  
Arch on box: **aarch64 / GB10**. Do not copy x86_64 CUDA images blindly.

Wizard **App** (leases) or **Producer** (desk) records this pin: DeepSeek-V4-Flash-0731 plus Wan2.2 A14B (T2V, I2V, S2V, Animate, VACE Fun). TI2V-5B is previz and is not in the pack. Brain serving is auto from the signed site (`spark-4x-v1` TP=4, `spark-2x-v1` TP=2, `b300-v1` TP=1). Operators do not pick a Hugging Face `latest` tag.

## Brain — DeepSeek-V4-Flash-0731

- Repo: `deepseek-ai/DeepSeek-V4-Flash-0731`
- License: MIT
- Disk: **166,878,536,440 bytes** (~155.4 GiB; ~167 GB decimal) measured from upstream safetensors index
- Structure: 284B target / 13B active; DSpark draft attached (~304B physical)
- Chat: **no Jinja template**. Use upstream `encoding` / `encoding_dsv4.py`. `reasoning_effort`: `low` | `high` | `max`
- Sampling (agentic): `temperature=1.0`, `top_p=0.95`. Otherwise `top_p=1.0`. High/max effort: allow very long output (upstream mentions up to 384K)

### Spark is not GB300

Official model card example (4×GB300, `--data-parallel-size 4`, `kv-cache-dtype fp8`, `moe-backend deep_gemm_mega_moe`) is a **starting point for datacenter Blackwell**, not the Spark command.

Leased H100/H200/B200/B300/GB200 (`lease-gpu-v1`) uses that datacenter class, **not** the Spark TP=2 command below. **Leased Dual-Spark** (`spark-2x-v1`) **does** use the Spark TP=2 command — that is its BRAIN. A **leased** B300 (`b300-v1`) uses TP=1 — see `infra/profiles/b300-v1.serving.md`. It is not a 4-node lease (`4×B300`) and not the Spark command. Pin lease and B300 recipes in compose when Week 2+ serving is live. Do not paste Spark TP=4 onto Dual-Spark, or GB300 4-way flags onto Spark. See Blueprint 14.

**SKU recipes to pin in compose:**

#### TP=2 (SPLIT brain on Quad-Spark; BRAIN on Dual-Spark) — community-tested class

2× DGX Spark, one GPU each (GB10), vLLM + DSpark. On `spark-4x-v1` this is the SPLIT brain (spark-01+02). On `spark-2x-v1` this is BRAIN and the resident brain during sequential SPLIT.

```
vllm serve deepseek-ai/DeepSeek-V4-Flash-0731 \
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

Calibrated context ceiling is **1,048,576** (YaRN), not 1.5M. Do not advertise 1.5M.

Tune `num_speculative_tokens` (3 vs 5) against garble and throughput on **this** cluster; record the winner in the profile. Forum 4-node Spark work used `MTP_NUM_TOKENS=3` in places; 2-node 1M recipes sometimes use 5.

vLLM versions: DeepSeek V4 support and DSpark on NVIDIA are moving targets. Pin a digest in Week 2; Day 1 names the flags.

#### TP=4 (BRAIN) — long-context lane

4× Spark, fabric QRS812/CRS812 class, TP=4. Community: `kv-cache-dtype nvfp4_ds_mla`, DSpark, async scheduling, chunked prefill. Bind NCCL to Spark port2. This lane is for bible/hour scripts, not for decode-throughput farming. Forum note: **2×2-node replicas still win aggregate decode**; 4-node TP=4 wins shared long-context.

Compose stub: `infra/compose/brain.yml`

#### TP=2 Dual-Spark BRAIN — `spark-2x-v1`

Same flags as TP=2 above. Compose stub: `infra/compose/brain-spark-2x.yml`. Serving notes: `infra/profiles/spark-2x-v1.serving.md`. Sequential SPLIT: `infra/compose/split-spark-2x.yml`. Fabric between the pair is required.

#### TP=1 (leased B300) — `b300-v1`

One Blackwell B300. Compose stub: `infra/compose/brain-b300.yml`. Serving notes: `infra/profiles/b300-v1.serving.md`.

```
vllm serve /models/deepseek/DeepSeek-V4-Flash-0731 \
  --trust-remote-code \
  --tensor-parallel-size 1 \
  --max-model-len 1048576 \
  --gpu-memory-utilization 0.90
```

Tune `max-model-len` on first load. Spark BRAIN 1M is not a B300 promise. Sequential SPLIT: `infra/compose/split-b300.yml`.

### Encoding

Always encode with `encoding_dsv4`. If vLLM’s OpenAI route already wraps it, verify with upstream test cases before production. Do not invent a chat template.

Conductor defaults:

| Task | `reasoning_effort` |
|---|---|
| Bible, continuity, QC fail diagnosis | `max` |
| Shot prompt expansion | `high` |
| Dispatcher / short tool calls | `low` or `high` |

## Camera — Wan2.2

Source: https://github.com/Wan-Video/Wan2.2  
License: Apache 2.0  
Native sizes: **480p and 720p**. TI2V-5B 720p is `1280×720` or `704×1280` at **24 fps**.

| Weights | Role |
|---|---|
| `Wan-AI/Wan2.2-T2V-A14B` | Cinematic text-to-video (quality) |
| `Wan-AI/Wan2.2-I2V-A14B` | Image-to-video, identity from keyframe |
| `Wan-AI/Wan2.2-TI2V-5B` | Fast previz T2V+I2V |
| `Wan-AI/Wan2.2-S2V-14B` | Speech-to-video (talking head / singing) |
| `Wan-AI/Wan2.2-Animate-14B` | Character animation / replacement |
| `alibaba-pai/Wan2.2-VACE-Fun-A14B` | Prompted edit: inpaint, outpaint, camera, replace |

Official single-GPU A14B wants ~80 GB class without offload. GB10 has **128 GB unified** — A14B with `--offload_model` / dtype convert is the conservative Spark path until measured. Multi-GPU: FSDP + DeepSpeed Ulysses as in upstream `torchrun`.

**Prompt extension:** do **not** use Dashscope. Conductor (DeepSeek) writes the extended prompt onto the Shot contract.

Worker: `diony-wan` (`apps/wan`) wraps official `generate.py`. Health: `GET /health`. Generate: `POST /v1/generate`. Dual-Spark FARM is one clip at a time on spark-01 with Spark offload flags. Prompt extend is DeepSeek on this site, never Dashscope.

On the signed pair after Wizard App: clone Wan2.2 to `/opt/wan22`, `pip install -r /opt/wan22/requirements.txt`, `PYTHONPATH=/opt/wan22`, `DIONY_PYTHON` is the GB10 interpreter that can `import wan`. Produce then writes `takes/{id}/negative_720.mp4`.

LightX2V / Cache-dit / FastVideo are **Week 2+ accelerators**. Day 1 quality path is official Wan. Speed path must not silently drop the quality mandate.

## Finish — Diony 1080

Not an upstream model. Pipeline in Blueprint `06`. Worker: `diony-finish` (`apps/finish`) on :8020. Picture lock drains Wan, runs tiled `diony_sr_v1`, writes `takes/{id}/finish_1080.mov`, then overlap-stitches `program/{timeline}/master_1080.mov`. Compose: `infra/compose/finish.yml`. Bootstrap weights: Real-ESRGAN x2 at `/models/diony/diony_sr_v1` (Wizard App pulls). Never lanczos as a master.

## Audio

- TTS: CosyVoice (Wan S2V extra requirements)
- Score: pin **ACE-Step** as the Day 1 Blueprint choice (local, replaceable). Bake-off is Week 2+.
- Mix: ffmpeg + loudness (pyloudnorm or ffmpeg `loudnorm`) to LUFS targets in `00`

## Weight placement

`infra/models/manifest.yaml` lists ids, approximate bytes, destination `/models/...`. Checksums filled when first pulled. Pull with `huggingface-cli` **on spark-01**, then replicate or NFS-export only if bandwidth allows; prefer local NVMe copies per node for Wan lanes.

## Failure modes

| Symptom | Cause | Action |
|---|---|---|
| Garble with DSpark | speculative tokens too high / greedy vs probabilistic | Drop to 3 tokens; `probabilistic` draft |
| vLLM won’t load on 2 nodes | using GB300 DP=4 flags | Use Spark TP=2 recipe |
| vLLM OOM on B300 at 1M | copied Spark BRAIN context | Lower `max_model_len`; see `b300-v1.serving.md` |
| Wan OOM | both MoE experts + T5 on GPU | `--offload_model True --t5_cpu --convert_model_dtype` |
| Cloud calls in logs | Dashscope env set | Unset `DASH_API_KEY`; Conductor extend only |
| generate.py `ImportError: wan` | clone missing or PYTHONPATH | `/opt/wan22` + `PYTHONPATH=/opt/wan22` |
| OpenAI client “no chat template” | missing encoding_dsv4 | Use upstream encoder |
