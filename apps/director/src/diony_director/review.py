"""The Director's eyes: measured review of a fetched clip.

Families measure what this host can actually measure (delivery, duration and
audio via ffprobe) and mark the rest ``unmeasured`` — a missing measurement
drops out, it never scores zero, and it never lets a character clip call
itself a clean pass: identity unmeasured caps the verdict at ``warn``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from pathlib import Path

from diony_contracts.production import ClipReview, ClipUnit, ProductionPackage

MIN_DELIVERY_BYTES = 1024
DURATION_TOLERANCE_S = 1.6

AUDIO_REQUIRED_CLASSES = {"sync-dialogue", "vo-over", "ambience-led"}


def _probe(path: Path) -> dict | None:
    from diony_producer.gallery_join import resolve_media_tool

    ffprobe = resolve_media_tool("ffprobe") or shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        raw = subprocess.run(
            [
                ffprobe, "-v", "quiet", "-print_format", "json",
                "-show_format", "-show_streams", str(path),
            ],
            check=True,
            capture_output=True,
            timeout=60,
        ).stdout
        body = json.loads(raw)
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError):
        return None
    duration = float(body.get("format", {}).get("duration") or 0)
    has_audio = any(s.get("codec_type") == "audio" for s in body.get("streams", []))
    return {"duration_s": duration, "has_audio": has_audio}


def _face_receipt(clip_path: Path, still_path: Path, frame_count: int):
    """Producer's YuNet+SFace evidence, when the models are staged on this host."""

    try:
        from diony_producer.face_identity import face_models, measure_face_identity
    except ImportError:
        return None
    if face_models() is None:
        return None
    return measure_face_identity(clip_path, still_path, frame_count=frame_count)


def _measure_identity(
    path: Path,
    clip: ClipUnit,
    package: ProductionPackage | None,
    families: dict[str, str],
    measured: dict[str, float],
    issues: list[str],
) -> None:
    if package is None or not clip.continuity.character_packs:
        return
    for pack in clip.continuity.character_packs:
        try:
            char = package.character(str(pack.get("char_id", "")))
        except KeyError:
            continue
        still = char.hero_stills.get("front") or next(iter(char.hero_stills.values()), "")
        if not still or not Path(still).is_file():
            continue
        receipt = _face_receipt(path, Path(still), frame_count=max(1, clip.duration_s * 24))
        if receipt is None or not receipt.measured:
            continue  # this character unmeasurable; the rest of the cast still gets checked
        if receipt.verified is None:
            issues.append(f"identity: no usable face found for {char.name} — check framing")
            families["identity"] = "unmeasured"
            continue
        measured[f"identity_min:{char.char_id}"] = float(receipt.similarity_min or 0)
        if receipt.verified:
            families["identity"] = "pass"
        else:
            families["identity"] = "fail"
            issues.append(
                f"{char.name} reads {receipt.similarity_min} against the sheet "
                f"(threshold {receipt.threshold}) — the face drifted"
            )
            return


CHAIN_DRIFT_FAIL = 0.10  # producer warns at 0.06; a bridge that moved 0.10 is a new room
TRANSCRIPT_ACCURACY_FLOOR = 0.85


def _measure_frames(
    path: Path, clip: ClipUnit, families: dict[str, str], measured: dict[str, float], issues: list[str]
) -> None:
    """Sampled-frame evidence (black frames are a hard fail; exposure advisory)."""
    try:
        from diony_producer.quality import measure_frame_quality
    except ImportError:
        return
    try:
        receipt = measure_frame_quality(path, frame_count=max(1, clip.duration_s * 24))
    except Exception:  # noqa: BLE001 - optional evidence never crashes a review
        return
    if not receipt.measured:
        return
    measured["black_frames"] = float(receipt.black_frames)
    measured["sharpness_min"] = float(receipt.sharpness_min)
    if receipt.black_frames:
        families["frames"] = "fail"
        issues.append(
            f"{receipt.black_frames} sampled frame(s) are black — the picture died mid-clip"
        )
    else:
        families["frames"] = "pass"
        if not receipt.exposure_ok:
            issues.append("exposure sits outside the working band (advisory)")


def _measure_continuity(
    path: Path, prev_clip_path: Path | None, families: dict[str, str], measured: dict[str, float], issues: list[str]
) -> None:
    """Chain colour drift against the previous accepted take's last frame."""
    if prev_clip_path is None or not Path(prev_clip_path).is_file():
        return
    try:
        from diony_producer.quality import chain_color_drift, frame_channel_stats
    except ImportError:
        return
    prev_stats = frame_channel_stats(Path(prev_clip_path), last=True)
    current_stats = frame_channel_stats(path, last=False)
    if prev_stats is None or current_stats is None:
        return
    drift = chain_color_drift(current_stats, prev_stats)
    measured["chain_color_drift"] = float(drift)
    if drift > CHAIN_DRIFT_FAIL:
        families["continuity"] = "fail"
        issues.append(
            f"colour drifted {drift:.3f} from the previous shot — the room changed under the cut"
        )
    else:
        families["continuity"] = "pass"
        if drift > 0.06:
            issues.append(f"colour drift {drift:.3f} vs the previous shot (advisory)")


def _measure_transcript(
    path: Path, clip: ClipUnit, families: dict[str, str], measured: dict[str, float], issues: list[str]
) -> None:
    """Did it say the scripted words? Whisper conformance when the model is staged."""
    if clip.clip_class != "sync-dialogue" or not clip.audio_packet.script_excerpt:
        return
    try:
        from diony_producer.speech_transcript import transcribe_line, whisper_model_path
    except ImportError:
        return
    if whisper_model_path() is None:
        return
    authored = str(clip.audio_packet.script_excerpt[0].get("line", ""))
    measurement = transcribe_line(path, authored)
    if measurement is None:
        return
    measured["transcript_accuracy"] = float(measurement.word_accuracy)
    if measurement.word_accuracy < TRANSCRIPT_ACCURACY_FLOOR or not measurement.last_word_ok:
        families["script_conformance"] = "fail"
        missing = ", ".join(measurement.missed_words[:6])
        issues.append(
            f"the spoken line diverges from the script "
            f"(accuracy {measurement.word_accuracy:.2f}"
            + (f"; not heard: {missing}" if missing else "")
            + ")"
        )
    else:
        families["script_conformance"] = "pass"


def _measure_adherence(
    path: Path, clip: ClipUnit, families: dict[str, str], measured: dict[str, float], issues: list[str]
) -> None:
    """CLIP subject match: a beautiful clip of the wrong thing must not pass."""
    try:
        from diony_producer.semantic_qc import ADHERENCE_FLOOR, prompt_adherence
    except ImportError:
        return
    subject = clip.camera_idea
    if clip.audio_packet.script_excerpt:
        subject += ". " + str(clip.audio_packet.script_excerpt[0].get("line", ""))
    score = prompt_adherence(path, subject, frame_count=max(1, clip.duration_s * 24))
    if score is None:
        return
    measured["prompt_adherence"] = float(score)
    if score < ADHERENCE_FLOOR:
        families["adherence"] = "fail"
        issues.append(
            f"the picture does not match the shot (CLIP {score:.2f} < {ADHERENCE_FLOOR}) — "
            "wrong subject, not wrong quality"
        )
    else:
        families["adherence"] = "pass"


def review_clip(
    path: Path,
    clip: ClipUnit,
    *,
    op_id: str = "",
    package: ProductionPackage | None = None,
    prev_clip_path: Path | None = None,
) -> ClipReview:
    families: dict[str, str] = {}
    measured: dict[str, float] = {}
    issues: list[str] = []

    if not path.is_file() or path.stat().st_size < MIN_DELIVERY_BYTES:
        families["delivery"] = "fail"
        issues.append("media missing or too small to be a clip")
    else:
        families["delivery"] = "pass"
        measured["bytes"] = float(path.stat().st_size)

    probe = _probe(path) if families["delivery"] == "pass" else None
    if probe is None:
        families["duration"] = "unmeasured"
        families["audio"] = "unmeasured"
    else:
        measured["duration_s"] = probe["duration_s"]
        delta = abs(probe["duration_s"] - clip.duration_s)
        if delta <= DURATION_TOLERANCE_S:
            families["duration"] = "pass"
        else:
            families["duration"] = "fail"
            issues.append(
                f"measured {probe['duration_s']:.1f}s vs booked {clip.duration_s}s"
            )
        if clip.clip_class in AUDIO_REQUIRED_CLASSES:
            if probe["has_audio"]:
                families["audio"] = "pass"
            else:
                families["audio"] = "fail"
                issues.append("no audio stream on a clip whose script carries sound")
        else:
            families["audio"] = "pass" if probe["has_audio"] else "unmeasured"

    if families["delivery"] == "pass":
        # A crashed measurement degrades ITS family to unmeasured with the
        # crash on record — it never aborts the whole review (an evidence-free
        # take slipping into the film is worse than a missing family).
        for family_name, measure in (
            ("identity", lambda: _measure_identity(path, clip, package, families, measured, issues)),
            ("frames", lambda: _measure_frames(path, clip, families, measured, issues)),
            ("continuity", lambda: _measure_continuity(path, prev_clip_path, families, measured, issues)),
            ("script_conformance", lambda: _measure_transcript(path, clip, families, measured, issues)),
            ("adherence", lambda: _measure_adherence(path, clip, families, measured, issues)),
        ):
            try:
                measure()
            except Exception as err:  # noqa: BLE001 - optional evidence never kills a review
                families[family_name] = "unmeasured"
                issues.append(f"{family_name} measurement crashed: {type(err).__name__}: {err}")

    # Anything a host cannot measure stays an honest 'unmeasured' — it drops
    # out of the verdict instead of scoring zero or silently passing.
    families.setdefault("identity", "unmeasured")
    families.setdefault("frames", "unmeasured")
    families.setdefault("continuity", "unmeasured")
    families.setdefault("script_conformance", "unmeasured")
    families.setdefault("adherence", "unmeasured")

    if any(v == "fail" for v in families.values()):
        verdict = "fail"
        recommended = ""
    elif clip.continuity.character_packs and families["identity"] == "unmeasured":
        verdict = "warn"
        recommended = "accept"
        issues.append("identity unmeasured on this host — accepting requires a human eye on the face")
    else:
        verdict = "pass"
        recommended = "accept"

    return ClipReview(
        id=f"rev-{uuid.uuid4().hex[:10]}",
        clip_id=clip.clip_id,
        op_id=op_id,
        verdict=verdict,
        families=families,
        measured=measured,
        issues=issues,
        recommended_op=recommended,
    )
