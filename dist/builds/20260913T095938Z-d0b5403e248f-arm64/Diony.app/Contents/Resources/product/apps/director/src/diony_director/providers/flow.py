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
import threading
import time
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

import httpx

from diony_contracts.director import DirectorOp
from diony_director import ratecards
from diony_director.estimate import OMNI_MODELS
from diony_director.providers import base

PROVIDER_ID = "google-flow"
CALL_TIMEOUT_S = 120.0
UPLOAD_TIMEOUT_S = 300.0
CAPTCHA_PROVIDER_DOCS = (
    "https://useapi.net/docs/api-google-flow-v1/post-google-flow-accounts-captcha-providers"
)
CAPTCHA_PROVIDER_NAMES = (
    "CapSolver",
    "AntiCaptcha",
    "YesCaptcha",
    "CapMonster",
    "SolveCaptcha",
    "2Captcha",
    "EzCaptcha",
)
_MISSING_CAPTCHA_PROVIDER = "no captcha provider api keys configured"

_ASSET_CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".mp4": "video/mp4",
}


def _response_error_text(response: httpx.Response) -> str:
    """Text used only to classify a provider failure.

    Some test doubles and a few useapi responses expose JSON without a useful
    ``text`` property, so inspect the small public error fields as well.
    """
    parts = [str(getattr(response, "text", "") or "")]
    try:
        body = response.json()
    except (ValueError, TypeError):
        body = None
    if isinstance(body, dict):
        for key in ("error", "message", "detail"):
            value = body.get(key)
            if isinstance(value, str):
                parts.append(value)
            elif isinstance(value, dict):
                parts.append(str(value.get("message") or value.get("error") or ""))
    return " ".join(part for part in parts if part)


def captcha_configuration_message() -> str:
    return (
        "Google Flow needs a captcha solver: useapi.net has no captcha provider API key "
        "and its included solves are exhausted. Configure at least one solver at "
        f"{CAPTCHA_PROVIDER_DOCS}, then press Run again. Nothing was spent."
    )


def _guard(response: httpx.Response, what: str = "request") -> None:
    """Convert HTTP failures into CloudAdapterError carrying the provider's own words."""
    if response.status_code == 429:
        # quota, not failure: nothing was spent and the op is fine — raise the
        # transient flavor so the wave holds and resumes instead of failing
        # (a Flow style-frame 429 once killed a whole chained Grok run)
        code, why = base.quota_reason(response)
        raise base.CloudAdapterQuotaError(
            f"Flow {what} held by quota (429): {why}" + (f" [{code}]" if code else ""),
            retry_at=base.quota_retry_at(response),
            reason_code=code,
        )
    if response.status_code >= 400:
        if (
            response.status_code == 400
            and _MISSING_CAPTCHA_PROVIDER in _response_error_text(response).casefold()
        ):
            # This is an account-wide prerequisite, not a malformed clip. A
            # distinct error lets the Director stop the wave after one call
            # and leave every unsent op runnable for the post-setup retry.
            raise base.CloudAdapterConfigurationError(captcha_configuration_message())
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


# --- Request pacing ----------------------------------------------------------
#
# useapi.net throttles a Flow account that is hit in a burst
# (PUBLIC_ERROR_USER_REQUESTS_THROTTLED: about a 30-minute hold, and the whole
# run pauses on it). Several threads talk to Flow at once — the submitter,
# the chain and master workers, every advance tick, a reconnect push — and
# none of them can see the others, so the gate is process-wide: Flow requests
# never overlap, and each one starts only after a quiet gap of
# DIONY_FLOW_REQUEST_GAP_S has passed since the previous one FINISHED
# (a failed request counts — it still touched the API). Media downloads are
# not Flow API calls (they go to Google's storage) and are not paced.
DEFAULT_REQUEST_GAP_S = 5.0
_PACE_LOCK = threading.Lock()
_last_request_done_at: float = 0.0  # monotonic clock; 0.0 = no request yet
# injectable for tests: the suite must never really sleep between fakes
_sleep = time.sleep
_clock = time.monotonic


def request_gap_s() -> float:
    """The quiet gap between consecutive Flow API requests, in seconds.
    DIONY_FLOW_REQUEST_GAP_S overrides the default; 0 disables the gap
    (requests still never overlap); junk falls back to the default."""
    raw = os.environ.get("DIONY_FLOW_REQUEST_GAP_S", "").strip()
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
    return DEFAULT_REQUEST_GAP_S


def paced(send, *, what: str = "request"):
    """Run one Flow API call through the pacing gate."""
    global _last_request_done_at
    gap = request_gap_s()
    with _PACE_LOCK:
        if _last_request_done_at:
            wait = _last_request_done_at + gap - _clock()
            if wait > 0:
                _sleep(wait)
        try:
            return send()
        finally:
            _last_request_done_at = _clock()


def _post(url: str, *, what: str = "request", **kwargs) -> httpx.Response:
    return paced(lambda: base.post_with_retries(url, what=f"Flow {what}", **kwargs), what=what)


def _get(url: str, *, what: str = "request", **kwargs) -> httpx.Response:
    return paced(lambda: base.get_with_retries(url, what=f"Flow {what}", **kwargs), what=what)


def captcha_status() -> dict:
    """Return masked, account-level captcha capacity without spending.

    useapi supplies 300 included solves to a newly linked Flow account. Once
    those are gone, at least one third-party provider key must be present.
    The GET response contains masked keys only; Diony never reads or stores a
    raw solver secret.
    """
    response = _get(
        f"{api_base()}/accounts/captcha-providers",
        what="captcha provider probe",
        headers=_headers(),
        timeout=CALL_TIMEOUT_S,
    )
    _guard(response, "captcha provider probe")
    body = response.json()
    if not isinstance(body, dict):
        raise base.CloudAdapterError(
            f"Flow captcha provider probe returned an invalid response: {body}"
        )
    providers = [name for name in CAPTCHA_PROVIDER_NAMES if body.get(name)]
    raw_free = body.get("freeCaptchaCredits")
    free_credits = (
        max(0, int(raw_free))
        if isinstance(raw_free, (int, float)) and not isinstance(raw_free, bool)
        else 0
    )
    return {
        "ready": bool(providers or free_credits),
        "providers": providers,
        "free_credits": free_credits,
    }


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
    response = _post(
        url, what="asset upload", content=payload, headers=headers, timeout=UPLOAD_TIMEOUT_S
    )
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
    payload["prompt"] = _with_markers(op)

    response = _post(
        f"{api_base()}/videos",
        what="video submit",
        json=payload,
        headers=_headers(),
        timeout=CALL_TIMEOUT_S,
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
    response = _post(
        f"{api_base()}/images",
        what="image submit",
        json=payload,
        headers=_headers(),
        timeout=CALL_TIMEOUT_S,
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
        generated = (media[0].get("image") or {}).get("generatedImage") or {}
        sync_url = str(generated.get("fifeUrl") or "")
        media_generation_id = str(generated.get("mediaGenerationId") or "")
        if sync_url:
            handle.metadata["sync_result_url"] = sync_url
        if media_generation_id:
            handle.metadata["media_generation_id"] = media_generation_id
    return handle


def _with_markers(op: DirectorOp) -> str:
    """Blueprint 32: useapi's inline markers — `@character_n` / `@referenceImage_n`
    — name what each supplied slot IS. Every marker must match a body slot
    (a dangling one 400s), so only the slots this payload carries are named;
    dropped whole when it would not fit."""
    roles = list(op.artifact_refs.get("reference_roles") or [])
    names = list(op.artifact_refs.get("character_names") or [])
    references = list(op.artifact_refs.get("references") or [])
    characters = list(op.artifact_refs.get("characters") or [])
    if not references and not characters:
        return op.prompt
    roles = roles[: len(references)]
    names = names[: len(characters)]
    if not roles and not names:
        return op.prompt
    from diony_director import cast as cast_mod

    legend = cast_mod.legend(roles, "google-flow", characters=names)
    if not legend:
        return op.prompt
    candidate = f"{op.prompt} {legend}"
    return candidate if len(candidate) <= base.prompt_limit(PROVIDER_ID) else op.prompt


MAX_CHARACTER_IMAGES = 2  # POST /characters: imageReference_1..2 ("max two per character")


def list_characters() -> list[dict]:
    """Return the reusable characters owned by the configured Flow account.

    The video endpoint needs the provider's full ``character`` reference;
    Google Flow's browser URL exposes only ``entityId``.  Listing is therefore
    the safe bridge between a character the user already made in Flow and the
    opaque reference the generation API accepts.
    """

    email = account_email()
    if not email:
        raise base.CloudAdapterError(
            "DIONY_FLOW_EMAIL is not set — Diony cannot look up an existing Flow character"
        )
    response = _get(
        f"{api_base()}/characters",
        what="character list",
        params={"email": email},
        headers=_headers(),
        timeout=CALL_TIMEOUT_S,
    )
    _guard(response, "character list")
    body = response.json()
    characters = body.get("characters") or []
    if not isinstance(characters, list):
        raise base.CloudAdapterError(f"Flow returned an invalid character list: {body}")
    return [entry for entry in characters if isinstance(entry, dict)]


def _character_lookup_key(value: str) -> str:
    """Accept a Flow character URL, entity id, or full API character ref."""

    raw = str(value or "").strip()
    if not raw:
        raise base.CloudAdapterError("paste a Google Flow character URL or character reference")
    parsed = urlparse(raw)
    if parsed.scheme or parsed.netloc:
        if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() != "flow.google.com":
            raise base.CloudAdapterError("that is not a Google Flow character URL")
        parts = [unquote(part) for part in parsed.path.split("/") if part]
        try:
            index = parts.index("character")
            raw = parts[index + 1]
        except (ValueError, IndexError) as err:
            raise base.CloudAdapterError(
                "that Google Flow URL does not identify a character"
            ) from err
    return raw.strip()


def resolve_character(value: str) -> dict:
    """Resolve a browser URL/entity id to the full ref required by videos.

    The lookup also proves the character belongs to ``DIONY_FLOW_EMAIL``;
    arbitrary opaque ids are never accepted on trust and allowed to fail only
    after a paid clip submission.
    """

    key = _character_lookup_key(value)
    for entry in list_characters():
        entity_id = str(entry.get("entityId") or "").strip()
        character_ref = str(entry.get("character") or "").strip()
        if key not in {entity_id, character_ref}:
            continue
        if not character_ref:
            raise base.CloudAdapterError(
                f"Flow found character {entity_id or key}, but returned no reusable character reference"
            )
        return entry
    raise base.CloudAdapterError(
        "that character was not found on the Google Flow account configured in Diony"
    )


def register_character(
    name: str, image_media_ids: list[str], *, voice: str = "", notes: str = ""
) -> str:
    # /characters dialect (docs, aligned with the /images correction):
    # displayName + imageReference_N (max two), optional personalityNotes and a
    # system voice preset; synchronous, returns the reusable character ref.
    # Unlike uploads and generations, this endpoint rejects an ``email`` body
    # field and infers the account from the scoped image references.
    payload: dict = {"displayName": name}
    if notes:
        payload["personalityNotes"] = notes[:2000]
    if voice:
        payload["voice"] = voice
    for index, ref in enumerate(image_media_ids[:MAX_CHARACTER_IMAGES], start=1):
        payload[f"imageReference_{index}"] = _media_ref(ref, what=f"imageReference_{index}")
    response = _post(
        f"{api_base()}/characters",
        what="character register",
        json=payload,
        headers=_headers(),
        timeout=CALL_TIMEOUT_S,
    )
    _guard(response)
    body = response.json()
    # ``entityId`` is the UUID shown in flow.google.com URLs.  POST /videos
    # does not accept it: the required value is the full, account-scoped
    # ``character`` reference returned beside it.
    ref = str(body.get("character") or body.get("ref") or "")
    if not ref:
        raise base.CloudAdapterError(f"Flow returned no reusable character reference: {body}")
    return ref


def register_voice(name: str, audio_media_id: str) -> str:
    payload: dict = {"name": name, "referenceAudio": audio_media_id}
    if account_email():
        payload["email"] = account_email()
    response = _post(
        f"{api_base()}/voices",
        what="voice register",
        json=payload,
        headers=_headers(),
        timeout=CALL_TIMEOUT_S,
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
    response = _post(
        f"{api_base()}/videos/extend",
        what="extend submit",
        json=payload,
        headers=_headers(),
        timeout=CALL_TIMEOUT_S,
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


# POST /videos/upscale (useapi.net docs, verified 2026-09-03): the source is a
# Flow-generated video's mediaGenerationId; resolution is 720p (promotes a 360p
# Omni clip), 1080p (default) or 4K (Google AI Ultra only); async=true answers
# 201 with a lowercase `jobid`; a paid Google AI plan is required (free
# accounts cannot upscale at all); 720p/1080p spend no credits; upscaling the
# same video twice returns the cached result.
UPSCALE_RESOLUTIONS = ("720p", "1080p", "4K")
UPSCALE_MODEL = "video-upscale"
# A job that reports completed but carries no videoUrl yet is "not yet", not
# "failed" (Google's link endpoint throttles by address): GET /jobs re-resolves
# the link at most once a minute, so the next poll waits at least that long.
LINK_PENDING_POLL_S = 60


def submit_upscale(media_generation_id: str, *, resolution: str = "1080p") -> base.CloudJobHandle:
    if resolution not in UPSCALE_RESOLUTIONS:
        raise base.CloudAdapterError(
            f"Flow upscale resolution must be one of {', '.join(UPSCALE_RESOLUTIONS)} — got {resolution!r}"
        )
    source = str(media_generation_id or "")
    if not source:
        raise base.CloudAdapterError(
            "Flow upscale needs the mediaGenerationId of a Flow-generated video"
        )
    if Path(source).is_file() or source.startswith(("http://", "https://", "data:")):
        raise base.CloudAdapterError(
            "Flow upscale needs the mediaGenerationId of a Flow-generated video — a "
            "local file or URL cannot be upscaled server-side"
        )
    # No email parameter here — /videos/upscale 400s on it (verified live);
    # the mediaGenerationId already names the owning account.
    payload: dict = {
        "mediaGenerationId": source,
        "resolution": resolution,
        "async": True,
    }
    response = _post(
        f"{api_base()}/videos/upscale",
        what="upscale submit",
        json=payload,
        headers=_headers(),
        timeout=CALL_TIMEOUT_S,
    )
    _guard(response, "upscale")
    body = response.json()
    external_id = str(body.get("jobId") or body.get("jobid") or "")
    if not external_id:
        raise base.CloudAdapterError(f"Flow returned no jobId for upscale: {body}")
    credits_before = body.get("remainingCredits")
    handle = submit_handle(
        external_id,
        model=UPSCALE_MODEL,
        duration_s=0,
        credits_before=credits_before if isinstance(credits_before, int) else None,
        kind="upscale",
        resolution=resolution,
        source_media_generation_id=source,
    )
    # A 200 (the cached result of an earlier upscale of the same video, or a
    # synchronous answer) carries the media in the response itself: capture
    # it so poll never has to ask for a job that already finished.
    if response.status_code == 200:
        first = _first_media(body)
        url = str(first.get("videoUrl") or "")
        if url:
            handle.metadata["sync_result_url"] = url
            finished_id = str(first.get("mediaGenerationId") or "")
            if finished_id:
                handle.metadata["media_generation_id"] = finished_id
            remaining = body.get("remainingCredits")
            if isinstance(remaining, int):
                handle.metadata["credits_after"] = remaining
    return handle


def _first_media(body: dict) -> dict:
    """The first media item of a job/upscale body — live jobs nest media under
    `response`, docs show it at the top level; read both."""
    nested = body.get("response") if isinstance(body.get("response"), dict) else {}
    media = nested.get("media") or body.get("media") or []
    if media and isinstance(media, list) and isinstance(media[0], dict):
        return media[0]
    return {}


# Flow's top-level `error` on a failed job is the generic "All operations
# failed"; the reason that matters is nested: `response.failureReasons` and
# each media item's `mediaMetadata.mediaStatus` (verified on a live job —
# PUBLIC_ERROR_PROMINENT_PEOPLE_FILTER_FAILED / PROMINENT_PERSON when the
# prompt named a real public figure). Google's filters refuse before render,
# so those land as "moderated" (nothing burned) with words a director can act on.
_GENERIC_ERRORS = {"all operations failed", "operation failed", "generation failed", "failed"}

_FILTER_REASONS: dict[str, str] = {
    # Verified live 2026-09-07 (five omni-flash probes, s-9c2f04d993): the
    # filter fires when the PROSE names any person — real, or an invented
    # character — beside a reference picture of a face. The name alone
    # renders; the picture alone renders; a quoted spoken line saying the
    # name renders. The submit gate describes every named person in a
    # pictured prompt; a refusal after that means the picture itself.
    "PROMINENT_PERSON": (
        "Google Flow refused the clip: its prominent-people filter fires when the "
        "prompt names a person — a real one, or any named character — beside a "
        "reference picture of a face, or when the picture itself reads as a "
        "public figure. The Director describes every named person in a pictured "
        "prompt before submit; Retry sets the picture aside and rolls the clip "
        "from the prompt alone."
    ),
    "MINOR": "Google Flow refused the clip: its filter matched a depiction of a minor.",
    "VIOLENCE": "Google Flow refused the clip: its safety filter matched violent content.",
    "SEXUAL": "Google Flow refused the clip: its safety filter matched sexual content.",
    "HATE": "Google Flow refused the clip: its safety filter matched hateful content.",
    "DANGEROUS": "Google Flow refused the clip: its safety filter matched dangerous content.",
    "CELEBRITY": (
        "Google Flow refused the clip: its filter matched a celebrity likeness in the "
        "prompt or the reference image. Rewrite without naming or depicting the person."
    ),
}
_FILTER_MARKERS = ("FILTER", "SAFETY", "MODERAT", "POLICY", "BLOCKED", "HARM")


def _failure_reasons(body: dict) -> list[str]:
    """Every failure code Flow reports for a job, top level and per media item."""
    nested = body.get("response") if isinstance(body.get("response"), dict) else {}
    reasons: list[str] = []

    def _add(items) -> None:
        for item in items if isinstance(items, list) else []:
            code = str(item or "").strip()
            if code and code not in reasons:
                reasons.append(code)

    _add(nested.get("failureReasons") or body.get("failureReasons"))
    media = nested.get("media") or body.get("media") or []
    for item in media if isinstance(media, list) else []:
        if not isinstance(item, dict):
            continue
        meta = item.get("mediaMetadata") if isinstance(item.get("mediaMetadata"), dict) else {}
        status = meta.get("mediaStatus") if isinstance(meta.get("mediaStatus"), dict) else {}
        if not status and isinstance(item.get("mediaStatus"), dict):
            status = item["mediaStatus"]
        error = status.get("error") if isinstance(status.get("error"), dict) else {}
        _add([error.get("message")] if error.get("message") else [])
        _add(status.get("failureReasons"))
    return reasons


def _is_filter_reason(code: str) -> bool:
    upper = code.upper()
    return any(key in upper for key in _FILTER_REASONS) or any(m in upper for m in _FILTER_MARKERS)


def failure_detail(body: dict) -> tuple[str, bool]:
    """(what to tell the director, was it a content filter). Prefers Flow's
    nested reasons over the generic top-level `error`; falls back to it."""
    top = str(body.get("error") or "").strip()
    reasons = _failure_reasons(body)
    filtered = any(_is_filter_reason(r) for r in reasons)
    plain = ""
    for code in reasons:
        upper = code.upper()
        for key, words in _FILTER_REASONS.items():
            if key in upper:
                plain = words
                break
        if plain:
            break
    if not plain and filtered:
        plain = (
            "Google Flow refused the clip before rendering — its content filter "
            f"reported {', '.join(reasons)}. Retry fails the same way; rewrite the prompt."
        )
    if not plain and reasons:
        plain = "Google Flow reported " + ", ".join(reasons)
    if plain:
        codes = ", ".join(r for r in reasons if r.upper() not in plain.upper())
        return (f"{plain} [{codes}]" if codes else plain), filtered
    if top and top.lower() not in _GENERIC_ERRORS:
        return top, False
    return (top or ""), False


def poll(handle: base.CloudJobHandle) -> base.CloudJobStatus:
    sync_url = handle.metadata.get("sync_result_url")
    if sync_url:
        # images (and cached upscales) complete inside their own submit
        # response — no job to poll
        metadata: dict = {}
        if handle.metadata.get("media_generation_id"):
            metadata["media_generation_id"] = handle.metadata["media_generation_id"]
        if isinstance(handle.metadata.get("credits_after"), int):
            metadata["credits_after"] = handle.metadata["credits_after"]
        return base.CloudJobStatus(state="done", result_url=str(sync_url), metadata=metadata)
    response = _get(
        f"{api_base()}/jobs/{handle.external_id}",
        what="job poll",
        headers=_headers(),
        timeout=CALL_TIMEOUT_S,
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
        media_generation_id = str(first.get("mediaGenerationId") or first.get("name") or "")
        if media_generation_id:
            metadata["media_generation_id"] = media_generation_id
    remaining = nested.get("remainingCredits", body.get("remainingCredits"))
    if isinstance(remaining, int):
        metadata["credits_after"] = remaining
    if raw_state in {"completed", "done"} or (url and not raw_state):
        if not url:
            return _completed_without_url(media, detail, metadata)
        return base.CloudJobStatus(state="done", result_url=url, metadata=metadata)
    if raw_state in {"failed", "error"}:
        words, filtered = failure_detail(body)
        if filtered:
            return base.CloudJobStatus(state="moderated", detail=words, metadata=metadata)
        return base.CloudJobStatus(
            state="failed", detail=words or "generation failed", metadata=metadata
        )
    # Terminal states outside the happy vocabulary must land terminal — an
    # unmapped "moderated"/"cancelled" would poll as "running" forever and
    # wedge the whole batch behind a job that already died.
    if raw_state in {"moderated", "blocked", "rejected", "content_violation"}:
        return base.CloudJobStatus(state="moderated", detail=detail or raw_state, metadata=metadata)
    if raw_state in {"cancelled", "canceled", "aborted", "timeout", "timed_out"}:
        return base.CloudJobStatus(state="failed", detail=detail or raw_state, metadata=metadata)
    if raw_state == "expired":
        return base.CloudJobStatus(state="expired", detail=detail or raw_state, metadata=metadata)
    if raw_state in {"queued", "pending"}:
        return base.CloudJobStatus(state="queued", metadata=metadata)
    return base.CloudJobStatus(state="running", detail=raw_state, metadata=metadata)


def _completed_without_url(media: list, detail: str, metadata: dict) -> base.CloudJobStatus:
    """A completed job with no download link. Per the docs this is Google's
    link endpoint throttling, not a failed generation: the media exists and
    GET /jobs re-resolves the URL (at most once a minute). Only a media item
    that itself reports a failed generation is terminal here."""
    first = media[0] if media and isinstance(media[0], dict) else {}
    # live jobs nest the status under mediaMetadata; older docs put it at the top
    meta = first.get("mediaMetadata") if isinstance(first.get("mediaMetadata"), dict) else {}
    status_obj = meta.get("mediaStatus") if isinstance(meta.get("mediaStatus"), dict) else {}
    if not status_obj and isinstance(first.get("mediaStatus"), dict):
        status_obj = first["mediaStatus"]
    media_status = str(status_obj.get("mediaGenerationStatus") or "").upper()
    if media_status and "SUCCESS" not in media_status:
        words, filtered = failure_detail({"error": detail, "media": media})
        return base.CloudJobStatus(
            state="moderated" if filtered else "failed",
            detail=words or f"media generation reported {media_status}",
            metadata=metadata,
        )
    return base.CloudJobStatus(
        state="running",
        detail="completed — media link pending (Google is throttling link issue); re-resolving",
        metadata={**metadata, "link_pending": True, "poll_after_s": LINK_PENDING_POLL_S},
    )


def credits_used(handle: base.CloudJobHandle, status: base.CloudJobStatus) -> int:
    before = handle.metadata.get("credits_before")
    after = status.metadata.get("credits_after")
    if isinstance(before, int) and isinstance(after, int) and after <= before:
        return before - after
    files = ratecards.load_files()
    duration = handle.duration_s or 8
    resolution = handle.resolution or "720p"
    if handle.model == UPSCALE_MODEL:
        from diony_director.estimate import upscale_rate

        try:
            return upscale_rate(files, resolution).internal_credits_per_unit
        except ratecards.RateCardMissing:
            return 0
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
