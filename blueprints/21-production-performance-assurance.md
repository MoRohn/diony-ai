# Blueprint 21 — Production Performance Assurance

Status: B200 cuDNN SDPA lane implemented; run-scoped quality canary required before activation

Owner: Diony AI

Applies to: Wan FARM, Diony Finish, Producer AutoQC, production telemetry, B200/B300 profiles

Depends on: Blueprints 05, 06, 08, 10, 19, and 20

## Product decision

Performance work may remove repeated initialization, unnecessary device transfers,
whole-clip buffering, and request-path control work. It may not reduce the camera
or master contract. A faster result is a candidate until it proves all of the
following against the pinned production lane:

- the same Wan2.2 A14B revision and checkpoint bytes;
- 1280x720 native camera negatives, 81 frames, and the same reference inputs;
- BF16 parameters, 24 UniPC steps, pinned shifts/guidance, seed, positive prompt,
  and negative conditioning;
- mandatory `diony_sr_v1` 1920x1080 Finish;
- ProRes 422 HQ 10-bit Rec.709 limited-range masters at 23.976 or 24.000 fps;
- no increase in AutoQC failures, manual retakes, OOMs, retries, or p95 latency.

Fewer steps, output-changing step/block caches, sparse attention, distilled
LoRAs, FP8/FP4, alternate weights, and simultaneous A14B clips on one B200 are
not part of the quality-preserving lane. Receipt-bound regional compilation of
the existing BF16 transformer blocks is a separate candidate because it changes
kernel execution without changing weights, steps, conditioning, or the solver.

## 1. B200 attention backend selection

The measured 81-frame I2V/T2V attention shape is BF16
`[batch=1, heads=40, sequence=75600, head=128]`. On the leased B200 host,
PyTorch automatic SDPA selected Flash SDPA at a 285.39 ms median while its
installed cuDNN SDPA backend measured 91.19 ms at the same shape, a 3.13x
operator speedup. Candidate output remained finite with maximum absolute BF16
delta 0.000244141625 and mean absolute delta 0.0000111.

`DIONY_WAN_ATTENTION_BACKEND=cudnn_sdpa` changes only the exact-attention
kernel selected by PyTorch. It does not change Wan weights, BF16 precision,
frames, steps, solver, guidance, seed, references, prompts, or negative
conditioning. If cuDNN cannot execute a call, the host overlay records a
warning and immediately re-executes that call through automatic SDPA.

Activation is intentionally two-stage:

1. An exact-shape operator receipt must prove at least 1.5x speedup, finite
   output, maximum absolute delta no greater than 0.0005, and mean absolute
   delta no greater than 0.00005 on the target B200.
2. One run-scoped 5-second quality canary must pass current reference QC,
   continuity/temporal QC, and a verified 1920x1080 Finish. Only then may
   `wan-cudnn-sdpa-validation.json` enable the remaining Takes in that Project.

The exact-shape operator command is:

```bash
python scripts/benchmark/measure_wan_attention.py \
  --project-id "$PROJECT_ID" \
  --approved-by "Named reviewer" \
  --output /diony/vault/qualification/wan-cudnn-sdpa-validation.json
```

It writes a held operator receipt only; it cannot promote its own quality canary.

Producer selects the backend per immutable Take rather than relying on the
Producer process environment captured at startup. At a Take boundary, measured
B200 operator proof may be rebound to the active Project and exactly one selected
Take. That Take runs cuDNN SDPA, measured AutoQC, and immediate verified 1080
Finish. A passing receipt activates the remaining Takes without restarting the
Producer. An eligible creative or audiovisual QC failure follows the normal
bounded immutable-Take AutoQC path: the operator proof is rebound only from the
failed selected Take to its newly selected retry, and the unchanged waiting
rundown remains queued. Runtime/infrastructure failure, a receipt-binding error,
or exhaustion of the four-Take cap holds the queue before another Shot dispatches.

The receipt is bound to one Project. It cannot activate another production.
Every remaining Take still runs the normal AutoQC and 1080 Finish gates. A
runtime failure falls back inside the same attention call; a picture-quality
failure holds Production and invalidates promotion. Thirty successful clips
are required only for general cross-Project promotion, not to unblock the
measured run-scoped canary.

## 2. Persistent native Wan lane

The accepted implementation boundary is process/device residency only:

```text
Producer sequential FARM queue
  -> validation receipt + deterministic canary gate
  -> one resident CUDA worker
  -> one cached task/checkpoint pipeline
  -> T5 + high-noise BF16 expert + low-noise BF16 expert remain resident
  -> exact pinned generate inputs per Take
  -> native 720p negative
  -> measured AutoQC
  -> failure: kill worker, remove only partial Take output, open circuit,
              rerun the same Take through pinned generate.py
```

Task or checkpoint changes drain the cached pipeline before loading the next one.
The resident worker supports receipt-qualified T2V-A14B, I2V-A14B, and S2V-A14B.
S2V carries the locked still, locked audio, one 84-frame inference window, exact
81-frame publish cap, and the same 24-step BF16/negative-conditioning inputs as
the pinned CLI. Animate, TI2V, offload sites, non-81-frame requests, and
non-24-step requests retain the official per-clip CLI. One worker never means
two concurrent clips.

Producer never reorders Shots across a previous-frame continuity dependency just
to group engines. Blueprint 20's exact-reference script lane is naturally a
homogeneous I2V sequence: one checkpoint, one resident pipeline, and the selected
prior Take's final frame handed directly to the next Shot. This removes task churn
without weakening authored scene order or reference lineage.

### Activation receipt

`DIONY_WAN_RESIDENT=validated` is necessary but insufficient. Producer also
requires `DIONY_WAN_RESIDENT_RECEIPT` to point to a JSON receipt that proves:

- at least 30 paired baseline/candidate clips;
- identical input lineage and quality-contract SHA-256;
- at least 15 percent p50 improvement;
- candidate p95 no worse than baseline;
- zero candidate OOMs;
- candidate retries no greater than baseline;
- quality equivalence passed for every pair;
- identical decoded-frame SHA-256 plus hashed AutoQC, verified-master, and blind-review evidence;
- an explicit `qualified_tasks` list; a receipt cannot activate an engine it did not measure;
- a named approver.

Qualification records model/pipeline initialization, first denoise step, steady
denoise step p50/p95, encode/publish time, peak VRAM, and end-to-end Take latency
separately. GPU utilization alone is not throughput proof, and the RunPod summary
panel is not accepted when it disagrees with direct `nvidia-smi` sampling.

The validator is:

```bash
python scripts/benchmark/validate_wan_residency.py \
  --results /vault/qualification/wan-resident-results.jsonl \
  --approved-by "Named reviewer" \
  --output /vault/qualification/wan-resident-validation.json
```

Invalid evidence cannot produce an activation receipt. The default canary is 10
percent, selected deterministically by Take path. A resident runtime failure opens
a durable vault circuit for that receipt, kills the resident worker, removes only the partial
output, and sends the same job through pinned `generate.py`. A new isolated receipt
is required to clear a receipt-scoped runtime rollback without restarting code.

The packaged GPU launcher discovers only the two named vault receipts above.
When neither exists, it clears resident environment inherited from an older
service and starts the pinned per-clip CLI. When one exists, it passes
`validated`, the exact receipt path, its matching kernel mode, and the 10 percent
canary into GPU Producer; `resident_decision` still validates the receipt before
any Take may enter the candidate lane. A file's presence is never approval.

### Regional compile candidate

`DIONY_WAN_KERNEL_MODE=regional_compile` compiles each repeated
`WanAttentionBlock` in both high/low-noise experts, or the S2V noise model, with
`fullgraph=True` and fixed shapes inside the persistent worker. T5, VAE, solver,
guidance, BF16 parameters, 24 denoise steps, and native output remain unchanged.
The eager resident worker remains the exact rollback.

An eager residency receipt cannot activate regional compilation. Qualification
must issue the distinct `persistent-bf16-regional-compile-v1` candidate:

```bash
python scripts/benchmark/validate_wan_residency.py \
  --results /vault/qualification/wan-regional-compile-results.jsonl \
  --kernel-mode regional_compile \
  --approved-by "Named reviewer" \
  --output /vault/qualification/wan-regional-compile-validation.json
```

The same 30-pair timing, decoded-frame hash, AutoQC, verified 1080 master, blind
review, OOM, retry, p50, and p95 gates apply. Compile time counts inside candidate
Take latency and remains visible as the cold candidate row. Failure opens the
receipt-scoped circuit and reruns the same Take through pinned eager `generate.py`.

## 3. Persistent streaming Finish

Finish caches one `diony_sr_v1` model by checkpoint path, size, and modification
time. A changed checkpoint invalidates the cache. Frame flow is bounded:

```text
FFmpeg raw RGB decoder -> one frame -> tiled RRDB x2 -> temporal warp/grain
  -> FFmpeg ProRes encoder -> verified temporary master -> atomic vault publish
```

The implementation retains only the current decoded frame, prior temporal state,
and the current finished frame. It does not retain the full 720 clip, full 1080
clip, or a joined raw byte buffer. A ProRes failure is a Finish failure; H.264 is
not a deliverable fallback.

Program stitching also writes ProRes 422 HQ 10-bit. Its overlap graph finishes
through the same 10-bit luma/chroma guard band and HQ macroblock budget as each
Take; `xfade` may not expand otherwise legal Take levels. Mix copies the verified
video stream while adding PCM 24-bit audio, so the audio-bearing program cannot
silently reduce the picture codec or bit depth.

## 4. Measured quality evidence

`QCReport` retains versioned temporal evidence:

- analyzed frame count;
- frame-delta p95;
- spatial residual p95 after removing coherent exposure change;
- global luminance-change p95.

`DIONY_MEASURED_QC_REQUIRED=1` makes missing evidence, incoherent temporal change,
or flicker a hard camera failure. It remains off until threshold calibration is
included in the isolated qualification. Reference SSIM and boundary continuity
remain independent required evidence.

Every 1080 Take and program master must independently verify:

- moving, structured decoded picture;
- exactly 1920x1080;
- ProRes codec and `yuv422p10le` pixel format;
- Rec.709 colorspace, primaries, transfer, and limited range;
- 23.976 or 24.000 fps.

For ProRes builds whose muxer does not expose `color_range=tv`, this is not a
metadata waiver. Finish decodes the complete 10-bit stream, aggregates
`signalstats` Y/Cb/Cr minima and maxima, and accepts only Y 64–940 plus Cb/Cr
64–960. The encoder applies a codec-safe guard band and maximum ProRes HQ
macroblock budget before this measurement so its decoded output—not merely its
filter declaration—is legal.

## 5. Observability and control ownership

Legacy camera and Finish stages append durable records to
`/diony/vault/.cluster/performance.jsonl`. `/metrics` reports completed, failed,
retry, average, p50, p95, and peak-VRAM values for those real stages in addition
to launch WorkflowRun metrics.

The default journal resolves the writable vault at record/read time, after the
packaged Desk has configured its Application Support vault. It must not freeze a
relative or pre-configuration path during module import; camera evidence and the
`/metrics` reader must always address the same durable file.

`GET /api/run` is observational. It cannot close gates, start Finish/Mix, or open
an SSH probe. A background controller owns legacy advancement and GPU sampling;
`POST /api/run/advance` is the explicit compatibility action. Browser polling may
therefore be frequent without becoming part of production correctness.

Every new negative also writes an atomic execution-lane sidecar. Producer binds
it to `Take.generation_receipt` only after checking the output hash, byte count,
and pinned quality fields. Production clip rows expose the actual lane, timing,
attention backend, inference frames per pass, denoise-pass count, effective
denoise steps, and fallback reason so held, doubled, or rolled-back acceleration
cannot look like an unexplained performance regression.

`GET /api/project` is also observational and must never persist or change
`.cluster/current.json`. `POST /api/projects/{project_id}/activate` is the only
explicit legacy operation that selects an existing vault Project. A durable
pause receipt drains after one immutable Take and holds every later dispatch;
infrastructure recovery reuses the same selected Take IDs and seeds and records
the interrupted STOP receipt instead of consuming a creative retake attempt.
Previous-frame I2V preflight resolves the same extracted prior-Take boundary
frame used by generation; chained Shots do not require duplicate canonical
keyframes. A Take is removed from the in-memory FARM queue only after preflight
has entered a handled dispatch path. Any unexpected pre-dispatch exception
requeues that Take at the head of the unchanged rundown, exposes a FARM error,
and stops dispatch. It may never restart threads while popping successive Takes
or report `Farm idle` after silently consuming waiting work.
GPU source stamps hash packaged file contents rather than package mtimes. A
Desk may sync and replace remote Producer only at a proven idle boundary: an
active Wan child, rolling FARM, busy Finish, or busy Mix makes replacement defer
and an unreadable remote status fails closed. The observational run payload
therefore exposes Mix busy state and treats it as protected rolling work. This
prevents opening a newly built App from interrupting an immutable Take, decoded
program-master verification, or a Mix bounce.

## 6. Rollout and rollback

1. Drain the current production at an immutable Take boundary.
2. Measure exact-shape attention kernels on the signed B200 and issue an
   operator-only, Project-scoped receipt.
3. Run one same-model 5-second canary through AutoQC and 1080 Finish; promote
   the receipt only if every picture gate passes.
4. Resume the remaining same selected Takes with cuDNN SDPA and per-Take QC.
5. Separately clone the pinned environment for resident-worker qualification;
   generate its receipt and start at a 10 percent canary.
6. Observe at least 30 resident canary clips. Roll back automatically on any
   resident exception, OOM, retry increase, p95 regression, or quality failure.
7. Increase resident rollout to 25, 50, then 100 percent only with new recorded approval.

Rollback never deletes completed Takes or changes a seed. It disables residency
and returns subsequent work to the pinned CLI. Approximate acceleration requires
a separate Blueprint and a visibly different non-deliverable or newly qualified
quality contract.
