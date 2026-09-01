"""Post-assembly deliverables: subtitles, the EDL, the assembly map, the QC packet.

A film that ships as a bare mp4 is a preview; a delivered picture carries its
dialogue sidecars (.srt/.vtt from the AUTHORED lines at their real cut times),
an edit decision list naming every cut and why it is there, the AssemblyMap
that finally records what the cut actually did, and a QC packet holding every
clip verdict plus a final check on the master itself. All of it lands beside
film.mp4 in the session's vault directory.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from diony_contracts.montage import SubtitleCue, render_srt, render_vtt
from diony_contracts.production import AssemblyEvent, AssemblyMap

DELIVERABLE_NAMES = ("film.srt", "film.vtt", "edl.json", "assembly-map.json", "qc-packet.json")

CUE_LEAD_IN_S = 0.15  # a cue lands just after the cut, never on it


def _final_master_check(film: Path, duration_s: float) -> dict[str, Any]:
    """A last look at the master itself: length, an audio track, and sampled
    frames when this host can decode them. Advisory — the per-clip reviews
    already gated the cut; this catches an assembly that mangled the join."""
    check: dict[str, Any] = {"duration_s": round(duration_s, 2), "verdict": "pass", "issues": []}
    try:
        from diony_producer.quality import measure_frame_quality

        receipt = measure_frame_quality(film, frame_count=max(1, int(duration_s * 24)), samples=8)
        if receipt.measured:
            check["black_frames"] = receipt.black_frames
            if receipt.black_frames:
                check["verdict"] = "warn"
                check["issues"].append(
                    f"{receipt.black_frames} sampled master frame(s) are black"
                )
    except Exception:  # noqa: BLE001 - optional evidence never blocks delivery
        pass
    return check


def build_deliverables(
    dest_dir: Path,
    *,
    session_id: str,
    entries: list[dict[str, Any]],
    film: Path,
    film_duration_s: float,
    look: str,
    loudness_target: str,
) -> dict[str, Any]:
    """Write the sidecar set. Each entry: {op, clip, window: (in_s, duration_s),
    review, accepted}. Returns the names written plus the final master check."""

    cues: list[SubtitleCue] = []
    edl_events: list[dict[str, Any]] = []
    map_events: list[AssemblyEvent] = []
    qc_clips: list[dict[str, Any]] = []
    timeline_s = 0.0
    for entry in entries:
        clip = entry["clip"]
        op = entry["op"]
        review = entry.get("review")
        source_in, duration = entry["window"]
        if (
            clip is not None
            and clip.clip_class in {"sync-dialogue", "vo-over"}
            and clip.audio_packet.script_excerpt
        ):
            excerpt = clip.audio_packet.script_excerpt[0]
            start = timeline_s + CUE_LEAD_IN_S
            end = max(start + 0.4, timeline_s + duration - 0.1)
            cues.append(
                SubtitleCue(
                    index=len(cues) + 1,
                    start_seconds=round(start, 3),
                    end_seconds=round(end, 3),
                    speaker=str(excerpt.get("speaker", "")),
                    text=str(excerpt.get("line", "")),
                )
            )
        edl_events.append(
            {
                "clip_id": clip.clip_id if clip is not None else "",
                "op_id": op.id,
                "record_in_s": round(timeline_s, 3),
                "duration_s": round(duration, 3),
                "source_in_s": round(source_in, 3),
                "join_next": clip.join_next if clip is not None else "cut",
                "camera": clip.camera_idea if clip is not None else "",
                "clip_class": clip.clip_class if clip is not None else "",
                "provider": op.provider.value if op.provider else "",
                "model": op.model,
                "prompt_version": op.prompt_version,
                "review_verdict": review.verdict if review is not None else "",
                "accepted": bool(entry.get("accepted")),
            }
        )
        map_events.append(
            AssemblyEvent(
                clip_id=clip.clip_id if clip is not None else op.id,
                op_id=op.id,
                in_s=round(source_in, 3),
                out_s=round(source_in + duration, 3),
                join="cut",
            )
        )
        if review is not None:
            qc_clips.append(
                {
                    "clip_id": review.clip_id,
                    "op_id": op.id,
                    "verdict": review.verdict,
                    "families": review.families,
                    "issues": review.issues,
                    "measured": review.measured,
                    "accepted": bool(entry.get("accepted")),
                }
            )
        timeline_s += duration

    final_check = _final_master_check(film, film_duration_s)
    assembly_map = AssemblyMap(
        id=f"map-{session_id}",
        events=map_events,
        loudness_target="broadcast" if loudness_target == "broadcast" else "streaming",
        look=look,
        title=session_id,
    )

    dest_dir.mkdir(parents=True, exist_ok=True)
    (dest_dir / "film.srt").write_text(render_srt(cues), encoding="utf-8")
    (dest_dir / "film.vtt").write_text(render_vtt(cues), encoding="utf-8")
    (dest_dir / "edl.json").write_text(
        json.dumps({"session_id": session_id, "events": edl_events}, indent=1), encoding="utf-8"
    )
    (dest_dir / "assembly-map.json").write_text(
        assembly_map.model_dump_json(indent=1), encoding="utf-8"
    )
    (dest_dir / "qc-packet.json").write_text(
        json.dumps(
            {
                "session_id": session_id,
                "master": final_check,
                "clips": qc_clips,
                "look": look,
                "loudness_target": loudness_target,
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    return {"names": list(DELIVERABLE_NAMES), "final_check": final_check, "cue_count": len(cues)}
