# Diony 1080 finish

`diony_sr_v1` runtime: tiled Real-ESRGAN x2 (temporal warp) → zscale 1920×1080 → Rec.709 limiter. Not lanczos as a master.

Finish keeps the unchanged checkpoint resident within its service process and
streams decode → RRDB/temporal processing → ProRes encode. Deliverables and
stitched programs are ProRes 422 HQ 10-bit Rec.709 limited range; an H.264
fallback cannot satisfy the master contract. See Blueprint 21.

```bash
pip install -e packages/contracts -e apps/finish
# After Studio App pulled /models/diony/diony_sr_v1
unset DASH_API_KEY
diony-finish   # :8020
```

Dual-Spark: drain Wan after the farm, then this process. Production starts FINISH.
