# Studio

Primary **crate-to-system** preparation. Line 01 is **Site**: **Local Quad-Spark Cluster**, **Leased Dual-Spark Cluster**, **Leased B300**, or **Leased B200**. Local keeps the crate rail. Dual-Spark, B300, and B200 are Site → Connect → App → Ready → Producer. B200/B300 Connect lists the RunPod Connect tab (Use this / Skip / Same). Copy **SSH over exposed TCP** into Host, SSH user, SSH port. Vault is Details → `/workspace`. App Continue mounts `/diony/vault`, pulls the pinned DeepSeek 0731 + Wan2.2 A14B + `diony_sr_v1` pack (Quad TP=4 on Producer, Dual TP=2, B300 TP=1, B200 TP=1 / 16k) and starts Producer (`DIONY_VAULT` + `DIONY_BIND`). Ready **Test GPU** reads `nvidia-smi` over that SSH line (pytest never opens a socket). If Pack is red, **Pull pack** on Ready (desk `DIONY_DRY_RUN=1` does not skip a real IP). Sign writes `{vault}/.cluster/site.json` (and copies it onto the GPU vault when live). **Continue** on every line except the last. Leases are never the default. Fabric netplan stays dry-run on a laptop.

First open: **START HERE.txt**, then double-click **Open Diony AI** in the repo folder. START HERE names Local Quad-Spark, or a lease from Runpod first (`https://www.runpod.io/`) then Enverge (`https://enverge.ai/`) for Dual-Spark / B300 / B200. Then:

```bash
pip install -e packages/contracts -e apps/wizard
diony-wizard   # http://studio.diony.local
```

You cannot continue a line with empty fields or a missed check. You cannot skip ahead. **Refresh** on Site clears this Studio and reloads the page. **Start over** in the mast does the same. Browser refresh does not. NVIDIA Sync still owns fabric on the local crate. See Blueprints `01`, `11`, and `14`.
