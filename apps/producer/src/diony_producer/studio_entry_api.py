"""Public, rate-limited Studio invitation review and acceptance API."""

from __future__ import annotations

import hashlib
import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from diony_producer.control_plane import ControlPlane, DocumentNotFound
from diony_producer.studio_invites import StudioInvitationService

router = APIRouter(prefix="/api/studio-entry", tags=["studio-entry"])

_entry_hits: dict[str, deque[float]] = defaultdict(deque)
_entry_hits_lock = Lock()


class InvitationTokenBody(BaseModel):
    token: str = Field(min_length=24, max_length=512)


class VerificationConfirmBody(InvitationTokenBody):
    code: str = Field(min_length=6, max_length=12)


class JoinBody(InvitationTokenBody):
    verification_ticket: str = Field(min_length=24, max_length=512)
    profile_name: str = Field(min_length=2, max_length=60)


def _service() -> StudioInvitationService:
    return StudioInvitationService(ControlPlane())


def _rate_limit(
    request: Request,
    token: str,
    action: str,
    *,
    limit: int,
    window_seconds: int,
) -> None:
    client = request.client.host if request.client else "unknown"
    token_key = hashlib.sha256(token.encode()).hexdigest()[:16]
    key = f"{client}:{token_key}:{action}"
    now = time.monotonic()
    with _entry_hits_lock:
        hits = _entry_hits[key]
        while hits and hits[0] <= now - window_seconds:
            hits.popleft()
        if len(hits) >= limit:
            retry_after = max(1, int(window_seconds - (now - hits[0])))
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Too many attempts reached this Studio. Wait before trying again.",
                headers={"Retry-After": str(retry_after)},
            )
        hits.append(now)


def _http_error(exc: Exception) -> HTTPException:
    message = str(exc)
    code = status.HTTP_400_BAD_REQUEST
    if "expired" in message:
        code = status.HTTP_410_GONE
    elif "closed the invitation" in message or "already" in message:
        code = status.HTTP_409_CONFLICT
    elif "not accepting" in message:
        code = status.HTTP_403_FORBIDDEN
    return HTTPException(code, message)


@router.post("/discover")
def discover(body: InvitationTokenBody, request: Request):
    _rate_limit(request, body.token, "discover", limit=30, window_seconds=60)
    try:
        return _service().discover(body.token)
    except (ValueError, RuntimeError, DocumentNotFound) as exc:
        raise _http_error(exc) from exc


@router.post("/verification/request", status_code=status.HTTP_202_ACCEPTED)
def request_verification(body: InvitationTokenBody, request: Request):
    _rate_limit(request, body.token, "verification-request", limit=5, window_seconds=600)
    try:
        return _service().request_email_verification(body.token)
    except (ValueError, RuntimeError, DocumentNotFound) as exc:
        raise _http_error(exc) from exc


@router.post("/verification/confirm")
def confirm_verification(body: VerificationConfirmBody, request: Request):
    _rate_limit(request, body.token, "verification-confirm", limit=10, window_seconds=600)
    try:
        return _service().confirm_email_verification(body.token, body.code)
    except (ValueError, RuntimeError, DocumentNotFound) as exc:
        raise _http_error(exc) from exc


@router.post("/join")
def join(body: JoinBody, request: Request):
    _rate_limit(request, body.token, "join", limit=10, window_seconds=600)
    try:
        return _service().accept_invitation(
            token=body.token,
            verification_ticket=body.verification_ticket,
            profile_name=body.profile_name,
            correlation_id=str(getattr(request.state, "correlation_id", "")),
        )
    except (ValueError, RuntimeError, DocumentNotFound) as exc:
        raise _http_error(exc) from exc
