"""Local mouth-sync measurement: the speaking face's aperture against the dialogue.

Blueprint 22 asks for SyncNet-class audiovisual evidence measured locally. This
module measures it from two open, offline models Studio App pins on the host:

* OpenCV YuNet (``DIONY_FACE_DETECTOR_MODEL``) finds every face at the clip's
  native raster. Dialogue shots are often wide; a 90-pixel face in a 1280x720
  frame is below what a landmarker's own short-range detector accepts.
* MediaPipe FaceLandmarker (``DIONY_FACE_LANDMARKER_MODEL``) runs on each face
  crop and yields the inner-lip gap and the ``jawOpen`` blendshape.

Per face track the two aperture signals are combined and compared with the
clean dialogue's per-frame RMS envelope over the *delivered* span (the frames
the finish stage keeps), searching ±12 frames of lag (±750 ms at 16 fps). The track whose mouth
follows the line best is the speaker; listeners with a still mouth are rejected
by their aperture variance, not by position. Confidence, offset, and
consistency are all measured quantities:

* ``lip_sync_confidence`` = 0.75 + 0.25 × syllable-smoothed peak correlation,
  so the locked 0.80 floor means correlation ≥ 0.20 (a qualified measurement
  still needs a peak of at least ``MIN_CORRELATION``).
* ``audiovisual_offset_ms_p95`` comes from the *unsmoothed* correlation peak
  with parabolic sub-frame refinement; diagnostics state the frame period, the
  measurement's resolution.
* ``performance_consistency`` is the balanced agreement between speech
  activity and mouth *modulation* (speech is oscillatory jaw motion; a held
  smile is not), with one frame of hysteresis at each speech boundary.
  Calibrated on a wide three-character Wan2.2 S2V shot (90-pixel faces at
  16 fps): landmark jitter on a small face sits near half the speech-time
  modulation, so the talking floor is two-thirds of it; the locked default
  floor is 0.75 (see ``VoiceQCReceipt.performance_consistency_min``), a step
  under that calibration so a small articulating face is not held for jitter.

Only degenerate evidence — no tracked face, inactive audio, too few frames —
stays unqualified (a one-take hold). A tracked, audible clip whose mouth is
still, does not follow the line, or lags past the search window is a
*measured* defect: a qualified failure that earns a retake.
"""

from __future__ import annotations

import json
import math
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

BACKEND_NAME = "diony-local-mouth-sync-v3"

MIN_POINTS = 12
LAG_RANGE_FRAMES = 12  # ±750 ms at 16 fps: a large S2V lag must be measured, not clipped at the edge
MIN_CORRELATION = 0.30
MIN_PROMINENCE = 0.05
NOISE_MARGIN = 1.25  # floor = 1.25 × the chance-correlation ceiling for the search
MIN_ACTIVE_AUDIO = 0.20
MIN_TRACK_COVERAGE = 0.60
MIN_APERTURE_STD = 0.02
SMOOTH_FRAMES = 3
MODULATION_WINDOW = 5
ACTIVITY_RATIO = 0.65  # mouth counts as talking above two-thirds of its speech-time modulation
AUDIO_ACTIVE_RATIO = 0.25
FACE_CROP_MARGIN = 0.5
FACE_CROP_SIZE = 256
TRACK_MAX_GAP_FRAMES = 3
TRACK_MAX_JUMP_FRACTION = 0.06

# MediaPipe FaceLandmarker canonical indices.
_UPPER_INNER_LIP = 13
_LOWER_INNER_LIP = 14
_FOREHEAD = 10
_CHIN = 152


class MouthSyncError(RuntimeError):
    pass


@dataclass
class FaceTrack:
    """One face followed across frames; ``jaw``/``gap`` are NaN where unmeasured."""

    frames: list[int] = field(default_factory=list)
    jaw: list[float] = field(default_factory=list)
    gap: list[float] = field(default_factory=list)
    centers: list[tuple[float, float]] = field(default_factory=list)

    @property
    def center(self) -> tuple[float, float]:
        if not self.centers:
            return (0.0, 0.0)
        xs = [c[0] for c in self.centers]
        ys = [c[1] for c in self.centers]
        return (float(np.median(xs)), float(np.median(ys)))


# --------------------------------------------------------------------------- signals


def _zscore(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=np.float64)
    std = float(np.std(values))
    if std <= 1e-12:
        return np.zeros_like(values)
    return (values - float(np.mean(values))) / std


def _smooth(values: np.ndarray, window: int) -> np.ndarray:
    values = np.asarray(values, dtype=np.float64)
    if window <= 1 or values.size < window:
        return values
    kernel = np.ones(window, dtype=np.float64) / window
    return np.convolve(values, kernel, mode="same")


def _fill_gaps(frame_count: int, frames: list[int], values: list[float]) -> np.ndarray:
    series = np.full(frame_count, np.nan, dtype=np.float64)
    for index, value in zip(frames, values):
        if 0 <= index < frame_count and value is not None and not math.isnan(value):
            series[index] = float(value)
    known = ~np.isnan(series)
    if not known.any():
        return np.zeros(frame_count, dtype=np.float64)
    positions = np.arange(frame_count)
    return np.interp(positions, positions[known], series[known])


def envelope_per_frame(samples: np.ndarray, sample_rate: int, fps: float, frame_count: int) -> np.ndarray:
    """Per-frame RMS of the dialogue at the clip's own frame rate."""
    samples = np.asarray(samples, dtype=np.float64)
    envelope = np.zeros(frame_count, dtype=np.float64)
    for index in range(frame_count):
        start = int(round(index * sample_rate / fps))
        stop = int(round((index + 1) * sample_rate / fps))
        segment = samples[start:stop]
        envelope[index] = float(np.sqrt(np.mean(segment * segment))) if segment.size else 0.0
    return envelope


def cross_correlation(signal: np.ndarray, reference: np.ndarray, lag_range: int) -> dict[int, float]:
    """Normalized correlation of ``signal`` shifted by each lag against ``reference``."""
    length = min(signal.size, reference.size)
    a = _zscore(signal[:length])
    b = _zscore(reference[:length])
    scores: dict[int, float] = {}
    for lag in range(-lag_range, lag_range + 1):
        left = a[max(0, lag) : length + min(0, lag)]
        right = b[max(0, -lag) : length - max(0, lag)]
        scores[lag] = float(np.mean(left * right)) if left.size and right.size else -1.0
    return scores


def refine_peak(scores: dict[int, float], best: int) -> float:
    left = scores.get(best - 1)
    right = scores.get(best + 1)
    peak = scores[best]
    if left is None or right is None:
        return float(best)
    curvature = left - 2 * peak + right
    if curvature >= -1e-9:
        return float(best)
    return best + 0.5 * (left - right) / curvature


def modulation(values: np.ndarray, window: int = MODULATION_WINDOW) -> np.ndarray:
    """Local standard deviation: speech is oscillatory jaw motion, a held pose is not."""
    values = np.asarray(values, dtype=np.float64)
    half = window // 2
    out = np.zeros(values.size, dtype=np.float64)
    for index in range(values.size):
        segment = values[max(0, index - half) : index + half + 1]
        out[index] = float(np.std(segment)) if segment.size else 0.0
    return out


def _dilate(mask: np.ndarray, radius: int = 1) -> np.ndarray:
    out = mask.copy()
    for shift in range(1, radius + 1):
        out[shift:] |= mask[:-shift]
        out[:-shift] |= mask[shift:]
    return out


def measure_sync(aperture: np.ndarray, envelope: np.ndarray, fps: float) -> dict[str, Any]:
    """Measure one mouth-aperture series against the dialogue envelope.

    Both inputs are per-frame at ``fps`` and already restricted to the
    delivered span. Returns every measured quantity plus ``disqualifiers``.
    """
    aperture = np.asarray(aperture, dtype=np.float64)
    envelope = np.asarray(envelope, dtype=np.float64)
    length = int(min(aperture.size, envelope.size))
    aperture = aperture[:length]
    envelope = envelope[:length]
    disqualifiers: list[str] = []
    if length < MIN_POINTS:
        disqualifiers.append(f"only {length} comparable frames; need {MIN_POINTS}")
    loud = float(np.percentile(envelope, 95)) if length else 0.0
    audio_active = envelope > max(1e-4, AUDIO_ACTIVE_RATIO * loud) if length else np.zeros(0, dtype=bool)
    active_fraction = float(np.mean(audio_active)) if length else 0.0
    if active_fraction < MIN_ACTIVE_AUDIO:
        disqualifiers.append(f"active audio fraction {active_fraction:.2f} is below {MIN_ACTIVE_AUDIO:.2f}")
    aperture_std = float(np.std(aperture)) if length else 0.0
    # Measured defects: the clip was measurable but the performance is absent
    # or weak. These fail the take (and earn a retake); they do not hold it.
    defects: list[str] = []
    if aperture_std < MIN_APERTURE_STD:
        defects.append(f"mouth aperture is still (std {aperture_std:.4f} below {MIN_APERTURE_STD:.2f})")
    envelope_std = float(np.std(envelope)) if length else 0.0

    raw_corr = 0.0
    smooth_corr = 0.0
    best_lag = 0
    refined_lag = 0.0
    prominence = 0.0
    if length >= MIN_POINTS and aperture_std > 1e-9 and envelope_std > 1e-9:
        raw_scores = cross_correlation(aperture, envelope, LAG_RANGE_FRAMES)
        best_lag = max(raw_scores, key=lambda lag: raw_scores[lag])
        raw_corr = raw_scores[best_lag]
        refined_lag = refine_peak(raw_scores, best_lag)
        smooth_scores = cross_correlation(
            _smooth(aperture, SMOOTH_FRAMES), _smooth(envelope, SMOOTH_FRAMES), LAG_RANGE_FRAMES
        )
        smooth_best = max(smooth_scores, key=lambda lag: smooth_scores[lag])
        smooth_corr = smooth_scores[smooth_best]
        prominence = smooth_corr - float(np.median(list(smooth_scores.values())))
        # Searching L lags over N frames, unrelated signals reach about
        # sqrt(2 ln L / N) by chance; the floor must sit clear of that.
        lags_searched = 2 * LAG_RANGE_FRAMES + 1
        noise_ceiling = math.sqrt(2.0 * math.log(lags_searched) / max(1, length))
        correlation_floor = max(MIN_CORRELATION, NOISE_MARGIN * noise_ceiling)
        if smooth_corr < correlation_floor:
            defects.append(
                f"mouth motion does not follow the line: peak correlation {smooth_corr:.4f} is below "
                f"{correlation_floor:.2f} (chance ceiling {noise_ceiling:.2f} over {lags_searched} lags × {length} frames)"
            )
        elif prominence < MIN_PROMINENCE:
            defects.append(f"correlation peak prominence {prominence:.4f} is below {MIN_PROMINENCE:.2f}")
        if abs(best_lag) >= LAG_RANGE_FRAMES:
            defects.append(
                f"correlation peak sits at the ±{LAG_RANGE_FRAMES}-frame search edge; the offset is at least "
                f"{LAG_RANGE_FRAMES * 1000.0 / fps:.0f} ms"
            )
    elif length >= MIN_POINTS and envelope_std <= 1e-9:
        disqualifiers.append("audio signal carries no measurable structure")

    # Consistency: does the mouth modulate when, and only when, the line is spoken?
    consistency = 0.0
    true_positive_rate = 0.0
    true_negative_rate = 0.0
    if length >= MIN_POINTS and audio_active.any():
        mouth_mod = modulation(aperture)
        lag = int(round(refined_lag))
        if lag >= 0:
            mouth_part = mouth_mod[lag:]
            audio_part = audio_active[: length - lag]
        else:
            mouth_part = mouth_mod[: length + lag]
            audio_part = audio_active[-lag:]
        speech_level = float(np.median(mouth_part[audio_part])) if audio_part.any() else 0.0
        mouth_active = mouth_part > ACTIVITY_RATIO * max(speech_level, 1e-9)
        silent = ~_dilate(audio_part, 1)
        true_positive_rate = float(np.mean(mouth_active[audio_part])) if audio_part.any() else 0.0
        if silent.any():
            true_negative_rate = float(np.mean(~mouth_active[silent]))
            consistency = 0.5 * (true_positive_rate + true_negative_rate)
        else:
            true_negative_rate = 1.0
            consistency = true_positive_rate

    frame_period_ms = 1000.0 / fps if fps > 0 else 0.0
    offset_ms = abs(refined_lag) * frame_period_ms
    confidence = max(0.0, min(1.0, 0.75 + 0.25 * max(0.0, smooth_corr)))
    if defects and not disqualifiers:
        # A measured absence of performance is a failing measurement, never a
        # pass by accident: a still mouth or chance-level correlation carries
        # no lip-sync evidence, so confidence reports the floor.
        confidence = min(confidence, 0.75)
    return {
        "frames": length,
        "raw_correlation": round(raw_corr, 4),
        "correlation": round(smooth_corr, 4),
        "prominence": round(prominence, 4),
        "best_lag_frames": best_lag,
        "refined_lag_frames": round(refined_lag, 3),
        "frame_period_ms": round(frame_period_ms, 3),
        "active_audio_fraction": round(active_fraction, 3),
        "aperture_std": round(aperture_std, 4),
        "lip_sync_confidence": round(confidence, 4),
        "audiovisual_offset_ms_p95": round(offset_ms, 3),
        "performance_consistency": round(max(0.0, min(1.0, consistency)), 4),
        "true_positive_rate": round(true_positive_rate, 3),
        "true_negative_rate": round(true_negative_rate, 3),
        "disqualifiers": disqualifiers,
        "defects": defects,
    }


def combined_aperture(frame_count: int, track: FaceTrack) -> np.ndarray:
    """Equal-weight blend of the geometric lip gap and the jawOpen blendshape."""
    jaw = _fill_gaps(frame_count, track.frames, track.jaw)
    gap = _fill_gaps(frame_count, track.frames, track.gap)
    jaw_std = float(np.std(jaw))
    gap_std = float(np.std(gap))
    if jaw_std <= 1e-9 and gap_std <= 1e-9:
        return np.zeros(frame_count, dtype=np.float64)
    # Keep jawOpen's physical scale (listeners sit near 0.001, speech near 0.1+)
    # so the stillness floor stays meaningful; the lip gap adds a second,
    # geometry-only witness of the same motion.
    blended = 0.5 * (jaw + (gap * (jaw_std / gap_std) if gap_std > 1e-9 else jaw))
    return blended


def choose_speaker(
    tracks: list[FaceTrack],
    envelope: np.ndarray,
    fps: float,
    frame_count: int,
) -> tuple[FaceTrack | None, dict[str, Any] | None, list[str]]:
    """The speaker is the covered face whose mouth follows the line best."""
    notes: list[str] = []
    best: tuple[FaceTrack, dict[str, Any]] | None = None
    for track in tracks:
        coverage = len(set(track.frames)) / max(1, frame_count)
        if coverage < MIN_TRACK_COVERAGE:
            notes.append(
                f"face at ({track.center[0]:.0f},{track.center[1]:.0f}) covered {coverage:.0%} of frames; skipped"
            )
            continue
        result = measure_sync(combined_aperture(frame_count, track), envelope, fps)
        result["coverage"] = round(coverage, 3)
        result["center"] = track.center
        notes.append(
            f"face at ({track.center[0]:.0f},{track.center[1]:.0f}): aperture std={result['aperture_std']:.4f} "
            f"correlation={result['correlation']:.3f} lag={result['best_lag_frames']}"
        )
        # A still mouth can score a chance correlation once z-scored; a face
        # that actually moves always outranks one that does not.
        moving = result["aperture_std"] >= MIN_APERTURE_STD
        if best is None:
            best = (track, result)
            continue
        best_moving = best[1]["aperture_std"] >= MIN_APERTURE_STD
        if moving and not best_moving:
            best = (track, result)
        elif moving == best_moving and result["correlation"] > best[1]["correlation"]:
            best = (track, result)
    if best is None:
        return None, None, notes
    return best[0], best[1], notes


# --------------------------------------------------------------------------- media


def _run(command: list[str], *, timeout: float) -> bytes:
    try:
        result = subprocess.run(command, capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise MouthSyncError(f"local media command failed: {exc}") from exc
    if result.returncode:
        detail = (result.stderr or result.stdout or b"").decode("utf-8", errors="replace")
        lines = [line.strip() for line in detail.splitlines() if line.strip()]
        raise MouthSyncError(lines[-1] if lines else f"media command exited {result.returncode}")
    return result.stdout


def probe_video(video: Path, ffprobe: str) -> tuple[int, int, float]:
    raw = _run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,r_frame_rate",
            "-of",
            "json",
            str(video),
        ],
        timeout=30,
    )
    try:
        stream = json.loads(raw.decode("utf-8", errors="replace"))["streams"][0]
        width = int(stream["width"])
        height = int(stream["height"])
        rate = str(stream.get("r_frame_rate") or "24")
        if "/" in rate:
            numerator, denominator = rate.split("/", 1)
            fps = float(numerator) / float(denominator)
        else:
            fps = float(rate)
    except (KeyError, IndexError, ValueError, ZeroDivisionError, json.JSONDecodeError) as exc:
        raise MouthSyncError("video stream could not be probed") from exc
    if not (1.0 <= fps <= 120.0) or width <= 0 or height <= 0:
        raise MouthSyncError("video stream reports an unusable raster or frame rate")
    return width, height, fps


def decode_frames(video: Path, *, ffmpeg: str, width: int, height: int, max_frames: int) -> np.ndarray:
    raw = _run(
        [
            ffmpeg,
            "-v",
            "error",
            "-nostdin",
            "-i",
            str(video),
            "-frames:v",
            str(int(max_frames)),
            "-f",
            "rawvideo",
            "-pix_fmt",
            "bgr24",
            "-",
        ],
        timeout=300,
    )
    frame_bytes = width * height * 3
    count = len(raw) // frame_bytes
    if count <= 0:
        raise MouthSyncError("audiovisual QC decoded no video frames")
    return np.frombuffer(raw[: count * frame_bytes], dtype=np.uint8).reshape(count, height, width, 3)


def decode_audio(audio: Path, *, ffmpeg: str, sample_rate: int = 16_000) -> np.ndarray:
    raw = _run(
        [ffmpeg, "-v", "error", "-nostdin", "-i", str(audio), "-ac", "1", "-ar", str(sample_rate), "-f", "f32le", "-"],
        timeout=120,
    )
    return np.frombuffer(raw, dtype="<f4").astype(np.float64)


def delivered_span_ms(performance_metadata: Path) -> float | None:
    """The span the finish stage keeps: the script's pacing target or its last line end."""
    try:
        payload = json.loads(performance_metadata.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    script = payload.get("script") if isinstance(payload, dict) else None
    if not isinstance(script, dict):
        return None
    target = script.get("pacing_target_ms")
    if isinstance(target, (int, float)) and target > 0:
        return float(target)
    ends = []
    for line in script.get("lines") or []:
        if not isinstance(line, dict):
            continue
        start = line.get("start_ms") or 0
        length = line.get("target_duration_ms") or 0
        if isinstance(start, (int, float)) and isinstance(length, (int, float)) and length > 0:
            ends.append(float(start) + float(length))
    return max(ends) if ends else None


# --------------------------------------------------------------------------- faces


def face_tracks(
    frames: np.ndarray,
    *,
    detector_model: Path,
    landmarker_model: Path,
) -> list[FaceTrack]:
    """Detect every face per frame, landmark each crop, and follow faces across frames."""
    import cv2  # noqa: PLC0415 — host-only dependency
    import mediapipe as mp  # noqa: PLC0415
    from mediapipe.tasks.python import BaseOptions, vision  # noqa: PLC0415

    count, height, width, _ = frames.shape
    detector = cv2.FaceDetectorYN.create(
        str(detector_model), "", (width, height), score_threshold=0.5, nms_threshold=0.3, top_k=50
    )
    options = vision.FaceLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=str(landmarker_model), delegate=BaseOptions.Delegate.CPU),
        running_mode=vision.RunningMode.IMAGE,
        num_faces=1,
        output_face_blendshapes=True,
        min_face_detection_confidence=0.3,
    )
    tracks: list[FaceTrack] = []
    max_jump = TRACK_MAX_JUMP_FRACTION * (width + height)
    with vision.FaceLandmarker.create_from_options(options) as landmarker:
        for index in range(count):
            frame = np.ascontiguousarray(frames[index])
            _, detections = detector.detect(frame)
            for detection in [] if detections is None else detections:
                x, y, w, h = (int(v) for v in detection[:4])
                if w <= 0 or h <= 0:
                    continue
                x0 = max(0, int(x - FACE_CROP_MARGIN * w))
                y0 = max(0, int(y - FACE_CROP_MARGIN * h))
                x1 = min(width, int(x + w + FACE_CROP_MARGIN * w))
                y1 = min(height, int(y + h + FACE_CROP_MARGIN * h))
                crop = frame[y0:y1, x0:x1]
                if crop.size == 0:
                    continue
                scale = FACE_CROP_SIZE / max(crop.shape[:2])
                resized = cv2.resize(
                    crop, (max(32, int(crop.shape[1] * scale)), max(32, int(crop.shape[0] * scale)))
                )
                rgb = np.ascontiguousarray(cv2.cvtColor(resized, cv2.COLOR_BGR2RGB))
                result = landmarker.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
                jaw = math.nan
                gap = math.nan
                if result.face_landmarks:
                    points = result.face_landmarks[0]
                    face_height = abs(points[_CHIN].y - points[_FOREHEAD].y) or 1e-6
                    gap = abs(points[_LOWER_INNER_LIP].y - points[_UPPER_INNER_LIP].y) / face_height
                    if result.face_blendshapes:
                        jaw = next(
                            (b.score for b in result.face_blendshapes[0] if b.category_name == "jawOpen"),
                            math.nan,
                        )
                center = (x + w / 2.0, y + h / 2.0)
                chosen: FaceTrack | None = None
                chosen_distance = math.inf
                for track in tracks:
                    if index - track.frames[-1] > TRACK_MAX_GAP_FRAMES or track.frames[-1] == index:
                        continue
                    last = track.centers[-1]
                    distance = abs(last[0] - center[0]) + abs(last[1] - center[1])
                    if distance < max_jump and distance < chosen_distance:
                        chosen, chosen_distance = track, distance
                if chosen is None:
                    chosen = FaceTrack()
                    tracks.append(chosen)
                chosen.frames.append(index)
                chosen.jaw.append(jaw)
                chosen.gap.append(gap)
                chosen.centers.append(center)
    return tracks


def detect_faces(image: Path, *, detector_model: Path) -> list[tuple[float, float, float, float, float]]:
    """Faces in one still as (x, y, w, h, score) in pixels, largest first."""
    import cv2  # noqa: PLC0415 — host-only dependency

    frame = cv2.imread(str(image), cv2.IMREAD_COLOR)
    if frame is None or frame.size == 0:
        raise MouthSyncError(f"still could not be decoded: {image}")
    height, width = frame.shape[:2]
    detector = cv2.FaceDetectorYN.create(
        str(detector_model), "", (width, height), score_threshold=0.5, nms_threshold=0.3, top_k=50
    )
    _, detections = detector.detect(np.ascontiguousarray(frame))
    faces = [
        (float(d[0]), float(d[1]), float(d[2]), float(d[3]), float(d[14]))
        for d in ([] if detections is None else detections)
        if float(d[2]) > 0 and float(d[3]) > 0
    ]
    return sorted(faces, key=lambda f: f[2] * f[3], reverse=True)


# --------------------------------------------------------------------------- entry


def measure_clip(
    video: Path,
    audio: Path,
    performance_metadata: Path,
    *,
    detector_model: Path,
    landmarker_model: Path,
    ffmpeg: str = "ffmpeg",
    ffprobe: str = "ffprobe",
) -> dict[str, Any]:
    """Full local mouth-sync measurement in the audiovisual-qc response shape."""
    width, height, fps = probe_video(video, ffprobe)
    samples = decode_audio(audio, ffmpeg=ffmpeg)
    audio_ms = 1000.0 * samples.size / 16_000.0
    span_ms = delivered_span_ms(performance_metadata) or audio_ms
    span_ms = max(1.0, min(span_ms, audio_ms if audio_ms > 0 else span_ms))
    span_frames = max(1, int(round(span_ms / 1000.0 * fps)) + 1)
    frames = decode_frames(video, ffmpeg=ffmpeg, width=width, height=height, max_frames=span_frames)
    frame_count = int(frames.shape[0])
    envelope = envelope_per_frame(samples, 16_000, fps, frame_count)
    tracks = face_tracks(frames, detector_model=detector_model, landmarker_model=landmarker_model)
    speaker, result, notes = choose_speaker(tracks, envelope, fps, frame_count)
    diagnostics = [
        f"delivered span {span_ms:.0f} ms = {frame_count} frames at {fps:.3f} fps "
        f"(offset resolution {1000.0 / fps:.1f} ms per frame)",
        f"faces tracked={len(tracks)}",
        *notes,
        "Measured locally; no hosted service used.",
    ]
    if speaker is None or result is None:
        diagnostics.append("unqualified: no face was tracked across the delivered span")
        return {
            "lip_sync_confidence": 0.0,
            "audiovisual_offset_ms_p95": 0.0,
            "performance_consistency": 0.0,
            "diagnostics": diagnostics,
            "backend": BACKEND_NAME,
            "qualified_for_visual_retake": False,
        }
    diagnostics.extend(
        [
            f"speaker face at ({result['center'][0]:.0f},{result['center'][1]:.0f}) "
            f"covered {result['coverage']:.0%} of frames; aperture std={result['aperture_std']:.4f}",
            f"mouth-audio correlation={result['correlation']:.4f} (raw {result['raw_correlation']:.4f}) "
            f"prominence={result['prominence']:.4f}",
            f"best_lag_frames={result['best_lag_frames']} refined_lag_frames={result['refined_lag_frames']:.3f}",
            f"active_audio_fraction={result['active_audio_fraction']:.3f}",
            f"mouth modulation agreement: speech {result['true_positive_rate']:.2f} · silence {result['true_negative_rate']:.2f}",
        ]
    )
    diagnostics.extend(f"defect: {reason}" for reason in result.get("defects", []))
    diagnostics.extend(f"unqualified: {reason}" for reason in result["disqualifiers"])
    return {
        "lip_sync_confidence": result["lip_sync_confidence"],
        "audiovisual_offset_ms_p95": result["audiovisual_offset_ms_p95"],
        "performance_consistency": result["performance_consistency"],
        "diagnostics": diagnostics,
        "backend": BACKEND_NAME,
        "qualified_for_visual_retake": not result["disqualifiers"],
    }
