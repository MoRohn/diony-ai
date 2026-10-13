"""Grok Imagine Video adapter (xAI).

Async contract: POST /v1/videos/generations (and /edits, /extensions later)
returns a request id; GET /v1/videos/{request_id} reports done|failed|expired.
Result URLs expire — the caller must download the moment a poll reports done.
Endpoint shapes are configurable via DIONY_XAI_BASE and re-verified against
live docs before any billable rollout.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx

from diony_contracts.director import DirectorOp
from diony_director.providers import base

PROVIDER_ID = "grok"
CALL_TIMEOUT_S = 60.0


def _guard(response: httpx.Response, what: str = "request") -> None:
    """Convert HTTP failures into CloudAdapterError carrying the provider's own words."""
    if response.status_code == 429:
        # rate/quota limit: transient by nature — hold and resume, never fail
        code, why = base.quota_reason(response)
        raise base.CloudAdapterQuotaError(
            f"xAI {what} held by rate limit (429): {why}" + (f" [{code}]" if code else ""),
            retry_at=base.quota_retry_at(response),
            reason_code=code,
        )
    if response.status_code >= 400:
        raise base.CloudAdapterError(
            f"xAI {what} rejected ({response.status_code}): {response.text[:300]}"
        )


def api_base() -> str:
    return os.environ.get("DIONY_XAI_BASE", "https://api.x.ai").rstrip("/")


def api_key() -> str:
    return os.environ.get("XAI_API_KEY", "").strip()


def configured() -> bool:
    return bool(api_key())


def _headers() -> dict[str, str]:
    key = api_key()
    if not key:
        raise base.CloudAdapterError("XAI_API_KEY is not set")
    return {"Authorization": f"Bearer {key}"}


def submit_handle(
    external_id: str, *, model: str, duration_s: int, **metadata
) -> base.CloudJobHandle:
    return base.CloudJobHandle(
        provider_id=PROVIDER_ID,
        external_id=external_id,
        model=model,
        duration_s=duration_s,
        metadata=dict(metadata),
    )


MAX_REFERENCE_IMAGES = 3  # per docs.x.ai reference-to-video
MAX_REFERENCE_AUDIOS = 3  # preset voices, addressed as <AUDIO_0>..<AUDIO_2> in the prompt


def _video_input(value: str) -> dict:
    """Source-video object per the extensions contract: url | base64 | file_id.

    A local vault path (the persisted copy of a fetched clip) is base64'd
    inline — provider result URLs expire, so the vault copy is the only source
    that is still valid by the time a revision runs."""

    if value.startswith("data:"):
        return {"base64": value.split(",", 1)[-1]}
    if value.startswith("file-") or value.startswith("file_"):
        return {"file_id": value}
    from pathlib import Path as _Path

    candidate = _Path(value)
    if candidate.is_file():
        import base64 as _b64

        return {"base64": _b64.b64encode(candidate.read_bytes()).decode()}
    return {"url": value}


def _with_legend(prompt: str, op: DirectorOp, sent_references: int) -> str:
    """Blueprint 32: name what each <IMAGE_n> IS (docs.x.ai reference-to-video
    tags images in the prompt). Appended only when roles rode the op and the
    references actually ship; dropped whole when it would not fit — the
    stills still carry the identity, the words are the courtesy."""
    roles = list(op.artifact_refs.get("reference_roles") or [])[:sent_references]
    if not roles or sent_references == 0:
        return prompt
    from diony_director import cast as cast_mod

    legend = cast_mod.legend(roles, "grok")
    if not legend:
        return prompt
    candidate = f"{prompt} {legend}"
    return candidate if len(candidate) <= base.prompt_limit(PROVIDER_ID) else prompt


def submit(op: DirectorOp) -> base.CloudJobHandle:
    base.ensure_prompt_fits(op.prompt, PROVIDER_ID)
    payload: dict = {
        "model": op.model,
        "prompt": op.prompt,
        "duration": int(op.duration_s),
    }
    if op.resolution:
        payload["resolution"] = op.resolution
    aspect_ratio = op.artifact_refs.get("aspect_ratio")
    if aspect_ratio:
        payload["aspect_ratio"] = aspect_ratio
    if op.artifact_refs.get("generate_audio") is False:
        payload["generate_audio"] = False
    start_image = op.artifact_refs.get("start_image")
    references = op.artifact_refs.get("references") or []
    if start_image:
        # image and reference_images are mutually exclusive modes; still-lock wins.
        # REST wants a typed object, not a bare string (verified live: string → 422).
        payload["image"] = {"url": _data_uri(str(start_image))}
    elif references:
        payload["reference_images"] = [
            {"url": _data_uri(str(ref))} for ref in references[:MAX_REFERENCE_IMAGES]
        ]
        payload["prompt"] = _with_legend(op.prompt, op, len(payload["reference_images"]))
    voices = op.artifact_refs.get("grok_voices") or []
    if voices:
        payload["reference_audios"] = [{"voice_id": v} for v in voices[:MAX_REFERENCE_AUDIOS]]
    response = base.post_with_retries(
        f"{api_base()}/v1/videos/generations",
        what="xAI video submit",
        json=payload,
        headers=_headers(),
        timeout=CALL_TIMEOUT_S,
    )
    _guard(response)
    body = response.json()
    external_id = str(body.get("request_id") or body.get("id") or "")
    if not external_id:
        raise base.CloudAdapterError(f"Grok returned no request id: {body}")
    handle = base.CloudJobHandle(
        provider_id=PROVIDER_ID,
        external_id=external_id,
        model=op.model,
        mode=op.mode.value if op.mode else "t2v",
        resolution=op.resolution,
        duration_s=int(op.duration_s),
    )
    if start_image and references:
        # The still-lock won and the identity refs were dropped — that
        # degradation must live in the record, not vanish silently.
        handle.metadata["references_dropped"] = len(references)
    return handle


def _submit_operation(
    path: str, payload: dict, *, model: str, duration_s: int, kind: str
) -> base.CloudJobHandle:
    base.ensure_prompt_fits(str(payload.get("prompt", "")), PROVIDER_ID, what=f"{kind} prompt")
    response = base.post_with_retries(
        f"{api_base()}{path}",
        what=f"xAI {kind} submit",
        json=payload,
        headers=_headers(),
        timeout=CALL_TIMEOUT_S,
    )
    _guard(response)
    body = response.json()
    external_id = str(body.get("request_id") or body.get("id") or "")
    if not external_id:
        raise base.CloudAdapterError(f"Grok returned no request id for {kind}: {body}")
    return base.CloudJobHandle(
        provider_id=PROVIDER_ID,
        external_id=external_id,
        model=model,
        mode=kind,
        duration_s=duration_s,
        metadata={"kind": kind},
    )


IMAGE_MODEL = "grok-imagine-image-2.0"


def _data_uri(path_or_ref: str) -> str:
    """Local still paths become base64 data URIs; anything else passes through."""

    import base64 as _b64
    from pathlib import Path as _Path

    candidate = _Path(path_or_ref)
    if candidate.is_file():
        suffix = candidate.suffix.lstrip(".") or "png"
        encoded = _b64.b64encode(candidate.read_bytes()).decode()
        return f"data:image/{suffix};base64,{encoded}"
    return path_or_ref


def submit_image(
    prompt: str,
    *,
    model: str = IMAGE_MODEL,
    references: list[str] | None = None,
    aspect_ratio: str = "landscape",
    resolution: str = "",
    quality: str = "low",
) -> base.CloudJobHandle:
    """xAI image generation is synchronous — the handle carries the result.

    ``resolution`` ("1k"|"2k") is sent when given so the request matches what
    the rate card priced — the default is 1k, and paying 2k rates for default
    output was a silent ledger lie. ``quality`` is pinned to low, the
    generation default xAI documents for auto (read 2026-10-13), so a 2K
    still bills the low unit and not whatever auto picks next.
    """

    base.ensure_prompt_fits(prompt, PROVIDER_ID, what="image prompt")
    payload: dict = {"model": model, "prompt": prompt, "response_format": "url"}
    if resolution in {"1k", "2k"}:
        payload["resolution"] = resolution
    if quality in {"low", "medium"}:
        payload["quality"] = quality
    if aspect_ratio in {"16:9", "9:16", "1:1"}:
        # plates and sheets are born at the program aspect — a landscape plate
        # centre-cropped to 9:16 throws away most of the set
        payload["aspect_ratio"] = aspect_ratio
    refs = list(references or [])
    if refs:
        payload["image_url"] = _data_uri(refs[0])
    response = base.post_with_retries(
        f"{api_base()}/v1/images/generations",
        what="xAI image submit",
        json=payload,
        headers=_headers(),
        timeout=CALL_TIMEOUT_S,
    )
    _guard(response)
    body = response.json()
    data = body.get("data") or []
    url = str((data[0] if data else {}).get("url") or body.get("url") or "")
    if not url:
        raise base.CloudAdapterError(f"Grok returned no image URL: {body}")
    return base.CloudJobHandle(
        provider_id=PROVIDER_ID,
        external_id=f"img-{abs(hash(url)) % 10**10}",
        model=model,
        mode="image",
        duration_s=0,
        metadata={"sync_result_url": url},
    )


MAX_EDIT_IMAGES = 5  # docs.x.ai multi-image editing: up to five source images


def submit_image_edit(
    prompt: str,
    images: list[str],
    *,
    model: str = IMAGE_MODEL,
    aspect_ratio: str = "",
    resolution: str = "",
    quality: str = "medium",
) -> base.CloudJobHandle:
    """xAI image EDITING (`/v1/images/edits`), synchronous like generation.

    Blueprint 32's board sheet and cast keyframes are drawn FROM stills — an
    edit preserves the identity in the source pixels where a generation
    with one loose reference re-imagines it. Wire shape per docs.x.ai
    (image editing / multi-image editing): one source rides as ``image``
    ``{url, type}``; several ride as ``image_urls`` (max five); the output
    aspect follows the first image unless ``aspect_ratio`` overrides it.
    NOT yet verified against the live API — a 4xx here names the field."""
    base.ensure_prompt_fits(prompt, PROVIDER_ID, what="image edit prompt")
    sources = [_data_uri(str(image)) for image in images[:MAX_EDIT_IMAGES] if image]
    if not sources:
        raise base.CloudAdapterError("an image edit needs at least one source image")
    payload: dict = {"model": model, "prompt": prompt, "response_format": "url"}
    if len(sources) == 1:
        payload["image"] = {"url": sources[0], "type": "image_url"}
    else:
        payload["image_urls"] = sources
    if aspect_ratio in {"16:9", "9:16", "1:1"}:
        payload["aspect_ratio"] = aspect_ratio
    if resolution in {"1k", "2k"}:
        payload["resolution"] = resolution
    # Edits default to medium under xAI's auto quality (read 2026-10-13).
    # Pin it so the medium rate-card unit is what the request is billed.
    if quality in {"low", "medium"}:
        payload["quality"] = quality
    response = base.post_with_retries(
        f"{api_base()}/v1/images/edits",
        what="xAI image edit submit",
        json=payload,
        headers=_headers(),
        timeout=CALL_TIMEOUT_S,
    )
    _guard(response)
    body = response.json()
    data = body.get("data") or []
    url = str((data[0] if data else {}).get("url") or body.get("url") or "")
    if not url:
        raise base.CloudAdapterError(f"Grok returned no image URL for the edit: {body}")
    return base.CloudJobHandle(
        provider_id=PROVIDER_ID,
        external_id=f"img-{abs(hash(url)) % 10**10}",
        model=model,
        mode="image",
        duration_s=0,
        metadata={"sync_result_url": url, "kind": "image_edit", "sources": len(sources)},
    )


EDIT_MODEL = "grok-imagine-video"  # per docs, editing runs on grok-imagine-video; output ≤720p


def submit_edit(video: str, *, prompt: str, model: str = EDIT_MODEL) -> base.CloudJobHandle:
    """Edit-in-place: keep the take, change the attribute.

    Output inherits duration/aspect from the input and is capped at 720p; the
    docs support editing on grok-imagine-video only, so the model is normalized.
    """

    return _submit_operation(
        "/v1/videos/edits",
        {"model": EDIT_MODEL, "prompt": prompt, "video": _video_input(video)},
        model=EDIT_MODEL,
        duration_s=0,
        kind="edit",
    )


def submit_extension(
    video: str, *, prompt: str, model: str, duration_s: int
) -> base.CloudJobHandle:
    """Continue motion and sound; duration is the extended portion only. Chain ≤ 2 deep.

    Extension is documented on grok-imagine-video only — the model is
    normalized so the request, the handle, and the pricing all agree."""

    return _submit_operation(
        "/v1/videos/extensions",
        {
            "model": EDIT_MODEL,
            "prompt": prompt,
            "video": _video_input(video),
            "duration": int(duration_s),
        },
        model=EDIT_MODEL,
        duration_s=int(duration_s),
        kind="extension",
    )


def poll(handle: base.CloudJobHandle) -> base.CloudJobStatus:
    sync_url = handle.metadata.get("sync_result_url")
    if sync_url:
        return base.CloudJobStatus(state="done", result_url=str(sync_url))
    response = base.get_with_retries(
        f"{api_base()}/v1/videos/{handle.external_id}",
        what="xAI poll",
        headers=_headers(),
        timeout=CALL_TIMEOUT_S,
    )
    _guard(response)
    body = response.json()
    raw_state = str(body.get("status", "")).lower()
    video = body.get("video") or {}
    url = str(video.get("url") or body.get("url") or "")
    error = body.get("error")
    if isinstance(error, dict):
        # Documented failure shape: {"error": {"code": ..., "message": ...}}
        detail = str(error.get("message") or "")
        error_code = str(error.get("code") or "")
    else:
        detail = str(error or "")
        error_code = ""
    metadata = {"duration_s": video.get("duration") or handle.duration_s}
    if raw_state == "done":
        if not url:
            return base.CloudJobStatus(state="failed", detail="done without a media URL")
        return base.CloudJobStatus(state="done", result_url=url, metadata=metadata)
    if raw_state == "expired":
        return base.CloudJobStatus(state="expired", detail="result URL expired before fetch")
    if raw_state == "failed":
        haystack = f"{detail} {error_code}".lower()
        state = "moderated" if "moderation" in haystack else "failed"
        message = detail or "generation failed"
        if error_code:
            message = f"{message} ({error_code})"
        return base.CloudJobStatus(state=state, detail=message)
    if raw_state in {"queued", "pending"}:
        return base.CloudJobStatus(state="queued")
    return base.CloudJobStatus(state="running", detail=raw_state)


def fetch(handle: base.CloudJobHandle, status: base.CloudJobStatus, dest: Path) -> base.FetchedClip:
    if status.state != "done":
        raise base.CloudAdapterError(f"cannot fetch a {status.state} job")
    clip = base.download(status.result_url, dest)
    clip.metadata["duration_s"] = status.metadata.get("duration_s", handle.duration_s)
    return clip
