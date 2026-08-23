from diony_contracts.enums import Engine
from diony_contracts.models import Brief
from diony_contracts.stitch import assemble_program, stitch_events
from diony_contracts.studio import expand_idea


def test_stitch_overlap_shortens_trt() -> None:
    project = expand_idea(
        Brief(
            idea="A courier delivers a letter that rewrites the city map.",
            customer_kind="feature",
            target_minutes=5,
        )
    )
    events, total = stitch_events(project)
    raw = sum(s.duration_frames for s in project.shots)
    assert events[0].handle_in == 0
    assert events[1].handle_in == 8
    assert total == raw - 8 * (len(project.shots) - 1)
    assert events[1].start_frame == project.shots[0].duration_frames - 8


def test_assemble_selects_prints_and_frame_to_clip() -> None:
    from diony_contracts.enums import ArtifactKind, QCVerdict, TakeOrigin
    from diony_contracts.models import Artifact, QCReport, Take

    project = expand_idea(
        Brief(
            idea="A lighthouse keeper paints the fog the color of warnings.",
            customer_kind="feature",
            target_minutes=2,
        )
    )
    for shot in project.shots:
        tid = f"{shot.id}-t1"
        project.takes.append(
            Take(
                id=tid,
                shot_id=shot.id,
                attempt=1,
                engine=shot.engine,
                seed=shot.seed,
                asset_720p_id=f"neg-{tid}",
                artifacts=[
                    Artifact(
                        id=f"art-{tid}-720",
                        take_id=tid,
                        kind=ArtifactKind.NEGATIVE_720,
                        path=f"takes/{tid}/negative_720.txt",
                        duration_frames=shot.duration_frames,
                    )
                ],
                qc=QCReport(
                    id=f"qc-{tid}",
                    take_id=tid,
                    shot_id=shot.id,
                    verdict=QCVerdict.WARN,
                    identity_ok=True,
                    morph_ok=True,
                    flicker_ok=True,
                    duration_ok=True,
                    legal_ok=True,
                ),
                origin=TakeOrigin.GENERATION,
            )
        )
    assemble_program(project)
    assert project.timeline is not None
    assert project.timeline.stitched is True
    assert all(s.selected_take_id for s in project.shots)
    i2v = next(s for s in project.shots if s.engine == Engine.I2V_A14B)
    event = next(e for e in project.timeline.events if e.shot_id == i2v.id)
    assert event.keyframe_from_take_id
    assert event.take_id == i2v.selected_take_id
