"""Diony-Enterprise credentials owned by the backend.

The Hugging Face token below is the Diony-Enterprise HF Token. Studio and the
apply engine always use it for the pinned model pack; operators never paste a
token on Compute, App, or Ready. `DIONY_ENTERPRISE_HF_TOKEN` in the environment
overrides it for rotation without a code change. The token never enters public
API state, the workbook, `site.json`, signed reports, or error text.
"""

from __future__ import annotations

import os

DIONY_ENTERPRISE_HF_TOKEN = "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"


def enterprise_hf_token() -> str:
    """The Diony-Enterprise HF Token (env override first)."""
    return os.environ.get("DIONY_ENTERPRISE_HF_TOKEN", "").strip() or DIONY_ENTERPRISE_HF_TOKEN
