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
    streams = body.get("streams", [])
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    return {
        "duration_s": duration,
        "has_audio": has_audio,
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
    }


def probe(path: Path) -> dict | None:
    """Duration, audio presence and raster of one file (None without ffprobe)."""
    return _probe(path)


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


# Measured clusters (s-1b80548c4d): healthy bridge joins sit under 0.005; a
# scene reset reads 0.06–0.12. The old 0.10 let a completely different room
# through at 0.062 as "advisory" — the bands now split the clusters.
CHAIN_DRIFT_FAIL = 0.05
CHAIN_DRIFT_ADVISORY = 0.03

# Neighbor drift is blind to COMPOUNDING: s-2e0a74644b drifted 0.003/link and
# still mutated its newsroom into a different room by link 4 (0.055 vs its own
# anchor, 0.12 by link 7; same-set truth measures 0.015-0.017). Every clip is
# therefore also measured against the scene's MASTER look.
ANCHOR_DRIFT_FAIL = 0.06
ANCHOR_DRIFT_ADVISORY = 0.03

# Blueprint 28: every director-lane location track is speechless — the voice
# is recorded in post against the cut. Heard words on a delivered take are a
# provider-invented voice that would stack under the narrator as a second
# talking voice. The floors keep an ASR mirage (a breath, a sung syllable of
# score the VAD let through) from spending a retake; a real voice clears
# both easily.
SPEECH_WORDS_FAIL = 3
SPEECH_SECONDS_FAIL = 1.5


def _measure_frames(
    path: Path, clip: ClipUnit, families: dict[str, str], measured: dict[str, float], issues: list[str]
) -> None:
    """Sampled-frame evidence (black frames are a hard fail; exposure advisory)."""
    _measure_frames_for(path, int(clip.duration_s), families, measured, issues)


def _measure_frames_for(
    path: Path, duration_s: int, families: dict[str, str], measured: dict[str, float], issues: list[str]
) -> None:
    try:
        from diony_producer.quality import measure_frame_quality
    except ImportError:
        return
    try:
        receipt = measure_frame_quality(path, frame_count=max(1, duration_s * 24))
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
    path: Path,
    prev_clip_path: Path | None,
    families: dict[str, str],
    measured: dict[str, float],
    issues: list[str],
    anchor_path: Path | None = None,
) -> None:
    """Chain colour drift against the previous take's join frame, AND against
    the scene's master anchor — the second catches drift that compounds
    invisibly link by link."""
    try:
        from diony_producer.quality import chain_color_drift, frame_channel_stats
    except ImportError:
        return
    verdicts: list[str] = []
    if prev_clip_path is not None and Path(prev_clip_path).is_file():
        prev_stats = frame_channel_stats(Path(prev_clip_path), last=True)
        current_stats = frame_channel_stats(path, last=False)
        if prev_stats is not None and current_stats is not None:
            drift = chain_color_drift(current_stats, prev_stats)
            measured["chain_color_drift"] = float(drift)
            if drift > CHAIN_DRIFT_FAIL:
                verdicts.append("fail")
                issues.append(
                    f"colour drifted {drift:.3f} from the previous shot — the room changed under the cut"
                )
            else:
                verdicts.append("pass")
                if drift > CHAIN_DRIFT_ADVISORY:
                    issues.append(f"colour drift {drift:.3f} vs the previous shot (advisory)")
    if anchor_path is not None and Path(anchor_path).is_file():
        anchor_stats = frame_channel_stats(Path(anchor_path), last=False)
        end_stats = frame_channel_stats(path, last=True)
        if anchor_stats is not None and end_stats is not None:
            anchor_drift = chain_color_drift(end_stats, anchor_stats)
            measured["anchor_color_drift"] = float(anchor_drift)
            if anchor_drift > ANCHOR_DRIFT_FAIL:
                verdicts.append("fail")
                issues.append(
                    f"the set drifted {anchor_drift:.3f} from the scene's master look — "
                    "the room is mutating away from its anchor"
                )
            else:
                verdicts.append("pass")
                if anchor_drift > ANCHOR_DRIFT_ADVISORY:
                    issues.append(
                        f"set drift {anchor_drift:.3f} vs the scene anchor (advisory)"
                    )
    if verdicts:
        families["continuity"] = "fail" if "fail" in verdicts else "pass"


def location_speech(path: Path):
    """The producer's speech-presence receipt for this track, or None when the
    host cannot measure it (no staged whisper model, unreadable media)."""
    try:
        from diony_producer.speech_transcript import detect_speech
    except ImportError:
        return None
    try:
        return detect_speech(path)
    except Exception:  # noqa: BLE001 - optional evidence never crashes a review
        return None


def _measure_speech(
    path: Path, families: dict[str, str], measured: dict[str, float], issues: list[str]
) -> None:
    """Is a voice TALKING on the location track? (Blueprint 28: it must not be
    — the narrator records every authored word in post, so heard words here
    are a provider-invented voice that would play under the narration.)"""
    detection = location_speech(path)
    if detection is None:
        return
    measured["speech_words"] = float(detection.word_count)
    measured["speech_s"] = float(detection.speech_s)
    if detection.word_count >= SPEECH_WORDS_FAIL or detection.speech_s >= SPEECH_SECONDS_FAIL:
        families["speech"] = "fail"
        heard = detection.transcript[:120]
        issues.append(
            "a talking voice is on the location track "
            f"(heard: “{heard}”) — the voice track is recorded in post; "
            "the location sound must carry no spoken words"
        )
    else:
        families["speech"] = "pass"


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
    anchor_path: Path | None = None,
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
            ("continuity", lambda: _measure_continuity(path, prev_clip_path, families, measured, issues, anchor_path)),
            ("speech", lambda: _measure_speech(path, families, measured, issues)),
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
    families.setdefault("speech", "unmeasured")
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


# --- chained one-shot links ---------------------------------------------------
#
# A chain link has no ClipUnit and no bible, but it has evidence: the frame it
# was told to open from, the link before it, and its own first and last frame.
# Before Blueprint 29 no review ran on chain links at all — twelve clips in
# eight visual languages printed and cut with nothing on record (s-e790706066).

OPEN_DRIFT_FAIL = ANCHOR_DRIFT_FAIL
INTRA_DRIFT_ADVISORY = 0.10


def _measure_chain_continuity(
    path: Path,
    *,
    start_image: Path | None,
    prev_clip_path: Path | None,
    scene_change: bool,
    families: dict[str, str],
    measured: dict[str, float],
    issues: list[str],
) -> None:
    try:
        from diony_producer.quality import chain_color_drift, frame_channel_stats
    except ImportError:
        return
    verdicts: list[str] = []
    first = frame_channel_stats(path, last=False)
    last = frame_channel_stats(path, last=True)
    if start_image is not None and Path(start_image).is_file() and first is not None:
        opener = frame_channel_stats(Path(start_image), last=False)
        if opener is not None:
            drift = chain_color_drift(first, opener)
            measured["open_drift"] = float(drift)
            if drift > OPEN_DRIFT_FAIL:
                verdicts.append("fail")
                issues.append(
                    f"the clip ignored its opening frame (drift {drift:.3f}) — "
                    "it did not start where it was told to"
                )
            else:
                verdicts.append("pass")
    if (
        not scene_change
        and prev_clip_path is not None
        and Path(prev_clip_path).is_file()
        and first is not None
    ):
        prev_stats = frame_channel_stats(Path(prev_clip_path), last=True)
        if prev_stats is not None:
            drift = chain_color_drift(first, prev_stats)
            measured["chain_color_drift"] = float(drift)
            if drift > CHAIN_DRIFT_FAIL:
                verdicts.append("fail")
                issues.append(
                    f"colour drifted {drift:.3f} from the previous clip — the look changed under the cut"
                )
            else:
                verdicts.append("pass")
                if drift > CHAIN_DRIFT_ADVISORY:
                    issues.append(f"colour drift {drift:.3f} vs the previous clip (advisory)")
    if first is not None and last is not None:
        # a camera move or a lighting change legitimately shifts channel
        # stats inside a clip — advisory only, never a fail on its own
        intra = chain_color_drift(last, first)
        measured["intra_clip_drift"] = float(intra)
        if intra > INTRA_DRIFT_ADVISORY:
            issues.append(f"the look wandered {intra:.3f} within the clip (advisory)")
    if verdicts:
        families["continuity"] = "fail" if "fail" in verdicts else "pass"


def review_chain_link(
    path: Path,
    *,
    op_id: str,
    duration_s: int,
    start_image: Path | None = None,
    prev_clip_path: Path | None = None,
    scene_change: bool = False,
) -> ClipReview:
    """Review one link of a chained one-shot: delivery, duration, frames, and
    look continuity — did it open from the frame it was given, did it hold
    the previous clip's colour when it was a continuation, did it wander."""
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
        if abs(probe["duration_s"] - duration_s) <= DURATION_TOLERANCE_S:
            families["duration"] = "pass"
        else:
            families["duration"] = "fail"
            issues.append(f"measured {probe['duration_s']:.1f}s vs booked {duration_s}s")
        families["audio"] = "pass" if probe["has_audio"] else "unmeasured"

    if families["delivery"] == "pass":
        for family_name, measure in (
            ("frames", lambda: _measure_frames_for(path, duration_s, families, measured, issues)),
            # a chain link's narration rides its op (Blueprint 28) — its
            # location track is as speechless-by-law as any scripted clip's
            ("speech", lambda: _measure_speech(path, families, measured, issues)),
            (
                "continuity",
                lambda: _measure_chain_continuity(
                    path,
                    start_image=start_image,
                    prev_clip_path=prev_clip_path,
                    scene_change=scene_change,
                    families=families,
                    measured=measured,
                    issues=issues,
                ),
            ),
        ):
            try:
                measure()
            except Exception as err:  # noqa: BLE001 - optional evidence never kills a review
                families[family_name] = "unmeasured"
                issues.append(f"{family_name} measurement crashed: {type(err).__name__}: {err}")

    families.setdefault("frames", "unmeasured")
    families.setdefault("continuity", "unmeasured")
    families.setdefault("identity", "unmeasured")
    families.setdefault("speech", "unmeasured")
    verdict = "fail" if any(v == "fail" for v in families.values()) else "pass"
    return ClipReview(
        id=f"rev-{uuid.uuid4().hex[:10]}",
        clip_id=op_id,
        op_id=op_id,
        verdict=verdict,
        families=families,
        measured=measured,
        issues=issues,
        recommended_op="" if verdict == "fail" else "accept",
    )
