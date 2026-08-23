"""Frame-authoritative Gallery composition helpers."""

from __future__ import annotations

from diony_contracts.models import ClipComposition


def frame_to_milliseconds(frame: int, fps: float) -> int:
    if fps <= 0:
        raise ValueError("fps must be positive")
    return round(max(0, frame) * 1000 / fps)


def frame_to_timecode(frame: int, fps: float) -> str:
    if fps <= 0:
        raise ValueError("fps must be positive")
    nominal = 24 if abs(fps - 23.976) < 0.01 else round(fps)
    value = max(0, int(frame))
    hours, remainder = divmod(value, nominal * 3600)
    minutes, remainder = divmod(remainder, nominal * 60)
    seconds, frames = divmod(remainder, nominal)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}:{frames:02d}"


def composition_summary(composition: ClipComposition) -> dict[str, object]:
    return {
        "id": composition.id,
        "version": composition.version,
        "source_count": len(composition.sources),
        "junction_count": len(composition.junctions),
        "total_frames": composition.total_frames,
        "duration_ms": frame_to_milliseconds(composition.total_frames, composition.fps),
        "timecode": frame_to_timecode(composition.total_frames, composition.fps),
        "output": (
            f"{composition.width}×{composition.height} · "
            f"{composition.fps:.3f} fps · Rec.709"
        ),
        "status": composition.status.value,
        "asset_id": composition.asset_id,
        "preview_path": composition.preview_path or composition.proxy_path,
        "profile_name": composition.profile_name,
        "cost_center_ids": composition.cost_center_ids,
    }
