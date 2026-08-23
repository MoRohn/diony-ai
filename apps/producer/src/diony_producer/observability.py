"""Structured controller logging with request/workflow correlation context."""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar, Token
from datetime import datetime, timezone
from typing import Any


_correlation: ContextVar[str] = ContextVar("diony_correlation_id", default="")
logger = logging.getLogger("diony.control")
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)
logger.propagate = False


def bind_correlation(correlation_id: str) -> Token[str]:
    return _correlation.set(correlation_id)


def reset_correlation(token: Token[str]) -> None:
    _correlation.reset(token)


def log_event(event: str, *, level: str = "info", **fields: Any) -> None:
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": level,
        "event": event,
        "correlation_id": fields.pop("correlation_id", "") or _correlation.get(),
        **fields,
    }
    method = getattr(logger, level if level in {"debug", "info", "warning", "error"} else "info")
    method(json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str))
