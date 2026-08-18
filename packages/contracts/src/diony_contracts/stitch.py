"""Intelligent stitch: overlap handles and frame-to-clip continuity.

Blueprint 05: adjacent shots share 8–16 frames. I2V / S2V / Animate inherit
the previous print’s last frame as the next keyframe. Never overwrite a parent take.
"""

from __future__ import annotations

from diony_contracts.edits import version_ladder
from diony_contracts.enums import Engine, PictureLockState
from diony_contracts.models import Project, Shot, Take, Timeline, TimelineEvent

FRAME_TO_CLIP_ENGINES = {
    Engine.I2V_A14B,
    Engine.S2V_14B,
    Engine.ANIMATE_14B,
}


def clamp_overlap(shot: Shot, incoming: bool) -> int:
    if not incoming:
        return 0
    room = max(0, shot.duration_frames - 1)
    overlap = shot.overlap_frames if 8 <= shot.overlap_frames <= 16 else 8
    return min(overlap, room)


def print_for(project: Project, shot: Shot) -> Take | None:
    takes = version_ladder(project, shot.id)
    if not takes:
        return None
    if shot.selected_take_id:
        try:
            return project.take(shot.selected_take_id)
        except KeyError:
            pass
    return takes[-1]


def stitch_events(project: Project) -> tuple[list[TimelineEvent], int]:
    """Overlap-aware cut. Incoming handle is shared frames, not added TRT."""
    events: list[TimelineEvent] = []
    start = 0
    prev_take: Take | None = None
    for index, shot in enumerate(project.shots):
        take = print_for(project, shot)
        incoming = clamp_overlap(shot, index > 0)
        outgoing = shot.overlap_frames if 8 <= shot.overlap_frames <= 16 else 8
        keyframe_from = None
        if index and prev_take and shot.engine in FRAME_TO_CLIP_ENGINES:
            keyframe_from = prev_take.id
        events.append(
            TimelineEvent(
                shot_id=shot.id,
                start_frame=start,
                handle_in=incoming,
                handle_out=outgoing,
                take_id=take.id if take else None,
                overlap_frames=incoming,
                keyframe_from_take_id=keyframe_from,
            )
        )
        start += shot.duration_frames - incoming
        prev_take = take
    return events, start


def assemble_program(project: Project) -> Project:
    """Select prints, stitch the timeline, record frame-to-clip links."""
    for shot in project.shots:
        take = print_for(project, shot)
        if take and not shot.selected_take_id:
            shot.selected_take_id = take.id
    events, total = stitch_events(project)
    fps = project.bible.fps
    if project.timeline:
        timeline_id = project.timeline.id
        lock = project.timeline.picture_lock
        previous = project.timeline.preview_path
    else:
        timeline_id = f"tl-{project.id}"
        lock = PictureLockState.OPEN
        previous = ""
    preview = f"program/{timeline_id}/stitch.txt"
    master = program_master_rel_for(timeline_id)
    if previous.endswith("master_1080.mov") or previous.endswith("master_1080.mp4"):
        preview = previous
    elif previous == master:
        preview = master
    project.timeline = Timeline(
        id=timeline_id,
        events=events,
        picture_lock=lock,
        total_frames=total,
        fps=fps,
        preview_path=preview,
        stitched=True,
    )
    return project


def program_master_rel_for(timeline_id: str) -> str:
    return f"program/{timeline_id}/master_1080.mov"


def program_master_rel(project: Project) -> str:
    tid = project.timeline.id if project.timeline else f"tl-{project.id}"
    return program_master_rel_for(tid)


def reel_label(target_minutes: float) -> str:
    if target_minutes <= 1:
        return "short hero"
    if target_minutes <= 15:
        return "short"
    return "first reel"


def program_view(project: Project) -> dict:
    events, total = stitch_events(project)
    fps = project.bible.fps or 23.976
    target = project.brief.target_minutes if project.brief else 0
    gaps = [
        shot.id
        for shot in project.shots
        if print_for(project, shot) is None
    ]
    clips = []
    for event, shot in zip(events, project.shots, strict=True):
        take = print_for(project, shot)
        artifacts = [a.model_dump(mode="json") for a in (take.artifacts if take else [])]
        clips.append(
            {
                "shot_id": shot.id,
                "slug": shot.slug,
                "scene": shot.scene,
                "engine": shot.engine.value,
                "duration_frames": shot.duration_frames,
                "start_frame": event.start_frame,
                "handle_in": event.handle_in,
                "overlap_frames": event.overlap_frames,
                "take_id": event.take_id,
                "origin": take.origin.value if take else None,
                "version": take.version if take else None,
                "keyframe_from_take_id": event.keyframe_from_take_id,
                "focal_mm": shot.focal_mm,
                "t_stop": shot.t_stop,
                "camera_move": shot.camera_move.value,
                "lighting_ratio": shot.lighting_ratio,
                "finish": shot.finish.value,
                "artifacts": artifacts,
            }
        )
    preview = ""
    stitched = False
    timeline_id = f"tl-{project.id}"
    if project.timeline:
        preview = project.timeline.preview_path
        stitched = project.timeline.stitched
        timeline_id = project.timeline.id
    return {
        "timeline_id": timeline_id,
        "fps": fps,
        "total_frames": total,
        "trt_seconds": round(total / fps, 3) if fps else 0,
        "target_minutes": target,
        "reel": reel_label(target or 8),
        "stitched": stitched,
        "preview_path": preview,
        "gaps": gaps,
        "clips": clips,
        "negative": "720p Wan",
        "master": "1080 Diony finish",
        "master_path": (
            project.timeline.preview_path
            if project.timeline and "master_1080" in project.timeline.preview_path
            else ""
        ),
    }
