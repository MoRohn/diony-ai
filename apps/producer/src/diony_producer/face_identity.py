"""Face-embedding identity evidence (Blueprint 24 §14).

Reference-lock SSIM says the picture still resembles the still; it cannot say
the *face* is the same person. With OpenCV Zoo's YuNet detector and SFace
recogniser staged on the host (`DIONY_FACE_ID_DETECTOR_MODEL`,
`DIONY_FACE_ID_RECOGNIZER_MODEL`; both Apache-2.0, run natively in cv2), every
reference-conditioned negative measures the cosine similarity between the
faces in its conditioning still and the most prominent face in each sampled
frame. SFace's published decision threshold is 0.363. Without the models the
fields stay None and gate nothing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from diony_producer.quality import decode_sampled_frames, sample_frame_indices

FACE_IDENTITY_VERSION = "diony-face-identity-v1"
# OpenCV SFace: cosine ≥ 0.363 (or L2 ≤ 1.128) is the same identity.
DEFAULT_FACE_SIMILARITY_MIN = 0.363
# Below this box size (px on the 1280x720 negative) an embedding is noise.
MIN_FACE_PX = 48
DETECT_SCORE_MIN = 0.7
FRAME_WIDTH = 1280
FRAME_HEIGHT = 720


@dataclass(frozen=True)
class FaceIdentityReceipt:
    measured: bool
    reference_faces: int = 0
    frames_sampled: int = 0
    frames_with_face: int = 0
    similarities: tuple[float, ...] = ()
    similarity_min: float | None = None
    similarity_mean: float | None = None
    verified: bool | None = None
    threshold: float = DEFAULT_FACE_SIMILARITY_MIN
    error: str = ""
    notes: list[str] = field(default_factory=list)


def face_models() -> tuple[Path, Path] | None:
    detector = os.environ.get("DIONY_FACE_ID_DETECTOR_MODEL", "").strip()
    recognizer = os.environ.get("DIONY_FACE_ID_RECOGNIZER_MODEL", "").strip()
    if not detector or not recognizer:
        return None
    paths = (Path(detector), Path(recognizer))
    if not all(path.is_file() for path in paths):
        return None
    return paths


def face_threshold() -> float:
    raw = os.environ.get("DIONY_FACE_ID_SIMILARITY_MIN", "").strip()
    try:
        return float(raw) if raw else DEFAULT_FACE_SIMILARITY_MIN
    except ValueError:
        return DEFAULT_FACE_SIMILARITY_MIN


@lru_cache(maxsize=1)
def _engines(detector: str, recognizer: str):
    import cv2  # type: ignore[import-not-found]

    yunet = cv2.FaceDetectorYN.create(detector, "", (FRAME_WIDTH, FRAME_HEIGHT), DETECT_SCORE_MIN, 0.3, 5000)
    sface = cv2.FaceRecognizerSF.create(recognizer, "")
    return yunet, sface


def _detect(yunet, bgr):
    height, width = bgr.shape[:2]
    yunet.setInputSize((width, height))
    _count, faces = yunet.detect(bgr)
    if faces is None:
        return []
    rows = []
    for row in faces:
        w, h = float(row[2]), float(row[3])
        if min(w, h) < MIN_FACE_PX:
            continue
        rows.append(row)
    return rows


def _embed(sface, bgr, face_row):
    aligned = sface.alignCrop(bgr, face_row)
    return sface.feature(aligned)


def _bgr(rgb):
    import numpy as np

    return np.ascontiguousarray(rgb[:, :, ::-1])


def _load_image_rgb(path: Path):
    import numpy as np

    frames = decode_sampled_frames(path, [0], width=FRAME_WIDTH, height=FRAME_HEIGHT)
    if not frames:
        raise RuntimeError("reference still could not be decoded")
    return np.asarray(frames[0])


def measure_face_identity(negative: Path, reference: Path, *, frame_count: int) -> FaceIdentityReceipt:
    """Cosine similarity between reference faces and each sampled frame's main face."""
    models = face_models()
    if models is None:
        return FaceIdentityReceipt(False, error="face models are not staged")
    threshold = face_threshold()
    try:
        import cv2  # type: ignore[import-not-found]  # noqa: F401

        yunet, sface = _engines(str(models[0]), str(models[1]))
        reference_rgb = _load_image_rgb(reference)
        reference_bgr = _bgr(reference_rgb)
        reference_faces = _detect(yunet, reference_bgr)
        if not reference_faces:
            return FaceIdentityReceipt(True, reference_faces=0, threshold=threshold, notes=["no face in the conditioning still"])
        reference_embeddings = [_embed(sface, reference_bgr, row) for row in reference_faces]
        frames = decode_sampled_frames(negative, sample_frame_indices(frame_count), width=FRAME_WIDTH, height=FRAME_HEIGHT)
        similarities: list[float] = []
        with_face = 0
        for frame in frames:
            bgr = _bgr(frame)
            faces = _detect(yunet, bgr)
            if not faces:
                continue
            # The most prominent face: largest box, the one S2V animates.
            main = max(faces, key=lambda row: float(row[2]) * float(row[3]))
            embedding = _embed(sface, bgr, main)
            score = max(
                float(sface.match(embedding, candidate, cv2.FaceRecognizerSF_FR_COSINE))
                for candidate in reference_embeddings
            )
            similarities.append(round(score, 4))
            with_face += 1
    except Exception as exc:  # noqa: BLE001 - unmeasurable identity never fails a take
        return FaceIdentityReceipt(False, error=str(exc)[:300], threshold=threshold)
    if not similarities:
        return FaceIdentityReceipt(
            True,
            reference_faces=len(reference_faces),
            frames_sampled=len(frames),
            frames_with_face=0,
            threshold=threshold,
            notes=["no usable face in the sampled frames"],
        )
    minimum = min(similarities)
    return FaceIdentityReceipt(
        True,
        reference_faces=len(reference_faces),
        frames_sampled=len(frames),
        frames_with_face=with_face,
        similarities=tuple(similarities),
        similarity_min=round(minimum, 4),
        similarity_mean=round(sum(similarities) / len(similarities), 4),
        verified=minimum >= threshold,
        threshold=threshold,
    )


__all__ = ["DEFAULT_FACE_SIMILARITY_MIN", "FACE_IDENTITY_VERSION", "FaceIdentityReceipt", "face_models", "face_threshold", "measure_face_identity"]
