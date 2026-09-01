"""Google Flow adapter (via useapi.net Google Flow API v1).

POST /videos with async=true returns a jobId; GET /jobs/{jobId} reports
completion with media[].videoUrl and remainingCredits. Credits are the meter:
actuals reconcile from the remainingCredits delta when the API reports it,
falling back to the packaged rate card — and Flow cost is always labeled as
modeled from the user's plan, never as Google's official price.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from urllib.parse import quote

import httpx

from diony_contracts.director import DirectorOp
from diony_director.providers import base
from diony_director import ratecards
from diony_director.estimate import OMNI_MODELS

PROVIDER_ID = "google-flow"
CALL_TIMEOUT_S = 120.0
UPLOAD_TIMEOUT_S = 300.0

_ASSET_CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".mp4": "video/mp4",
}


def _guard(response: httpx.Response, what: str = "request") -> None:
    """Convert HTTP failures into CloudAdapterError carrying the provider's own words."""
    if response.status_code >= 400:
        raise base.CloudAdapterError(
            f"Flow {what} rejected ({response.status_code}): {response.text[:300]}"
        )


def api_base() -> str:
    return os.environ.get("DIONY_USEAPI_BASE", "https://api.useapi.net/v1/google-flow").rstrip("/")


def api_token() -> str:
    return os.environ.get("USEAPI_TOKEN", "").strip()


def account_email() -> str:
    return os.environ.get("DIONY_FLOW_EMAIL", "").strip()


def configured() -> bool:
    return bool(api_token())


def _headers() -> dict[str, str]:
    token = api_token()
    if not token:
        raise base.CloudAdapterError("USEAPI_TOKEN is not set")
    return {"Authorization": f"Bearer {token}"}


def submit_handle(
    external_id: str, *, model: str, duration_s: int, credits_before: int | None = None, **metadata
) -> base.CloudJobHandle:
    meta = dict(metadata)
    if credits_before is not None:
        meta["credits_before"] = credits_before
    # mode/resolution are dataclass fields, not metadata — the credit fallback
    # prices from handle.resolution, so dropping it silently billed 360p as 720p.
    mode = str(meta.pop("mode", "t2v") or "t2v")
    resolution = str(meta.pop("resolution", "720p") or "720p")
    return base.CloudJobHandle(
        provider_id=PROVIDER_ID,
        external_id=external_id,
        model=model,
        mode=mode,
        resolution=resolution,
        duration_s=duration_s,
        metadata=meta,
    )


# --- Media store: local files become Flow-hosted assets --------------------
#
# The Flow API references media exclusively by mediaGenerationId. A local path
# (plate, bridge frame, user upload) is uploaded once through POST /assets and
# cached by content hash; a raw path or URL never reaches a generation payload.


def _asset_cache_path() -> Path:
    from diony_contracts.cluster import cluster_root

    return cluster_root() / "director" / ".flow_assets.json"


def _asset_cache() -> dict[str, str]:
    try:
        body = json.loads(_asset_cache_path().read_text())
        return body if isinstance(body, dict) else {}
    except (OSError, ValueError):
        return {}


def _remember_asset(sha: str, media_id: str) -> None:
    cache = _asset_cache()
    cache[sha] = media_id
    path = _asset_cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cache, indent=1, sort_keys=True))
    except OSError:
        pass  # the upload still succeeded; worst case is a re-upload later


def upload_asset(path: Path) -> str:
    """Upload one local file, returning its mediaGenerationId (content-hash cached)."""

    content_type = _ASSET_CONTENT_TYPES.get(path.suffix.lower())
    if content_type is None:
        raise base.CloudAdapterError(
            f"Flow assets accept PNG/JPEG/WebP/MP4 — cannot upload {path.name}"
        )
    payload = path.read_bytes()
    sha = hashlib.sha256(payload).hexdigest()
    cached = _asset_cache().get(sha)
    if cached:
        return cached
    url = f"{api_base()}/assets"
    if account_email():
        url += f"/{quote(account_email(), safe='')}"
    headers = _headers()
    headers["Content-Type"] = content_type
    response = base.post_with_retries(url, content=payload, headers=headers, timeout=UPLOAD_TIMEOUT_S)
    _guard(response, "asset upload")
    body = response.json()
    media_id = body.get("mediaGenerationId")
    if isinstance(media_id, dict):
        media_id = media_id.get("mediaGenerationId") or media_id.get("id") or ""
    media_id = str(media_id or "")
    if not media_id:
        raise base.CloudAdapterError(f"Flow asset upload returned no mediaGenerationId: {body}")
    _remember_asset(sha, media_id)
    return media_id


def _media_ref(value: str, *, what: str) -> str:
    """Resolve one media value into something the Flow API accepts.

    Local file → uploaded asset id. Hosted-looking ids pass through. URLs and
    dangling paths are refused loudly — Flow has no URL intake, and a silent
    pass-through was an illegal request the tests never saw."""

    value = str(value)
    candidate = Path(value)
    if candidate.is_file():
        return upload_asset(candidate)
    if value.startswith(("http://", "https://", "data:")):
        raise base.CloudAdapterError(
            f"Flow {what} must be a Flow-hosted mediaGenerationId, not a URL: {value[:80]}"
        )
    if (os.sep in value or "/" in value) and not candidate.exists():
        raise base.CloudAdapterError(f"Flow {what} points at a missing local file: {value[:120]}")
    return value


def submit(op: DirectorOp) -> base.CloudJobHandle:
    base.ensure_prompt_fits(op.prompt, PROVIDER_ID)
    is_v2v = op.mode is not None and op.mode.value == "v2v_edit"
    payload: dict = {
        "prompt": op.prompt,
        "model": op.model,
        "aspectRatio": op.artifact_refs.get("aspect_ratio", "landscape"),
        "async": True,
    }
    if not is_v2v:
        # v2v rejects the duration parameter — output length comes from the
        # source's trim window, not from a booking.
        payload["duration"] = int(op.duration_s)
    if op.model in OMNI_MODELS and op.resolution:
        payload["resolution"] = op.resolution
    if account_email():
        payload["email"] = account_email()
    start_image = op.artifact_refs.get("start_image")
    if start_image:
        payload["startImage"] = _media_ref(start_image, what="startImage")
    end_image = op.artifact_refs.get("end_image")
    if end_image:
        payload["endImage"] = _media_ref(end_image, what="endImage")
    for index, ref in enumerate(op.artifact_refs.get("references") or [], start=1):
        payload[f"referenceImage_{index}"] = _media_ref(ref, what=f"referenceImage_{index}")
    for index, character in enumerate(op.artifact_refs.get("characters") or [], start=1):
        payload[f"character_{index}"] = character
    for index, voice in enumerate(op.artifact_refs.get("voices") or [], start=1):
        payload[f"referenceAudio_{index}"] = voice
    source_video = op.artifact_refs.get("source_video")
    if source_video and op.mode is not None and op.mode.value == "v2v_edit":
        payload["referenceVideo_1"] = _media_ref(source_video, what="referenceVideo_1")

    response = base.post_with_retries(
        f"{api_base()}/videos", json=payload, headers=_headers(), timeout=CALL_TIMEOUT_S
    )
    _guard(response)
    body = response.json()
    external_id = str(body.get("jobId") or body.get("jobid") or "")
    if not external_id:
        raise base.CloudAdapterError(f"Flow returned no jobId: {body}")
    credits_before = body.get("remainingCredits")
    return submit_handle(
        external_id,
        model=op.model,
        duration_s=int(op.duration_s),
        credits_before=credits_before if isinstance(credits_before, int) else None,
        mode=op.mode.value if op.mode else "t2v",
        resolution=op.resolution,
    )


# POST /images speaks a different dialect from /videos (verified live
# 2026-09-01 — a production's plates all 400'd on it): synchronous with NO
# async parameter; aspect ratios are ratio strings ("16:9"), never
# landscape/portrait; references are reference_N, not referenceImage_N; and
# count DEFAULTS TO 4 — unpinned, every plate would bill four images.
_IMAGE_ASPECTS = {"landscape": "16:9", "portrait": "9:16", "": "16:9"}


def submit_image(
    prompt: str,
    *,
    model: str = "nano-banana-2",
    references: list[str] | None = None,
    aspect_ratio: str = "landscape",
) -> base.CloudJobHandle:
    base.ensure_prompt_fits(prompt, PROVIDER_ID, what="image prompt")
    aspect = _IMAGE_ASPECTS.get(aspect_ratio, aspect_ratio)
    payload: dict = {"prompt": prompt, "model": model, "aspectRatio": aspect, "count": 1}
    if account_email():
        payload["email"] = account_email()
    for index, ref in enumerate(references or [], start=1):
        payload[f"reference_{index}"] = _media_ref(ref, what=f"reference_{index}")
    response = base.post_with_retries(
        f"{api_base()}/images", json=payload, headers=_headers(), timeout=CALL_TIMEOUT_S
    )
    _guard(response)
    body = response.json()
    external_id = str(body.get("jobId") or body.get("jobid") or "")
    if not external_id:
        raise base.CloudAdapterError(f"Flow returned no jobId for image: {body}")
    credits_before = body.get("remainingCredits")
    handle = submit_handle(
        external_id,
        model=model,
        duration_s=0,
        credits_before=credits_before if isinstance(credits_before, int) else None,
        kind="image",
    )
    # The result arrives IN this response: media[].image.generatedImage with a
    # signed fifeUrl (~6h validity) and the mediaGenerationId later payloads
    # reference. Capture both so poll never has to guess.
    nested = body.get("response") if isinstance(body.get("response"), dict) else {}
    media = body.get("media") or nested.get("media") or []
    if media and isinstance(media[0], dict):
        generated = ((media[0].get("image") or {}).get("generatedImage") or {})
        sync_url = str(generated.get("fifeUrl") or "")
        media_generation_id = str(generated.get("mediaGenerationId") or "")
        if sync_url:
            handle.metadata["sync_result_url"] = sync_url
        if media_generation_id:
            handle.metadata["media_generation_id"] = media_generation_id
    return handle


def register_character(name: str, image_media_ids: list[str]) -> str:
    # /characters dialect (docs, aligned with the /images correction):
    # displayName + imageReference_N, synchronous, returns entityId.
    payload: dict = {"displayName": name}
    if account_email():
        payload["email"] = account_email()
    for index, ref in enumerate(image_media_ids, start=1):
        payload[f"imageReference_{index}"] = _media_ref(ref, what=f"imageReference_{index}")
    response = base.post_with_retries(
        f"{api_base()}/characters", json=payload, headers=_headers(), timeout=CALL_TIMEOUT_S
    )
    _guard(response)
    body = response.json()
    ref = str(body.get("entityId") or body.get("character") or body.get("ref") or body.get("id") or "")
    if not ref:
        raise base.CloudAdapterError(f"Flow returned no character ref: {body}")
    return ref


def register_voice(name: str, audio_media_id: str) -> str:
    payload: dict = {"name": name, "referenceAudio": audio_media_id}
    if account_email():
        payload["email"] = account_email()
    response = base.post_with_retries(
        f"{api_base()}/voices", json=payload, headers=_headers(), timeout=CALL_TIMEOUT_S
    )
    _guard(response)
    body = response.json()
    ref = str(body.get("ref") or body.get("id") or "")
    if not ref:
        raise base.CloudAdapterError(f"Flow returned no voice ref: {body}")
    return ref


def submit_extend(
    media_generation_id: str, *, prompt: str, duration_s: int, model: str = "veo-3.1-fast"
) -> base.CloudJobHandle:
    if model in OMNI_MODELS:
        raise base.CloudAdapterError(
            "omni-flash cannot perform an extension — extend runs on a Veo lane "
            "(and omni-flash output can never be extended; fix it with a v2v edit)"
        )
    if Path(str(media_generation_id)).is_file():
        raise base.CloudAdapterError(
            "Flow extend needs the mediaGenerationId of a Veo-generated video — an "
            "uploaded local file cannot be extended"
        )
    payload: dict = {
        "mediaGenerationId": media_generation_id,
        "prompt": prompt,
        "duration": int(duration_s),
        "async": True,
    }
    if account_email():
        payload["email"] = account_email()
    response = base.post_with_retries(
        f"{api_base()}/videos/extend", json=payload, headers=_headers(), timeout=CALL_TIMEOUT_S
    )
    _guard(response)
    body = response.json()
    external_id = str(body.get("jobId") or body.get("jobid") or "")
    if not external_id:
        raise base.CloudAdapterError(f"Flow returned no jobId for extend: {body}")
    credits_before = body.get("remainingCredits")
    return submit_handle(
        external_id,
        model=model,
        duration_s=int(duration_s),
        credits_before=credits_before if isinstance(credits_before, int) else None,
        kind="extend",
    )


def submit_upscale(media_generation_id: str, *, resolution: str = "1080p") -> base.CloudJobHandle:
    # No email parameter here — /videos/upscale 400s on it (verified live);
    # the mediaGenerationId already names the owning account.
    payload: dict = {
        "mediaGenerationId": media_generation_id,
        "resolution": resolution,
        "async": True,
    }
    response = base.post_with_retries(
        f"{api_base()}/videos/upscale", json=payload, headers=_headers(), timeout=CALL_TIMEOUT_S
    )
    _guard(response)
    body = response.json()
    external_id = str(body.get("jobId") or body.get("jobid") or "")
    if not external_id:
        raise base.CloudAdapterError(f"Flow returned no jobId for upscale: {body}")
    credits_before = body.get("remainingCredits")
    return submit_handle(
        external_id,
        model="video-upscale",
        duration_s=0,
        credits_before=credits_before if isinstance(credits_before, int) else None,
        kind="upscale",
        resolution=resolution,
    )


def poll(handle: base.CloudJobHandle) -> base.CloudJobStatus:
    sync_url = handle.metadata.get("sync_result_url")
    if sync_url:
        # images complete inside their own submit response — no job to poll
        metadata: dict = {}
        if handle.metadata.get("media_generation_id"):
            metadata["media_generation_id"] = handle.metadata["media_generation_id"]
        return base.CloudJobStatus(state="done", result_url=str(sync_url), metadata=metadata)
    response = base.get_with_retries(
        f"{api_base()}/jobs/{handle.external_id}", headers=_headers(), timeout=CALL_TIMEOUT_S
    )
    _guard(response)
    body = response.json()
    raw_state = str(body.get("status", "")).lower()
    detail = str(body.get("error") or "")
    # Live shape (verified on a real job): media and remainingCredits nest
    # under "response"; older docs showed them at the top level — read both.
    nested = body.get("response") if isinstance(body.get("response"), dict) else {}
    media = nested.get("media") or body.get("media") or []
    url = ""
    metadata: dict = {}
    if media and isinstance(media, list):
        first = media[0] if isinstance(media[0], dict) else {}
        url = str(first.get("videoUrl") or first.get("imageUrl") or first.get("url") or "")
        media_generation_id = str(
            first.get("mediaGenerationId") or first.get("name") or ""
        )
        if media_generation_id:
            metadata["media_generation_id"] = media_generation_id
    remaining = nested.get("remainingCredits", body.get("remainingCredits"))
    if isinstance(remaining, int):
        metadata["credits_after"] = remaining
    if raw_state in {"completed", "done"} or (url and not raw_state):
        if not url:
            return base.CloudJobStatus(state="failed", detail="completed without a media URL")
        return base.CloudJobStatus(state="done", result_url=url, metadata=metadata)
    if raw_state in {"failed", "error"}:
        return base.CloudJobStatus(state="failed", detail=detail or "generation failed", metadata=metadata)
    if raw_state in {"queued", "pending"}:
        return base.CloudJobStatus(state="queued", metadata=metadata)
    return base.CloudJobStatus(state="running", detail=raw_state, metadata=metadata)


def credits_used(handle: base.CloudJobHandle, status: base.CloudJobStatus) -> int:
    before = handle.metadata.get("credits_before")
    after = status.metadata.get("credits_after")
    if isinstance(before, int) and isinstance(after, int) and after <= before:
        return before - after
    files = ratecards.load_files()
    duration = handle.duration_s or 8
    resolution = handle.resolution or "720p"
    if handle.model in OMNI_MODELS:
        unit = f"generation:{resolution}:{duration}s"
    else:
        unit = "generation"
    try:
        return ratecards.resolve(files, PROVIDER_ID, handle.model, unit).internal_credits_per_unit
    except ratecards.RateCardMissing:
        return 0


def fetch(handle: base.CloudJobHandle, status: base.CloudJobStatus, dest: Path) -> base.FetchedClip:
    if status.state != "done":
        raise base.CloudAdapterError(f"cannot fetch a {status.state} job")
    clip = base.download(status.result_url, dest)
    clip.metadata["credits_used"] = credits_used(handle, status)
    return clip
