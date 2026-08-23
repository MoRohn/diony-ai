import pytest

from diony_contracts.composition import composition_summary, frame_to_milliseconds, frame_to_timecode
from diony_contracts.enums import GalleryCompositionStatus, GalleryTransition
from diony_contracts.models import ClipComposition, ClipJoinPoint, ClipJoinSource


def source(
    clip_id: str,
    *,
    frames: int = 240,
    trim_in: int = 0,
    trim_out: int | None = None,
) -> ClipJoinSource:
    return ClipJoinSource(
        clip_id=clip_id,
        project_id=f"project-{clip_id}",
        title=clip_id,
        path=f"program/{clip_id}/master_1080.mov",
        duration_frames=frames,
        trim_in_frame=trim_in,
        trim_out_frame=trim_out or frames,
        fps=24.0,
        width=1920,
        height=1080,
        codec="prores",
    )


def test_composition_is_non_destructive_1080_lineage_with_transition_overlap() -> None:
    composition = ClipComposition(
        id="cmp-1",
        project_id="project-a",
        version=1,
        fps=24.0,
        sources=[source("a", trim_in=12, trim_out=228), source("b", trim_in=24)],
        junctions=[
            ClipJoinPoint(
                left_clip_id="a",
                right_clip_id="b",
                transition=GalleryTransition.CROSS_DISSOLVE,
                duration_frames=24,
            )
        ],
        profile_name="Local maker",
        cost_center_ids=["local-production"],
    )

    summary = composition_summary(composition)

    assert summary["total_frames"] == 408
    assert summary["duration_ms"] == 17_000
    assert summary["output"] == "1920×1080 · 24.000 fps · Rec.709"
    assert composition.sources[0].path.endswith("master_1080.mov")
    assert composition.status == GalleryCompositionStatus.DRAFT


def test_composition_rejects_extreme_trims_and_invalid_junction_order() -> None:
    with pytest.raises(ValueError, match="at least two frames"):
        source("short", frames=20, trim_in=19, trim_out=20)

    with pytest.raises(ValueError, match="must connect consecutive clips"):
        ClipComposition(
            id="cmp-bad",
            project_id="project-a",
            sources=[source("a"), source("b"), source("c")],
            junctions=[
                ClipJoinPoint(left_clip_id="a", right_clip_id="c"),
                ClipJoinPoint(left_clip_id="c", right_clip_id="b"),
            ],
        )


def test_transition_duration_is_zero_for_cut_and_bounded_by_both_clips() -> None:
    with pytest.raises(ValueError, match="Clean cuts use zero"):
        ClipJoinPoint(
            left_clip_id="a",
            right_clip_id="b",
            transition=GalleryTransition.CUT,
            duration_frames=12,
        )

    with pytest.raises(ValueError, match="longer than the available trimmed media"):
        ClipComposition(
            id="cmp-long-transition",
            project_id="project-a",
            sources=[source("a", frames=10), source("b", frames=10)],
            junctions=[
                ClipJoinPoint(
                    left_clip_id="a",
                    right_clip_id="b",
                    transition=GalleryTransition.CROSS_DISSOLVE,
                    duration_frames=9,
                )
            ],
        )


def test_frame_precision_has_stable_timecode_and_millisecond_display() -> None:
    assert frame_to_timecode(240, 24.0) == "00:00:10:00"
    assert frame_to_milliseconds(1, 23.976) == 42
    assert frame_to_milliseconds(24, 24.0) == 1000
