# Producer

Idea-to-Studio floor. Customers: feature, series, commercial, music video, education, social hero.

```bash
pip install -e packages/contracts -e apps/wan -e apps/finish -e apps/producer
# Laptop without weights: stubs
diony-producer   # http://producer.diony.local
# Dual-Spark spark-01 after Wizard App Continue (weights in /models, Wan2.2 in /opt/wan22)
export DIONY_VAULT=/diony/vault
export DIONY_BIND=0.0.0.0
export DIONY_WAN_REPO=/opt/wan22
export PYTHONPATH=/opt/wan22${PYTHONPATH:+:$PYTHONPATH}
export DIONY_PYTHON=$(command -v python3)
unset DASH_API_KEY DASHSCOPE_API_KEY
diony-producer
```

Sequential Dual-Spark: do not start `diony-wan` and brain together. Produce starts Wan for FARM. Picture lock starts `diony-finish` for 1080.

Pages: **Idea** → **Dailies** → **Program** → **Lock**. Idea has a **Length** goal: 30s, 1m, 3m, 5m, 10m, 15m, 20m, 30m, 40m, 1hr (default 5m). It is a goal, not a stopwatch. After Wizard Sign the mast shows the site. Dual-Spark is sequential SPLIT — do not quote the Quad-Spark 2–5 day SLA. **Produce** farms Wan2.2 **720p negatives** (not 1080 masters). **Picture lock** writes `finish_1080.mov` per take and `master_1080.mov` for the program. **Retake** cap 4. Optional **Prompt to edit** (Cut) stays VACE. Program screens the stitch. See Blueprints `04`, `06`, `12`, `13`, and `14`.

Projects persist under `{vault}/projects/{id}/project.json`. Sample loads Night Press. 720p Wan is camera negative. 1080 is Diony finish.
