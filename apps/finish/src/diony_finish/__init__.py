"""Diony finish — 720p Wan negative → 1920×1080 Rec.709 legal master.

diony_sr_v1 is this runtime: tiled x2 neural SR, temporal warp, zscale to 1080,
studio limiter. Bootstrap checkpoint is Real-ESRGAN x2 (BSD-3) at
/models/diony/diony_sr_v1 until the proprietary net is pulled.
Never lanczos as a deliverable.
"""

from __future__ import annotations

__version__ = "0.1.0"
