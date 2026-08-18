from __future__ import annotations

from diony_contracts.enums import (
    ArtifactKind,
    CameraMove,
    CustomerKind,
    Engine,
    FinishIntent,
    GateName,
    ProjectStatus,
    QCVerdict,
    TakeOrigin,
)
from diony_contracts.models import (
    Artifact,
    Bible,
    Brief,
    Character,
    Gate,
    MixLock,
    Note,
    Project,
    QCReport,
    Shot,
    Take,
    Timeline,
    TimelineEvent,
)


def demo_project() -> Project:
    """Pilot fixture so Producer UX is real on Day 1 without Wan."""
    bible = Bible(
        title="Pilot — The Night Press",
        logline="A late-shift typesetter discovers tomorrow's edition already printed.",
        fps=23.976,
        visual_grammar=(
            "Tungsten practicals, 35mm spherical, wet asphalt, restrained camera, "
            "ink and brass, no daylight interiors."
        ),
        world="1962 metropolitan daily. Linotype, rain, one newsroom, one alley.",
        characters=[
            Character(
                id="char-mara",
                name="Mara Chen",
                still_asset_ids=["still-mara-wide", "still-mara-close"],
                wardrobe="Ink-stained oxford, dark wool vest, hair pinned",
            )
        ],
        locked=True,
    )
    shots = [
        Shot(
            id="sh-010",
            scene="1",
            slug="INT NEWSROOM — MARA AT THE CASE",
            duration_frames=120,
            fps=23.976,
            focal_mm=40,
            t_stop=2.8,
            lighting_ratio="4:1 key/fill, tungsten practical",
            camera_move=CameraMove.DOLLY_IN,
            prompt=(
                "Mara Chen, 32, ink-stained oxford, stands at a California job case. "
                "Slow dolly in, 40mm, T2.8, warm practicals, shallow depth, "
                "lead type glints. Continuity locked to still-mara-wide."
            ),
            negative_prompt="morphing face, extra fingers, daylight, modern screens",
            seed=10401,
            character_ref_ids=["char-mara"],
            keyframe_asset_ids=["still-mara-wide"],
            engine=Engine.I2V_A14B,
            finish=FinishIntent.DELIVERABLE,
            audio_line="They're running tomorrow's obit tonight.",
            selected_take_id="sh-010-t2",
        ),
        Shot(
            id="sh-020",
            scene="1",
            slug="INSERT — TOMORROW'S FRONT PAGE",
            duration_frames=72,
            fps=23.976,
            focal_mm=90,
            t_stop=4.0,
            lighting_ratio="hard top, paper white held",
            camera_move=CameraMove.STATIC,
            prompt="Macro of wet newsprint. Headline already set for tomorrow. 90mm.",
            seed=10420,
            engine=Engine.T2V_A14B,
            finish=FinishIntent.DELIVERABLE,
        ),
        Shot(
            id="sh-030",
            scene="2",
            slug="EXT ALLEY — MARA IN RAIN",
            duration_frames=168,
            fps=23.976,
            focal_mm=35,
            t_stop=2.0,
            lighting_ratio="1:8, sodium vs tungsten spill",
            camera_move=CameraMove.STEADICAM,
            prompt=(
                "Mara exits to a rain alley. Steadicam follow, 35mm, T2.0, "
                "sodium vapor vs newsroom spill. Face locked to still-mara-close."
            ),
            seed=10430,
            character_ref_ids=["char-mara"],
            keyframe_asset_ids=["still-mara-close"],
            engine=Engine.I2V_A14B,
            finish=FinishIntent.DELIVERABLE,
            audio_line="If it's printed, someone already died.",
        ),
    ]
    takes = [
        Take(
            id="sh-010-t1",
            shot_id="sh-010",
            attempt=1,
            engine=Engine.I2V_A14B,
            seed=10401,
            asset_720p_id="neg-sh-010-t1",
            version=1,
            origin=TakeOrigin.GENERATION,
            label="hold",
            artifacts=[
                Artifact(
                    id="art-sh-010-t1-720",
                    take_id="sh-010-t1",
                    kind=ArtifactKind.NEGATIVE_720,
                    path="takes/sh-010-t1/negative_720.txt",
                    duration_frames=120,
                )
            ],
            qc=QCReport(
                id="qc-sh-010-t1",
                take_id="sh-010-t1",
                shot_id="sh-010",
                verdict=QCVerdict.FAIL,
                identity_ok=False,
                morph_ok=True,
                flicker_ok=True,
                duration_ok=True,
                legal_ok=True,
                notes="Identity drift vs still-mara-wide. Retake.",
            ),
        ),
        Take(
            id="sh-010-t2",
            shot_id="sh-010",
            attempt=2,
            engine=Engine.I2V_A14B,
            seed=10411,
            asset_720p_id="neg-sh-010-t2",
            asset_1080p_id="fin-sh-010-t2",
            version=1,
            origin=TakeOrigin.GENERATION,
            label="print",
            artifacts=[
                Artifact(
                    id="art-sh-010-t2-720",
                    take_id="sh-010-t2",
                    kind=ArtifactKind.NEGATIVE_720,
                    path="takes/sh-010-t2/negative_720.txt",
                    duration_frames=120,
                ),
                Artifact(
                    id="art-sh-010-t2-1080",
                    take_id="sh-010-t2",
                    kind=ArtifactKind.FINISH_1080,
                    path="takes/sh-010-t2/finish_1080.txt",
                    width=1920,
                    height=1080,
                    duration_frames=120,
                ),
            ],
            qc=QCReport(
                id="qc-sh-010-t2",
                take_id="sh-010-t2",
                shot_id="sh-010",
                verdict=QCVerdict.PASS,
                identity_ok=True,
                morph_ok=True,
                flicker_ok=True,
                duration_ok=True,
                legal_ok=True,
                lipsync_ok=True,
            ),
        ),
        Take(
            id="sh-020-t1",
            shot_id="sh-020",
            attempt=1,
            engine=Engine.T2V_A14B,
            seed=10420,
            asset_720p_id="neg-sh-020-t1",
            version=1,
            origin=TakeOrigin.GENERATION,
            artifacts=[
                Artifact(
                    id="art-sh-020-t1-720",
                    take_id="sh-020-t1",
                    kind=ArtifactKind.NEGATIVE_720,
                    path="takes/sh-020-t1/negative_720.txt",
                    duration_frames=72,
                )
            ],
            qc=QCReport(
                id="qc-sh-020-t1",
                take_id="sh-020-t1",
                shot_id="sh-020",
                verdict=QCVerdict.WARN,
                identity_ok=True,
                morph_ok=True,
                flicker_ok=True,
                duration_ok=True,
                legal_ok=True,
                notes="Paper stock slightly modern. Usable.",
            ),
        ),
        Take(
            id="sh-030-t1",
            shot_id="sh-030",
            attempt=1,
            engine=Engine.I2V_A14B,
            seed=10430,
            asset_720p_id="neg-sh-030-t1",
            version=1,
            origin=TakeOrigin.GENERATION,
            artifacts=[
                Artifact(
                    id="art-sh-030-t1-720",
                    take_id="sh-030-t1",
                    kind=ArtifactKind.NEGATIVE_720,
                    path="takes/sh-030-t1/negative_720.txt",
                    duration_frames=168,
                )
            ],
            qc=QCReport(
                id="qc-sh-030-t1",
                take_id="sh-030-t1",
                shot_id="sh-030",
                verdict=QCVerdict.PASS,
                identity_ok=True,
                morph_ok=True,
                flicker_ok=True,
                duration_ok=True,
                legal_ok=True,
                lipsync_ok=True,
            ),
        ),
    ]
    return Project(
        id="ep-pilot-night-press",
        status=ProjectStatus.SHOOTING,
        customer_kind=CustomerKind.SERIES,
        brief=Brief(
            idea="A late-shift typesetter discovers tomorrow's edition already printed.",
            customer_kind=CustomerKind.SERIES,
            target_minutes=8,
            audience="broadcast drama",
            title_hint="Pilot — The Night Press",
        ),
        bible=bible,
        shots=shots,
        takes=takes,
        notes=[
            Note(
                id="n-human-010",
                author="human",
                body="Hold her eyes on the nick in the case. Don't blink off it.",
                shot_id="sh-010",
            )
        ],
        timeline=Timeline(
            id="tl-pilot",
            events=[
                TimelineEvent(shot_id="sh-010", start_frame=0, handle_in=8, handle_out=8),
                TimelineEvent(shot_id="sh-020", start_frame=120, handle_in=4, handle_out=4),
                TimelineEvent(shot_id="sh-030", start_frame=192, handle_in=8, handle_out=8),
            ],
        ),
        mix=MixLock(id="mix-pilot", picture_lock_timeline_id="tl-pilot"),
        gates=[
            Gate(name=GateName.BIBLE, open=False, locked_by="showrunner"),
            Gate(name=GateName.PICTURE, open=True),
            Gate(name=GateName.MIX, open=True),
            Gate(name=GateName.DISTRIBUTION, open=True),
        ],
        fleet_mode="SPLIT",
    )
