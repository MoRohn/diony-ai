# Wizard

Primary **crate-to-system** preparation. Line 01 is **Site**: **Local Quad-Spark Cluster**, **Leased Dual-Spark Cluster**, or **Leased B300**. Local keeps the crate rail. Dual-Spark and B300 are Site → Connect → App → Producer. Connect is hosts + vault + SSH user. App Continue pulls the pinned DeepSeek 0731 + Wan2.2 A14B + `diony_sr_v1` pack (Quad TP=4 on Producer, Dual TP=2, B300 TP=1) and starts Producer (`DIONY_VAULT` + `DIONY_BIND` on Dual-Spark and B300). Sign writes `{vault}/.cluster/site.json`. **Continue** on every line except the last. Leases are never the default. Fabric netplan stays dry-run on a laptop.

```bash
pip install -e packages/contracts -e apps/wizard
diony-wizard   # http://wizard.diony.local
```

You cannot continue a line with empty fields or a missed check. You cannot skip ahead. NVIDIA Sync still owns fabric on the local crate. See Blueprints `01`, `11`, and `14`.
