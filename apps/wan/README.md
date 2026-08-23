# Wan camera worker

Live Wan2.2 with two explicit backends. `native` (default) runs the official
`generate.py`; `comfyui` executes a validated API-format export of an official
ComfyUI Wan 2.2 template. Both write a native **720p negative**. Neither may use
Dashscope. Diony finish remains mandatory for a 1080 deliverable.

The optional persistent BF16 worker is governed by Blueprint 21. It is inert
unless `DIONY_WAN_RESIDENT=validated` and a passing
`DIONY_WAN_RESIDENT_RECEIPT` are both present. The default rollout is a
deterministic 10 percent canary. Any worker failure opens a receipt-scoped
circuit and reruns the same Take through pinned `generate.py`.

```bash
pip install -e packages/contracts -e apps/wan
# After Studio App pack pull and clone Wan2.2 to /opt/wan22:
export DIONY_WAN_REPO=/opt/wan22
export PYTHONPATH=/opt/wan22${PYTHONPATH:+:$PYTHONPATH}
unset DASH_API_KEY DASHSCOPE_API_KEY
diony-wan   # :8010
```

Dual-Spark: drain DeepSeek before this process. Producer `Produce` enters FARM and calls this worker (or `generate.py` on spark-01).

## ComfyUI backend

Use the official templates as graphs; do not hand-build replacements. In
ComfyUI 0.3.46 or newer, open the Wan 2.2 T2V, I2V, TI2V-5B, or FLF2V template,
validate it interactively once, then export **Save (API Format)** to:

```text
/diony/workflows/wan2.2/wan2.2-t2v-14b.api.json
/diony/workflows/wan2.2/wan2.2-i2v-14b.api.json
/diony/workflows/wan2.2/wan2.2-ti2v-5b.api.json
/diony/workflows/wan2.2/wan2.2-flf2v-14b.api.json
```

The export must retain titled positive/negative `CLIPTextEncode` nodes. Diony
discovers standard latent, sampler, seed, loader, image, and output inputs and
fails on ambiguity. For 14B it requires and binds both diffusion loaders.

Exact ComfyUI placement:

```text
ComfyUI/models/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors
ComfyUI/models/vae/wan2.2_vae.safetensors                         # 5B
ComfyUI/models/vae/wan_2.1_vae.safetensors                        # 14B
ComfyUI/models/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors
ComfyUI/models/diffusion_models/wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors
ComfyUI/models/diffusion_models/wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors
ComfyUI/models/diffusion_models/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors
ComfyUI/models/diffusion_models/wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors
```

Configure and gate the backend:

```bash
export DIONY_WAN_BACKEND=comfyui
export DIONY_COMFY_URL=http://127.0.0.1:8188
export DIONY_COMFY_ROOT=/opt/ComfyUI
curl -fsS http://127.0.0.1:8010/ready | python -m json.tool
```

The local default cannot send frames to an external host. An explicitly
approved remote ComfyUI requires both its URL and `DIONY_ALLOW_REMOTE_COMFY=1`.
The optional 5B fallback likewise requires a validated 5B workflow, exact 5B
files, and `DIONY_COMFY_ALLOW_5B_FALLBACK=1`; it is never silently enabled.

Starting parameters are 1280×720, 81 frames, 24 steps, CFG 3.5, Euler + Beta
for production negatives. Use 832×480 for approved previz only. Width and height
must be divisible by 16; frames must be `4n+1` from 17 through 81. Every request
records the seed, full mutated workflow, queue response, history, node errors,
and durable `queued → processing → completed/failed` state beside the output.
The runner calls `/free` after a heavy job and before retrying.

On B200, a promoted Project-scoped
`/diony/vault/qualification/wan-cudnn-sdpa-validation.json` selects PyTorch's
cuDNN SDPA backend for the official Wan path. The receipt requires an exact
`[1,40,75600,128]` BF16 operator probe plus one AutoQC/reference/temporal/1080
quality canary. It does not change models, steps, solver, seed, or conditioning;
unsupported cuDNN calls fall back to automatic SDPA.

## Diony-owned job contract

`POST /v1/jobs` is the Production interface. It accepts `quality_tier`
(`preview`, `production`, or `hero`), optional explicit `mode` (`task` remains a
compatibility alias), prompt, negative,
seed, destination, dimensions, frame count, sampler settings, first/last images,
recorded S2V audio, prepared Animate source root, and qualified quality-LoRA IDs.
`GET /v1/jobs/{id}` returns durable state and the underlying ComfyUI
`prompt_id`, queue depth, observed VRAM use/peak, timings, exact node error, and
output path. `POST /v1/jobs/{id}/cancel` interrupts the active executor and
`POST /v1/jobs/{id}/rerun` creates a new destination with the exact recorded
config and seed.
`GET /v1/metrics` reports queue time, generation time, success rate, and peak
VRAM. The compatibility-only blocking route remains `POST /v1/generate`.

Job records live at `{DIONY_VAULT}/.wan-jobs` (override with
`DIONY_WAN_JOBS`). Output destinations have cross-process ownership locks, so
concurrent HTTP submissions are safe while a single GPU/ComfyUI endpoint
executes one shot at a time. After a Diony restart, a landed clip is adopted; an
in-flight ComfyUI `prompt_id` is resumed through history without a duplicate
`POST /prompt`; a request that crashed before queueing is safely re-submitted.
Producer uses this job API and does not treat an HTTP acknowledgement as a
landed negative.

The preflight VRAM number is a conservative capacity-planning estimate. The
observed peak comes from ComfyUI `/system_stats` and is the operational metric.
The native runtime accepts only the site-qualified official Animate relighting
adapter (`wan2.2-animate-relighting`, declared in
`DIONY_WAN_QUALIFIED_LORAS`) and records it with the job. Other quality or
Lightning LoRAs fail closed; no speed LoRA is silently applied to Production or
hero.

### T2V and I2V smoke

These are real GPU smoke tests; they are not part of laptop pytest. Start
ComfyUI and `diony-wan`, make a keyframe available on the Wan host, then run:

```bash
mkdir -p /diony/vault/smoke
curl -fsS -X POST http://127.0.0.1:8010/v1/jobs \
  -H 'content-type: application/json' \
  -d '{"quality_tier":"production","task":"t2v-A14B","prompt":"Static shot of a night newsroom as a reporter closes a ledger and rises in one continuous action. Stable tungsten practicals, cool window spill, restrained teal and amber grade, natural motion blur, consistent anatomy, controlled depth of field, clean cinematic cadence, believable contact and gravity, continuous background geometry, no unmotivated camera change.","seed":2201,"save_file":"/diony/vault/smoke/wan22-t2v.mp4"}' \
  | tee /tmp/wan22-t2v-job.json

curl -fsS -X POST http://127.0.0.1:8010/v1/jobs \
  -H 'content-type: application/json' \
  -d '{"quality_tier":"production","task":"i2v-A14B","prompt":"Static shot. The subject turns toward camera, takes one measured step, and settles naturally. Preserve the supplied composition and identity exactly. Lighting remains stable, motion stays physically coherent, facial structure and wardrobe remain continuous, background geometry does not drift, and the action resolves cleanly with restrained film grain and natural 24fps motion blur.","seed":2202,"image":"/diony/vault/smoke/i2v-keyframe.png","save_file":"/diony/vault/smoke/wan22-i2v.mp4"}' \
  | tee /tmp/wan22-i2v-job.json

T2V_JOB=$(python3 -c 'import json; print(json.load(open("/tmp/wan22-t2v-job.json"))["id"])')
I2V_JOB=$(python3 -c 'import json; print(json.load(open("/tmp/wan22-i2v-job.json"))["id"])')
curl -fsS "http://127.0.0.1:8010/v1/jobs/${T2V_JOB}" | python3 -m json.tool
curl -fsS "http://127.0.0.1:8010/v1/jobs/${I2V_JOB}" | python3 -m json.tool

test -s /diony/vault/smoke/wan22-t2v.mp4
test -s /diony/vault/smoke/wan22-i2v.mp4
```

Poll each job until its state is `completed` before the `test -s` checks. To
prove clean input recovery, this request must fail immediately with the exact
latent-shape message and must not enter the GPU queue:

```bash
curl -sS -o /tmp/wan22-invalid.json -w '%{http_code}\n' \
  -X POST http://127.0.0.1:8010/v1/jobs \
  -H 'content-type: application/json' \
  -d '{"quality_tier":"production","prompt":"Static shot with one coherent action.","seed":2203,"save_file":"/diony/vault/smoke/must-not-exist.mp4","frames":80}'
python3 -m json.tool /tmp/wan22-invalid.json
test ! -e /diony/vault/smoke/must-not-exist.mp4
```

For restart recovery, restart `diony-wan` while either smoke job reports
`processing`, then query the same job ID. Its record must show `recovered: true`
and retain the same ComfyUI `prompt_id`; no second prompt is queued.

On failure, inspect the adjacent `.comfy.status.json` and `.comfy.json`; exact
ComfyUI node errors are retained. `GET /ready` checks the server, workflows,
graph shape, dual experts, and exact model filenames before Production.
