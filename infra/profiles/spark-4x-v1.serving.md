# Serving — spark-4x-v1

Desk crate. Spark Cluster (4). Studio pack points here so the operator does not leave Diony.

- Brain: Blueprint 04 Spark TP=2 (SPLIT) and TP=4 (BRAIN). **Not** the GB300 card command.
- Compose: `infra/compose/brain.yml`, `split.yml`, `wan.yml`, `finish.yml`
- Weights: `infra/models/manifest.yaml` → `/models`
- Vault: `infra/storage/vault.fstab.example` → `/diony/vault`
- Producer: `http://producer.diony.local` (binds `:8788` on spark-01)
- Fabric owner: NVIDIA Sync. Diony does not replace it.
- After weight pull, drop outbound. No Dashscope. No hosted prompt extend.

Pin `max_model_len` 1048576. Do not advertise 1.5M.
