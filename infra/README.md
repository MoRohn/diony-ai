# Infrastructure as a Product

Versioned desired state for SKU `spark-4x-v1` (Local Quad-Spark), `spark-2x-v1` (Leased Dual-Spark), `lease-gpu-v1` (signed four-node lease, not a Wizard Site), and `b300-v1` (Leased B300). Blueprints `02`, `03`, `04`, `09`, `14` are the prose; these files are what Wizard/Conductor apply.

| Path | What |
|---|---|
| `profiles/spark-4x-v1.yaml` | Local Quad-Spark crate (four GB10, TP=4 BRAIN, concurrent SPLIT) |
| `profiles/spark-2x-v1.yaml` | Leased Dual-Spark (two GB10, TP=2, sequential SPLIT) |
| `profiles/lease-gpu-v1.yaml` | Four-node leased GPU profile (not a Wizard Site) |
| `profiles/b300-v1.yaml` | Leased B300 profile (TP=1, sequential SPLIT) |
| `profiles/*.serving.md` | Per-site serving notes (Wizard Files) |
| `compose/` | brain / wan / split / finish service stubs |
| `switch/` | MikroTik 200G snippets, lease interconnect, Dual-Spark pair fabric, B300 on-package |
| `storage/vault.fstab.example` | NAS mount |
| `models/manifest.yaml` | Weight ids, bytes, dests |
| `health/check_cluster.py` | Dry-run / live probe |

```bash
python3 infra/health/check_cluster.py
python3 infra/health/check_cluster.py --live   # on cluster after serving exists
```
