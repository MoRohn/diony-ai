# Blueprint 04 — Model serving

Status: Week 2+ camera live via `diony-wan` + official native or ComfyUI workflow execution. Fabric mutation stays dry-run.
Arch on box: **aarch64 / GB10**. Do not copy x86_64 CUDA images blindly.

Studio **App** (leases) or **Producer** (desk) records this pin: DeepSeek-V4-Flash-0731 plus Wan2.2 A14B (T2V, I2V, S2V, Animate, and VACE Fun compatibility weights). TI2V-5B is previz and is not in the pack. Producer does not expose an Edit page or prompt-to-edit route. Brain serving is auto from the signed site (`spark-4x-v1` TP=4, `spark-2x-v1` TP=2, `b300-v1` TP=1, `b200-v1` TP=1 with `max_model_len` 16384). Operators do not pick a Hugging Face `latest` tag.

## Brain — DeepSeek-V4-Flash-0731

- Repo: `deepseek-ai/DeepSeek-V4-Flash-0731`
- License: MIT
- Disk: **166,878,536,440 bytes** (~155.4 GiB; ~167 GB decimal) measured from upstream safetensors index
- Structure: 284B target / 13B active; DSpark draft attached (~304B physical)
- Chat: **no Jinja template**. Use upstream `encoding` / `encoding_dsv4.py`. `reasoning_effort`: `low` | `high` | `max`
- Sampling (agentic): `temperature=1.0`, `top_p=0.95`. Otherwise `top_p=1.0`. High/max effort: allow very long output (upstream mentions up to 384K)

### Spark is not GB300

Official model card example (4×GB300, `--data-parallel-size 4`, `kv-cache-dtype fp8`, `moe-backend deep_gemm_mega_moe`) is a **starting point for datacenter Blackwell**, not the Spark command.

Four-node leased H100/H200/B200/B300/GB200 (`lease-gpu-v1`) uses that datacenter class, **not** the Spark TP=2 command below, and is **not** a Studio Site. **Leased Dual-Spark** (`spark-2x-v1`) **does** use the Spark TP=2 command — that is its BRAIN. A **leased** B300 (`b300-v1`) uses TP=1 — see `infra/profiles/b300-v1.serving.md`. A **leased** B200 (`b200-v1`) uses TP=1 with `max_model_len 16384` — see `infra/profiles/b200-v1.serving.md`. Neither single GPU is a 4-node lease and neither is the Spark command. Do not paste Spark TP=4 onto Dual-Spark, GB300 4-way flags onto Spark, or B300 `1048576` onto B200. See Blueprint 14.

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

#### TP=1 (leased B200) — `b200-v1`

One Blackwell B200, **180 GB**. Weights ~155.4 GiB. Pin `max_model_len 16384`, `gpu-memory-utilization 0.95`. Do not paste B300 `1048576`. Compose stub: `infra/compose/brain-b200.yml`. Serving notes: `infra/profiles/b200-v1.serving.md`. Sequential SPLIT: `infra/compose/split-b200.yml`.

```
vllm serve /models/deepseek/DeepSeek-V4-Flash-0731 \
  --trust-remote-code \
  --tensor-parallel-size 1 \
  --max-model-len 16384 \
  --gpu-memory-utilization 0.95
```

Studio App does not start vLLM. If the first load OOMs, lower `max-model-len` before touching the pack.

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
| `alibaba-pai/Wan2.2-VACE-Fun-A14B` | Legacy lineage compatibility and automated repair research; no Producer Edit page |

Official single-GPU A14B wants ~80 GB class without offload. GB10 has **128 GB unified** — A14B with `--offload_model` / dtype convert is the conservative Spark path until measured. Multi-GPU: FSDP + DeepSpeed Ulysses as in upstream `torchrun`.

**Prompt extension:** do **not** use Dashscope. Conductor (DeepSeek) writes the extended prompt onto the Shot contract.

Worker: `diony-wan` (`apps/wan`) selects `native` (default) or `comfyui` explicitly. Native wraps official `generate.py`. ComfyUI loads only a validated API-format export of an official Wan 2.2 template and mutates standard inputs; it does not invent a graph. Health: `GET /health`. Readiness: `GET /ready`. Production submits `POST /v1/jobs`, polls `GET /v1/jobs/{id}`, and may cancel with `POST /v1/jobs/{id}/cancel`; the old blocking `POST /v1/generate` is compatibility-only. Dual-Spark FARM is one clip at a time on spark-01 with Spark offload flags (`--offload_model True --convert_model_dtype --t5_cpu`). Leased B200 / B300 native execution must pass `--offload_model False` (upstream defaults True on one GPU) and must not paste Spark dtype-convert / T5-CPU. Prompt extend is DeepSeek on this site, never Dashscope.

### ComfyUI execution contract

- Source graphs: current official Wan 2.2 TI2V-5B, T2V-14B, I2V-14B, and FLF2V templates. Validate once in ComfyUI, then export **API format**. UI/graph JSON is rejected.
- T2V/I2V/FLF2V 14B require two `UNETLoader` / diffusion loader nodes. Diony binds exact matched high-noise and low-noise FP8-scaled filenames and fails preflight if either expert is absent.
- Exact shared files are `text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors`; `vae/wan_2.1_vae.safetensors` for 14B; `vae/wan2.2_vae.safetensors` for 5B. Diffusion filenames and paths are listed in `apps/wan/README.md` and enforced in `diony_wan.comfy.MODEL_FILES`.
- Injection covers positive, negative, deterministic seed, width, height, `4n+1` frame count, steps, CFG, sampler, scheduler, image(s), output prefix, and the selected matched diffusion files. Both classic `KSampler` and current modular sampler nodes are supported.
- Default Production negative: 1280×720, 81 frames, 24 steps, CFG 3.5, Euler + Beta. 832×480 is previz. Wan output remains 720p/previz; `diony_sr_v1` owns the 1080 master.
- Every run persists its seed, full config, mutated request, `/prompt` response, `/history` result, prompt ID, and state beside the negative. Exact validation and execution node errors reach Producer. Retry calls `/free` with model unload + executor-cache release.
- Diony owns persistent `queued → running → completed/failed/cancelled` job records, destination locks, restart adoption, queue/VRAM telemetry, elapsed time, success rate, and output hand-off. Concurrent HTTP admission is safe; one worker is serialized per GPU/ComfyUI endpoint so `/free` and `/interrupt` cannot affect another shot.
- 5B fallback is opt-in and readiness-gated. A remote ComfyUI URL is also explicit opt-in. Diony never auto-routes a failed local shot to the internet.
- I2V prompts describe motion, not source appearance. FLF2V prompts must be compatible with both frames. The cinematic builder enforces camera → coherent motion → stable atmosphere in 80–120 words; existing Shot prompts remain contract-owned and reproducible.

On the signed pair after Studio App: clone Wan2.2 to `/opt/wan22`, `pip install -r /opt/wan22/requirements.txt`, `PYTHONPATH=/opt/wan22`. `DIONY_PYTHON` is the **host** interpreter that can `import torch` (RunPod image: `/usr/bin/python3`). It is not `$DIONY_REPO/.venv/bin/python` — that venv is FastAPI only. Studio writes `diony-system-torch.pth` so in-process finish can see the same torch. Produce then writes `takes/{id}/negative_720.mp4`.

Host dependencies are tiered. T2V/I2V are the strict Production core: upstream base requirements plus `infra/runtime/wan-host-overlay.txt`, validated by `diony_wan.runtime` under the exact CUDA interpreter used for `generate.py`. S2V and Animate are purpose-built optional capability sets: Producer routes S2V only with a still plus recorded performance audio, and Animate only with a still plus official preprocessed motion source. Their missing packages are reported but do not break core camera startup. Diony patches Wan's package exports to lazy-load those optional engines. Never compile an optional SAM2 extension against a CUDA/PyTorch mismatch just to make T2V import.

LightX2V / Cache-dit / FastVideo are **Week 2+ accelerators**. Day 1 quality path is official Wan. Speed path must not silently drop the quality mandate.

## Finish — Diony 1080

Not an upstream model. Pipeline in Blueprint `06`. Worker: `diony-finish` (`apps/finish`) on :8020. Picture lock drains Wan, runs tiled `diony_sr_v1`, writes `takes/{id}/finish_1080.mov`, then overlap-stitches `program/{timeline}/master_1080.mov`. Compose: `infra/compose/finish.yml`. Bootstrap weights: Real-ESRGAN x2 at `/models/diony/diony_sr_v1` (Studio App pulls). Never lanczos as a master.

## Audio

- Dialogue TTS: pinned local `iic/CosyVoice2-0.5B` through the official CosyVoice
  runtime. Studio App prepares the runtime, model, and a local reference prompt;
  Production never downloads audio dependencies on demand. Spoken intent must
  produce a versioned dialogue stem or block Mix with an actionable readiness
  error. It may never be replaced by room tone.
- Wan S2V remains a separate camera capability for a still plus recorded
  performance audio; CosyVoice supplies dialogue audio and does not silently
  relabel a Shot as S2V.
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
| Wan OOM | both MoE experts + T5 on GPU | Spark only: `--offload_model True --t5_cpu --convert_model_dtype` |
| B200/B300 farm dies at `0/40` / `Loading checkpoint` / float32 `to()` | Spark offload pasted onto 180/288 GB; generate.py default offload is True | `--offload_model False`. No `--convert_model_dtype`, no `--t5_cpu`. Do not QC-fail on tqdm |
| QC shows `Loading ch` / `0/40` / float32 warning | stderr progress captured as the fail | Stream `generate.py` to `{take}.wan.log`. Classify Traceback / signal / OOM only |
| Cloud calls in logs | Dashscope env set | Unset `DASH_API_KEY`; Conductor extend only |
| generate.py `ImportError: wan` | clone missing or PYTHONPATH | `/opt/wan22` + `PYTHONPATH=/opt/wan22` |
| generate.py `No module named torch` | Diony venv used as `python3` | `DIONY_PYTHON` = host python with CUDA torch; not `.venv` |
| generate.py `No module named dashscope` | Official script imports DashScopePromptExpander at load | `pip install dashscope` on **host** python. Never set `DASH_API_KEY`. Never `--use_prompt_extend`. |
| generate.py `No module named einops` | Wan2.2 imports `einops`, but the upstream `requirements.txt` does not declare it | Install Diony's pinned host overlay into `DIONY_PYTHON`, then require the complete `diony_wan.runtime` import gate before Ready or Production. Do not repull weights. |
| generate.py `numpy has no attribute 'long'` | Wan `requirements.txt` pinned numpy 1.x over the image torch stack | Host pip skips `numpy` and `flash_attn`. Keep the image numpy. |
| generate.py `AssertionError` at `FLASH_ATTN_2_AVAILABLE` | Official `flash_attention` requires the `flash_attn` wheel; host pip skips it (compile) | Diony routes Wan through `attention()` SDPA on `[B, L, N, C]` before FA2 flatten. Do not compile flash_attn on the lease overlay. |
| generate.py `mat1 and mat2 shapes cannot be multiplied (...x128 and 5120x5120)` | SDPA was called *after* FA2 flatten, so `self.o` saw tokens×C vs hidden×hidden | Patch `flash_attention` to SDPA *before* flatten. Re-roll 0-byte takes. |
| OpenAI client “no chat template” | missing encoding_dsv4 | Use upstream encoder |
