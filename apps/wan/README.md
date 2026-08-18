# Wan camera worker

Week 2+ live Wan2.2. Official `generate.py`. Native **720p**. Never Dashscope.

```bash
pip install -e packages/contracts -e apps/wan
# After Wizard App pack pull and clone Wan2.2 to /opt/wan22:
export DIONY_WAN_REPO=/opt/wan22
export PYTHONPATH=/opt/wan22${PYTHONPATH:+:$PYTHONPATH}
unset DASH_API_KEY DASHSCOPE_API_KEY
diony-wan   # :8010
```

Dual-Spark: drain DeepSeek before this process. Producer `Produce` enters FARM and calls this worker (or `generate.py` on spark-01).
