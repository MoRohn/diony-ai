# Diony 1080 finish

`diony_sr_v1` runtime: tiled Real-ESRGAN x2 (temporal warp) → zscale 1920×1080 → Rec.709 limiter. Not lanczos as a master.

```bash
pip install -e packages/contracts -e apps/finish
# After Wizard App pulled /models/diony/diony_sr_v1
unset DASH_API_KEY
diony-finish   # :8020
```

Dual-Spark: drain Wan after picture lock, then this process. Producer Lock → picture starts FINISH.
