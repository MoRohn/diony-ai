# Infrastructure as a Product

Versioned desired state for SKU `spark-4x-v1` (Local Quad-Spark), `spark-2x-v1` (Leased Dual-Spark), `lease-gpu-v1` (signed four-node lease, not a Studio Site), `b300-v1` (Leased B300), and `b200-v1` (Leased B200). Blueprints `02`, `03`, `04`, `09`, `14` are the prose; these files are what Studio/Conductor apply.

| Path | What |
|---|---|
| `profiles/spark-4x-v1.yaml` | Local Quad-Spark crate (four GB10, TP=4 BRAIN, concurrent SPLIT) |
| `profiles/spark-2x-v1.yaml` | Leased Dual-Spark (two GB10, TP=2, sequential SPLIT) |
| `profiles/lease-gpu-v1.yaml` | Four-node leased GPU profile (not a Studio Site) |
| `profiles/b300-v1.yaml` | Leased B300 profile (TP=1, sequential SPLIT) |
| `profiles/b200-v1.yaml` | Leased B200 profile (TP=1, 180 GB, `max_model_len` 16384) |
| `profiles/*.serving.md` | Per-site serving notes (Studio Files) |
| `compose/` | brain / wan / split / finish service stubs |
| `switch/` | MikroTik 200G snippets, lease interconnect, Dual-Spark pair fabric, B300/B200 on-package |
| `storage/vault.fstab.example` | NAS mount |
| `models/manifest.yaml` | Weight ids, bytes, dests |
| `health/check_cluster.py` | Dry-run / live probe |

```bash
python3 infra/health/check_cluster.py
python3 infra/health/check_cluster.py --live   # on cluster after serving exists
```
