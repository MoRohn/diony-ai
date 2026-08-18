# Serving — lease-gpu-v1

Four matched leased GPUs. Same Diony as the desk. This file is the lease recipe; do not open NVIDIA or provider docs to finish prep.

- Brain: datacenter class for the GPU you signed (H100 / H200 / B200 / GB200 / B300 / GB10). **Not** Spark TP=2/TP=4 flags.
- If the class is GB10, NVIDIA Sync still owns fabric. Otherwise the provider owns fabric; Diony still wraps it.
- Compose stubs: `infra/compose/brain.yml` (layout only). Pin lease flags in Week 2+. Do not paste a 4×GB300 `--data-parallel-size 4` command onto Spark-sized leases, or Spark flags onto B200.
- Weights: `infra/models/manifest.yaml` on node-01, then replicate. Same checksums as the desk.
- Vault: private NFS/block/object at `/diony/vault`. Use `infra/storage/vault.fstab.example` as the shape. Never public S3 as the bible.
- Producer: `http://producer.diony.local` (binds `:8788` on node-01)
- After pull, drop outbound. Unset `DASH_API_KEY`. No Dashscope. Brain is DeepSeek on the four nodes.

SPLIT remains two brain + two Wan. BRAIN remains TP=4 across four matched nodes.

## Weight pull (node-01)

```bash
huggingface-cli download deepseek-ai/DeepSeek-V4-Flash-0731 \
  --local-dir /models/deepseek/DeepSeek-V4-Flash-0731
# Then Wan family per infra/models/manifest.yaml. Fill sha256. Replicate.
```
