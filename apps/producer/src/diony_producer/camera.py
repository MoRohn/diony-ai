"""Wan2.2 camera from Producer. Live after Studio pack on the signed site."""

from __future__ import annotations

import hashlib
import json
import re
import os
import subprocess
import time
from pathlib import Path

import httpx

from diony_contracts.enums import ArtifactKind, AssetKind, ContinuityMode
from diony_contracts.live import generate_py, inference_live, wan_base, wan_frame_num
from diony_contracts.models import Project, Shot, Take
from diony_contracts.wan_prompt import validate_wan_prompt_dispatch
from diony_producer.vault import record_activity, vault_root
from diony_producer.gallery_join import resolve_media_tool

try:
    from diony_wan.runner import WanError, run_generate
except ImportError:  # producer installed without wan package
    WanError = RuntimeError  # type: ignore[misc, assignment]

    def run_generate(**kwargs):  # type: ignore[no-untyped-def]
        raise WanError("diony-wan is not installed")


STILLS = {".png", ".jpg", ".jpeg", ".webp"}


def qualified_quality_loras(shot: Shot, task: str) -> list[str]:
    """Select only adapters supported by the active official runtime contract."""
    available = {
        value.strip()
        for value in os.environ.get("DIONY_WAN_QUALIFIED_LORAS", "").split(",")
        if value.strip()
    }
    selected = (
        ["wan2.2-animate-relighting"]
        if task == "animate-14B" and "wan2.2-animate-relighting" in available
        else []
    )
    if shot.prompt_plan is not None:
        shot.prompt_plan.quality_lora_ids = selected
    return selected


def live_camera() -> bool:
    """True when this process farms Wan, or when desk Producer is proxied to the signed GPU."""
    if inference_live() and (generate_py().is_file() or _wan_http_up() or _comfy_http_up()):
        return True
    try:
        from diony_producer.gpu import gpu_producer_up, should_proxy
    except ImportError:
        return False
    return should_proxy() and gpu_producer_up()


def resolve_camera_task(
    engine: str,
    image: Path | None,
    *,
    audio: Path | None = None,
    animate_root: Path | None = None,
) -> str:
    """Resolve the requested engine without silently changing creative semantics."""
    if engine == "i2v-A14B" and image is not None and image.suffix.lower() in STILLS:
        return "i2v-A14B"
    if engine == "i2v-A14B":
        raise RuntimeError("I2V reference lock requires a readable still image")
    if engine == "t2v-A14B":
        return "t2v-A14B"
    if engine == "s2v-14B":
        if image is None or image.suffix.lower() not in STILLS:
            raise RuntimeError("S2V requires a readable reference still image")
        if audio is None or audio.suffix.lower() not in {".wav", ".mp3", ".flac"}:
            raise RuntimeError("S2V requires recorded audio in WAV, MP3, or FLAC format")
        return "s2v-14B"
    if engine == "animate-14B":
        if image is None or image.suffix.lower() not in STILLS:
            raise RuntimeError("Animate requires a readable character still image")
        if animate_root is None or not animate_root.is_dir():
            raise RuntimeError("Animate requires a preprocessed motion reference directory")
        return "animate-14B"
    raise RuntimeError(f"{engine} requires its purpose-built Wan runtime; Diony will not replace it with T2V")


def camera_task_for(project: Project, shot: Shot) -> str:
    """Task generate.py will actually run for this shot.

    Previous-frame I2V shots deliberately have no canonical keyframe on the
    Shot: their conditioning image is the prior selected Take's last frame.
    Preflight must resolve that same image instead of rejecting the shot before
    ``generate_clip`` can stage it.
    """
    canonical = _keyframe(project, shot)
    image = canonical
    if shot.continuity_mode == ContinuityMode.PREVIOUS_FRAME:
        image, _previous_take_id = continuity_frame(project, shot)
        if image is None and canonical is not None and boundary_fallback_reason(project, shot):
            # No prior boundary (rejected prints, or a voice hold before camera):
            # this Shot restarts from its reference still, as generate_clip does.
            image = canonical
        if image is None:
            raise RuntimeError(
                "Previous-frame continuity is required, but the prior selected print is unavailable"
            )
    return resolve_camera_task(
        shot.engine.value,
        image,
        audio=_audio_reference(project, shot),
        animate_root=_animate_reference_root(project, shot),
    )


def staged_camera_task(project: Project, shot: Shot) -> str:
    """Family to stage into the hot tier before this shot's voice is locked.

    ``camera_task_for`` answers for the shot as it is now. A spoken shot is
    conditioned to S2V by the voice pipeline moments before camera, so staging
    its current engine would copy the wrong 100+ GB family and then load S2V
    cold. Predict the conditioned family instead; preflight still resolves the
    real inputs when the take rolls.
    """
    if shot.audio_line or shot.dialogue_turns:
        return "s2v-14B"
    return camera_task_for(project, shot)


def _wan_http_up() -> bool:
    try:
        res = httpx.get(wan_base() + "/health", timeout=2.0)
        return res.status_code == 200
    except httpx.HTTPError:
        return False


def _comfy_http_up() -> bool:
    if os.environ.get("DIONY_WAN_BACKEND", "native").strip().lower() != "comfyui":
        return False
    base = os.environ.get("DIONY_COMFY_URL", "http://127.0.0.1:8188").rstrip("/")
    try:
        res = httpx.get(base + "/system_stats", timeout=2.0)
        return res.status_code == 200
    except httpx.HTTPError:
        return False


def _keyframe(project: Project, shot: Shot) -> Path | None:
    for aid in shot.keyframe_asset_ids:
        try:
            asset = project.asset(aid)
        except KeyError:
            continue
        path = vault_root() / asset.path
        if path.is_file() and path.suffix.lower() in STILLS:
            return path
    return None


def _audio_reference(project: Project, shot: Shot) -> Path | None:
    for asset_id in shot.audio_asset_ids:
        try:
            asset = project.asset(asset_id)
        except KeyError:
            continue
        path = vault_root() / asset.path
        if asset.kind == AssetKind.AUDIO and path.is_file() and path.suffix.lower() in {
            ".wav",
            ".mp3",
            ".flac",
        }:
            return path
    return None


def _animate_reference_root(project: Project, shot: Shot) -> Path | None:
    """Resolve operator-preprocessed official Wan-Animate material only."""
    for asset_id in shot.motion_reference_asset_ids:
        try:
            asset = project.asset(asset_id)
        except KeyError:
            continue
        configured = str(asset.metadata.get("wan_animate_src_root") or "").strip()
        if not configured:
            continue
        path = Path(configured)
        if not path.is_absolute():
            path = vault_root() / configured
        if path.is_dir():
            return path
    return None


def reference_frame(project: Project, shot: Shot) -> Path | None:
    """Stage a subject-defining still on the native 1280×720 Wan canvas."""
    source = _keyframe(project, shot)
    if source is None:
        return None
    asset = None
    for aid in shot.keyframe_asset_ids:
        try:
            candidate = project.asset(aid)
        except KeyError:
            continue
        if vault_root() / candidate.path == source:
            asset = candidate
            break
    fingerprint = (asset.sha256 if asset is not None else "") or str(source.stat().st_mtime_ns)
    asset_id = asset.id if asset is not None else source.stem
    destination = (
        vault_root()
        / "projects"
        / project.id
        / "references"
        / f"{asset_id}-{fingerprint[:12]}-1280x720.png"
    )
    if destination.is_file() and destination.stat().st_size > 32:
        return destination
    ffmpeg = resolve_media_tool("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("Image-conditioned generation requires the bundled FFmpeg tool")
    destination.parent.mkdir(parents=True, exist_ok=True)
    pending = destination.with_name(f".{destination.stem}.part.png")
    pending.unlink(missing_ok=True)
    command = [
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-vf",
        "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=0xF4F0E6",
        "-frames:v",
        "1",
        str(pending),
    ]
    try:
        subprocess.run(command, capture_output=True, check=True, timeout=90)
        if not pending.is_file() or pending.stat().st_size <= 32:
            raise RuntimeError("FFmpeg produced no staged reference")
        pending.replace(destination)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        pending.unlink(missing_ok=True)
        raise RuntimeError("The subject image could not be staged at 1280×720") from exc
    return destination


def _negative_for_take(take: Take) -> tuple[Path, str] | None:
    for artifact in take.artifacts:
        if artifact.kind != ArtifactKind.NEGATIVE_720:
            continue
        path = vault_root() / artifact.path
        if path.is_file() and path.stat().st_size > 32:
            return path, artifact.sha256
    return None


def _previous_take(project: Project, shot: Shot) -> Take | None:
    try:
        index = next(i for i, item in enumerate(project.shots) if item.id == shot.id)
    except StopIteration:
        return None
    if index < 1:
        return None
    previous_shot = project.shots[index - 1]
    if not set(previous_shot.character_ref_ids) & set(shot.character_ref_ids):
        return None
    if _speaker_changes(project, previous_shot, shot):
        _announce_boundary_fallback(shot, boundary_fallback_reason(project, shot))
        return None
    printable = _previous_prints(project, previous_shot)
    for take in printable:
        # Only a print that passed AutoQC is a boundary worth continuing from.
        # A "selected" take that is merely the best of four rejections would
        # seed this Shot with the drift and the mid-word mouth that failed it.
        if _take_passed(take):
            return take
    _announce_boundary_fallback(shot, boundary_fallback_reason(project, shot))
    return None


_announced_fallbacks: set[tuple[str, str]] = set()


def boundary_fallback_reason(project: Project, shot: Shot) -> str | None:
    """Why this previous-frame Shot has no boundary to continue from, if so.

    Returns None when a passed (or policy-settled) prior print exists. A prior
    Shot that printed but never passed, or that never printed at all (a voice
    hold before camera), is a reason to restart from the reference still —
    never a reason to stop the Production.
    """
    try:
        index = next(i for i, item in enumerate(project.shots) if item.id == shot.id)
    except StopIteration:
        return None
    if index < 1:
        return None
    previous_shot = project.shots[index - 1]
    if _speaker_changes(project, previous_shot, shot):
        return (
            f"speaker changes from {_speaker_character_id(project, previous_shot)} to "
            f"{_speaker_character_id(project, shot)}"
        )
    printable = _previous_prints(project, previous_shot)
    if any(_take_passed(take) for take in printable):
        return None
    if printable:
        return f"previous print {printable[0].id} did not pass AutoQC"
    return f"previous Shot {previous_shot.id} has no print"


def _speaker_changes(project: Project, previous_shot: Shot, shot: Shot) -> bool:
    """A spoken Shot whose speaker differs from the prior Shot's is a cut, not a continuation.

    The prior print frames the prior speaker (a close-up, or a wide frame the
    model animated for them); continuing from it would put this line on that
    face. The Shot restarts from the reference still and crops its own speaker.
    """
    if not shot_speaks_here(shot):
        return False
    return _speaker_character_id(project, previous_shot) != _speaker_character_id(project, shot)


def _announce_boundary_fallback(shot: Shot, reason: str | None) -> None:
    if not reason or (shot.id, reason) in _announced_fallbacks:
        return
    _announced_fallbacks.add((shot.id, reason))
    record_activity(
        "wan",
        f"{shot.id} · {reason}; starting from the reference still instead of its last frame",
        level="info",
        shot_id=shot.id,
    )


def _previous_prints(project: Project, previous_shot: Shot) -> list[Take]:
    """Every take of the prior Shot with a readable negative, selected print first."""
    candidates: list[Take] = []
    if previous_shot.selected_take_id:
        try:
            candidates.append(project.take(previous_shot.selected_take_id))
        except KeyError:
            pass
    candidates.extend(reversed(project.takes_for(previous_shot.id)))
    seen: set[str] = set()
    printable: list[Take] = []
    for take in candidates:
        if take.id in seen or not _negative_for_take(take):
            continue
        seen.add(take.id)
        printable.append(take)
    return printable


def previous_prints_all_rejected(project: Project, shot: Shot) -> bool:
    """Is the prior boundary unavailable (rejected prints, or no print yet)?"""
    return boundary_fallback_reason(project, shot) is not None


def _take_passed(take: Take) -> bool:
    qc = take.qc
    if qc is None:
        return False
    if getattr(qc, "settled_by_policy", False):
        return True
    return str(getattr(qc.verdict, "value", qc.verdict) or "") == "pass"


def continuity_frame(project: Project, shot: Shot) -> tuple[Path | None, str | None]:
    """Extract the selected prior print's last usable frame for frame-to-clip generation."""
    if shot.continuity_mode != ContinuityMode.PREVIOUS_FRAME:
        return None, None
    previous = _previous_take(project, shot)
    if previous is None:
        return None, None
    source_row = _negative_for_take(previous)
    if source_row is None:
        return None, None
    source, source_sha256 = source_row
    fingerprint = source_sha256 or str(source.stat().st_mtime_ns)
    destination = (
        vault_root()
        / "projects"
        / project.id
        / "references"
        / f"{previous.id}-{fingerprint[:12]}-last-1280x720.png"
    )
    if destination.is_file() and destination.stat().st_size > 32:
        return destination, previous.id
    ffmpeg = resolve_media_tool("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("Previous-frame continuity requires the bundled FFmpeg tool")
    destination.parent.mkdir(parents=True, exist_ok=True)
    pending = destination.with_name(f".{destination.stem}.part.png")
    pending.unlink(missing_ok=True)
    command = [
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-sseof",
        "-0.1",
        "-i",
        str(source),
        "-vf",
        "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=0xF4F0E6",
        "-frames:v",
        "1",
        str(pending),
    ]
    try:
        subprocess.run(command, capture_output=True, check=True, timeout=90)
        if not pending.is_file() or pending.stat().st_size <= 32:
            raise RuntimeError("FFmpeg produced no continuity frame")
        pending.replace(destination)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        pending.unlink(missing_ok=True)
        raise RuntimeError(f"Could not stage the boundary frame from {previous.id}") from exc
    return destination, previous.id


def _response_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text
    if isinstance(payload, dict):
        detail = payload.get("detail") or payload.get("error")
        if detail:
            return str(detail)
    return response.text


def _generate_http_job(payload: dict, destination: Path, *, timeout: float) -> Path:
    """Submit to Diony's durable Wan queue and preserve synchronous FARM hand-off."""
    frame_num = wan_frame_num(
        int(payload.get("duration_frames") or 81), float(payload.get("fps") or 23.976)
    )
    job_payload = {
        key: value
        for key, value in payload.items()
        if key not in {"duration_frames", "fps"}
    }
    job_payload.update(
        {
            "quality_tier": os.environ.get("DIONY_WAN_QUALITY_TIER", "production"),
            "frames": frame_num,
        }
    )
    response = httpx.post(
        wan_base() + "/v1/jobs", json=job_payload, timeout=min(timeout, 60.0)
    )
    if response.status_code >= 400:
        raise RuntimeError(_response_detail(response) or "diony-wan rejected the job")
    try:
        job_id = str(response.json()["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("diony-wan job submission returned no durable job id") from exc
    deadline = time.monotonic() + timeout
    poll_interval = max(0.0, float(os.environ.get("DIONY_WAN_JOB_POLL", "1.0")))
    while time.monotonic() < deadline:
        response = httpx.get(
            wan_base() + f"/v1/jobs/{job_id}",
            timeout=min(30.0, max(1.0, deadline - time.monotonic())),
        )
        if response.status_code >= 400:
            raise RuntimeError(_response_detail(response) or f"Wan job {job_id} status failed")
        try:
            job = response.json()
        except ValueError as exc:
            raise RuntimeError(f"Wan job {job_id} returned unreadable status") from exc
        state = str(job.get("state") or "")
        if state == "completed":
            if destination.is_file() and destination.stat().st_size > 32:
                return destination
            output = job.get("output") if isinstance(job.get("output"), dict) else {}
            raise RuntimeError(
                f"Wan job {job_id} completed but wrote no clip at {destination}; output={output}"
            )
        if state in {"failed", "cancelled"}:
            progress = job.get("progress") if isinstance(job.get("progress"), dict) else {}
            error = job.get("error") or progress.get("error") or "no backend detail"
            raise RuntimeError(f"Wan job {job_id} {state}: {error}")
        time.sleep(poll_interval)
    raise RuntimeError(f"Wan job {job_id} timed out after {int(timeout)} seconds")


SPEAKER_FACES_FILE = "speaker-faces.json"
CLOSEUP_WIDTH_FACTOR = 3.2  # crop width as a multiple of the face width: a medium close-up
CLOSEUP_MIN_WIDTH = 480.0
CLOSEUP_FACE_HEADROOM = 0.42  # the face centre sits at 42 % of the crop height


def speaker_faces_path(project: Project) -> Path:
    return vault_root() / "projects" / project.id / "references" / SPEAKER_FACES_FILE


def learn_speaker_face(project: Project, character_id: str, center: tuple[float, float], *, width: int = 1280, height: int = 720) -> None:
    """Remember where a character's face sits in the wide frame after a qualified S2V pass."""
    path = speaker_faces_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        known = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, json.JSONDecodeError):
        known = {}
    if not isinstance(known, dict):
        known = {}
    point = (center[0] / width, center[1] / height)
    for other, row in known.items():
        if other == character_id or not (isinstance(row, list) and len(row) == 2):
            continue
        if abs(float(row[0]) - point[0]) + abs(float(row[1]) - point[1]) <= KNOWN_FACE_TOLERANCE:
            # S2V animated a face that already belongs to another character:
            # that is the model's choice, not this character's identity.
            return
    known[character_id] = [round(point[0], 4), round(point[1], 4)]
    path.write_text(json.dumps(known, indent=2, sort_keys=True) + "\n", encoding="utf-8")


KNOWN_FACE_TOLERANCE = 0.12  # normalised L1 distance between two face centres


def _known_faces(project: Project) -> dict[str, tuple[float, float]]:
    path = speaker_faces_path(project)
    try:
        known = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(known, dict):
        return {}
    faces: dict[str, tuple[float, float]] = {}
    for character_id, row in known.items():
        if isinstance(row, list) and len(row) == 2:
            faces[str(character_id)] = (float(row[0]), float(row[1]))
    return faces


_STAGING_CUES = (
    ("left", re.compile(r"\b(left|stage left|screen left)\b", re.IGNORECASE)),
    ("right", re.compile(r"\b(right|stage right|screen right)\b", re.IGNORECASE)),
    ("centre", re.compile(r"\b(cent(?:re|er)|middle|between)\b", re.IGNORECASE)),
)


def staging_cue(idea: str, name: str) -> str | None:
    """The brief's left / centre / right placement of a named character, if stated.

    Only the sentence that names the character counts, and only its first
    placement word: "Lila sits on a stool to his right" → "right". The cue is
    weak evidence — authors and stills disagree — so callers apply it only
    among faces not already known to be someone else.
    """
    clean_name = (name or "").strip()
    if not clean_name or not idea:
        return None
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", idea):
        if not re.search(rf"\b{re.escape(clean_name)}\b", sentence, re.IGNORECASE):
            continue
        hits = [
            (match.start(), cue)
            for cue, pattern in _STAGING_CUES
            if (match := pattern.search(sentence))
        ]
        if hits:
            return min(hits)[1]
    return None


def _known_speaker_face(project: Project, character_id: str) -> tuple[float, float] | None:
    path = speaker_faces_path(project)
    try:
        known = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, json.JSONDecodeError):
        return None
    row = known.get(character_id) if isinstance(known, dict) else None
    if isinstance(row, list) and len(row) == 2:
        return float(row[0]), float(row[1])
    return None


def _speaker_character_id(project: Project, shot: Shot) -> str:
    for turn in shot.dialogue_turns:
        if turn.character_id:
            return turn.character_id
    return _primary_character_id(project, shot)


def choose_speaker_face(
    faces: list[tuple[float, float, float, float, float]],
    *,
    known: tuple[float, float] | None,
    width: int,
    height: int,
    exclude: tuple[tuple[float, float], ...] = (),
    cue: str | None = None,
) -> tuple[tuple[float, float, float, float, float], str] | None:
    """Pick the speaker's face from evidence, never by guessing among equals.

    Evidence, strongest first: a face learned from a qualified take of this
    character; the only face left once other characters' known faces are
    excluded; the brief's left / centre / right cue applied among the
    remaining faces. With several unassigned faces and no cue, the wide
    framing stays.
    """
    if not faces:
        return None

    def distance(face, point) -> float:
        return abs(face[0] + face[2] / 2 - point[0] * width) + abs(face[1] + face[3] / 2 - point[1] * height)

    if known is not None:
        nearest = min(faces, key=lambda f: distance(f, known))
        if distance(nearest, known) <= KNOWN_FACE_TOLERANCE * (width + height):
            return nearest, "learned from a qualified take"
    if len(faces) == 1:
        return faces[0], "only face in the still"
    candidates = [
        face
        for face in faces
        if not any(distance(face, point) <= KNOWN_FACE_TOLERANCE * (width + height) for point in exclude)
    ]
    if len(candidates) == 1:
        return candidates[0], "only face not already identified as another character"
    if cue and len(candidates) >= 2:
        ordered = sorted(candidates, key=lambda f: f[0] + f[2] / 2)
        if cue == "left":
            return ordered[0], "staged left in the brief"
        if cue == "right":
            return ordered[-1], "staged right in the brief"
        if cue == "centre":
            return ordered[len(ordered) // 2], "staged centre in the brief"
    return None


def closeup_box(face: tuple[float, float, float, float, float], *, width: int, height: int) -> tuple[int, int, int, int]:
    """A 16:9 medium close-up around a face box, clamped to the still."""
    x, y, w, h, _score = face
    crop_w = max(CLOSEUP_MIN_WIDTH, CLOSEUP_WIDTH_FACTOR * w)
    crop_w = min(crop_w, float(width))
    crop_h = min(float(height), crop_w * 9.0 / 16.0)
    crop_w = crop_h * 16.0 / 9.0
    cx = x + w / 2.0
    cy = y + h / 2.0
    left = min(max(0.0, cx - crop_w / 2.0), width - crop_w)
    top = min(max(0.0, cy - crop_h * CLOSEUP_FACE_HEADROOM), height - crop_h)
    return int(round(left)), int(round(top)), int(round(crop_w)), int(round(crop_h))


def speaker_keyframe(project: Project, shot: Shot, still: Path | None) -> Path | None:
    """Crop the speaking character from the wide still for S2V, when that face is known."""
    if still is None or not shot_speaks_here(shot):
        return still
    detector_model = os.environ.get("DIONY_FACE_DETECTOR_MODEL", "").strip()
    if not detector_model or not Path(detector_model).is_file():
        return still
    character_id = _speaker_character_id(project, shot)
    try:
        from diony_producer.av_sync import detect_faces

        faces = detect_faces(still, detector_model=Path(detector_model))
    except Exception as exc:  # noqa: BLE001 — a framing aid never blocks camera
        record_activity("wan", f"{shot.id} · wide framing kept · face detection failed: {str(exc)[:120]}", level="warn", shot_id=shot.id)
        return still
    width, height = 1280, 720
    known_faces = _known_faces(project)
    names = {character.id: character.name for character in project.bible.characters}
    cue = staging_cue(project.brief.idea if project.brief is not None else "", names.get(character_id, ""))
    choice = choose_speaker_face(
        faces,
        known=known_faces.get(character_id),
        width=width,
        height=height,
        exclude=tuple(point for other, point in known_faces.items() if other != character_id),
        cue=cue,
    )
    if choice is None:
        record_activity(
            "wan",
            f"{shot.id} · wide framing kept · {len(faces)} faces in the still and {character_id} is not yet identified",
            level="info",
            shot_id=shot.id,
        )
        return still
    face, reason = choice
    left, top, crop_w, crop_h = closeup_box(face, width=width, height=height)
    fingerprint = f"{still.stat().st_size}-{left}-{top}-{crop_w}-{crop_h}"
    destination = still.with_name(f"{shot.id}-{character_id}-closeup-{fingerprint}.png")
    if destination.is_file() and destination.stat().st_size > 32:
        return destination
    ffmpeg = resolve_media_tool("ffmpeg")
    if not ffmpeg:
        return still
    pending = destination.with_name(f".{destination.stem}.part.png")
    pending.unlink(missing_ok=True)
    command = [
        ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(still),
        "-vf", f"crop={crop_w}:{crop_h}:{left}:{top},scale=1280:720:flags=lanczos",
        "-frames:v", "1", str(pending),
    ]
    try:
        subprocess.run(command, capture_output=True, check=True, timeout=90)
        if not pending.is_file() or pending.stat().st_size <= 32:
            raise RuntimeError("FFmpeg produced no close-up keyframe")
        pending.replace(destination)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        pending.unlink(missing_ok=True)
        record_activity("wan", f"{shot.id} · wide framing kept · close-up crop failed: {str(exc)[:120]}", level="warn", shot_id=shot.id)
        return still
    record_activity(
        "wan",
        f"{shot.id} · medium close-up keyframe on {character_id} ({reason}; crop {crop_w}×{crop_h} at {left},{top})",
        level="live",
        shot_id=shot.id,
    )
    return destination


def conditioning_sidecar(negative: Path) -> Path:
    return negative.with_name(f"{negative.stem}.conditioning.json")


def record_conditioning_image(negative: Path, image: Path | None, *, source: str) -> None:
    """Write which still actually conditioned this negative, next to it.

    AutoQC's reference lock must compare the print with the image the camera
    started from — the speaker close-up crop or the previous print's last
    frame — not with the wide subject still those were derived from.
    """
    sidecar = conditioning_sidecar(negative)
    if image is None or not image.is_file():
        sidecar.unlink(missing_ok=True)
        return
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(
        json.dumps({"image": str(image), "sha256": digest, "source": source}, indent=2) + "\n",
        encoding="utf-8",
    )


def conditioning_image(negative: Path) -> Path | None:
    """The still this negative was conditioned on, when the camera recorded it."""
    sidecar = conditioning_sidecar(negative)
    if not sidecar.is_file():
        return None
    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
        image = Path(str(payload.get("image") or ""))
        expected = str(payload.get("sha256") or "")
    except (OSError, ValueError):
        return None
    if not image.is_file():
        return None
    if expected and hashlib.sha256(image.read_bytes()).hexdigest() != expected:
        return None
    return image


def shot_speaks_here(shot: Shot) -> bool:
    return bool(shot.dialogue_turns) or bool(" ".join(str(shot.audio_line or "").split()))


def generate_clip(
    project: Project,
    shot: Shot,
    dest: Path,
    seed: int,
    *,
    attention_backend: str | None = None,
) -> Path:
    """Write a 720p Wan negative to dest. Raises on failure."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    canonical = reference_frame(project, shot)
    boundary, _previous_take_id = continuity_frame(project, shot)
    if shot.continuity_mode == ContinuityMode.PREVIOUS_FRAME and boundary is None:
        # No prior boundary — rejected prints, or a prior Shot that never
        # printed (voice hold before camera). This Shot restarts from its
        # reference still (announced on the activity feed); only a Shot with
        # no reference still at all stops here.
        reason = boundary_fallback_reason(project, shot)
        if reason is None or canonical is None:
            raise RuntimeError("Previous-frame continuity is required, but the prior selected print is unavailable")
        _announce_boundary_fallback(shot, reason)
    image = boundary or canonical
    source = "boundary" if boundary is not None else "reference"
    if str(getattr(shot.engine, "value", shot.engine) or "") == "s2v-14B":
        framed = speaker_keyframe(project, shot, image)
        if framed is not None and framed != image:
            source = "closeup"
        image = framed
    record_conditioning_image(dest, image, source=source)
    audio = _audio_reference(project, shot)
    animate_root = _animate_reference_root(project, shot)
    task = resolve_camera_task(
        shot.engine.value,
        image,
        audio=audio,
        animate_root=animate_root,
    )
    quality_lora_ids = qualified_quality_loras(shot, task)
    prompt_diagnostics = validate_wan_prompt_dispatch(shot)
    if prompt_diagnostics:
        raise RuntimeError("Wan prompt contract invalid: " + "; ".join(prompt_diagnostics))
    prompt = shot.prompt
    timeout = float(os.environ.get("DIONY_WAN_TIMEOUT", "7200"))
    want_http = bool(os.environ.get("DIONY_WAN_URL", "").strip())
    if (want_http or not generate_py().is_file()) and _wan_http_up():
        payload = {
            "task": task,
            "prompt": prompt,
            "negative_prompt": shot.negative_prompt,
            "seed": seed,
            "save_file": str(dest),
            "duration_frames": shot.duration_frames,
            "fps": shot.fps,
            "image": str(image) if image else None,
            "audio": str(audio) if audio else None,
            "animate_root": str(animate_root) if animate_root else None,
            "quality_lora_ids": quality_lora_ids,
            "guide_scale": shot.s2v_guide_scale,
        }
        return _generate_http_job(payload, dest, timeout=timeout)
    run_generate(
        task=task,
        prompt=prompt,
        negative_prompt=shot.negative_prompt,
        seed=seed,
        save_file=dest,
        duration_frames=shot.duration_frames,
        fps=shot.fps,
        image=image,
        audio=audio,
        animate_root=animate_root,
        quality_lora_ids=quality_lora_ids,
        attention_backend=attention_backend,
        guide_scale=shot.s2v_guide_scale,
    )
    return dest
