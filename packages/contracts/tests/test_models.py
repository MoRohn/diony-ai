from diony_contracts.enums import CameraMove, Engine, FinishIntent, QCVerdict
from diony_contracts.fixture import demo_project
from diony_contracts.models import Bible, Character, Note, Project, QCReport, Shot, Take


def _bible() -> Bible:
    return Bible(
        title="Pilot — The Night Press",
        logline="A late-shift typesetter discovers tomorrow's edition already printed.",
        visual_grammar="Tungsten interiors, 35mm spherical, wet streets, restrained camera.",
        world="1962 metropolitan daily. Lead type, rain, one newsroom.",
        characters=[
            Character(
                id="char-mara",
                name="Mara Chen",
                still_asset_ids=["still-mara-wide", "still-mara-close"],
                wardrobe="Ink-stained oxford, dark wool vest",
            )
        ],
    )


def test_note_requires_attachment() -> None:
    try:
        Note(id="n1", author="human", body="orphan")
        raise AssertionError("expected failure")
    except Exception:
        pass
    Note(id="n1", author="human", body="hold the look", shot_id="sh-010")


def test_qc_cannot_pass_hard_fail() -> None:
    try:
        QCReport(
            id="qc1",
            take_id="t1",
            shot_id="s1",
            verdict=QCVerdict.PASS,
            identity_ok=False,
            morph_ok=True,
            flicker_ok=True,
            duration_ok=True,
            legal_ok=True,
        )
        raise AssertionError("expected failure")
    except Exception:
        pass


def test_master_must_be_deliverable() -> None:
    from diony_contracts.enums import DeliverableKind
    from diony_contracts.models import Deliverable

    try:
        Deliverable(
            id="d1",
            kind=DeliverableKind.PRORES_HQ,
            path="/vault/master.mov",
            finish=FinishIntent.PREVIZ,
        )
        raise AssertionError("expected failure")
    except Exception:
        pass


def test_project_roundtrip() -> None:
    shot = Shot(
        id="sh-010",
        scene="1",
        slug="INT NEWSROOM — MARA AT THE CASE",
        duration_frames=120,
        fps=23.976,
        focal_mm=40,
        t_stop=2.8,
        lighting_ratio="4:1 key/fill",
        camera_move=CameraMove.DOLLY_IN,
        prompt="Mara Chen at a California job case, tungsten practicals, slow dolly in.",
        seed=10401,
        character_ref_ids=["char-mara"],
        keyframe_asset_ids=["still-mara-wide"],
        engine=Engine.I2V_A14B,
        finish=FinishIntent.DELIVERABLE,
        audio_line="They're running tomorrow's obit tonight.",
    )
    take = Take(
        id="sh-010-t1",
        shot_id="sh-010",
        attempt=1,
        engine=Engine.I2V_A14B,
        seed=10401,
        asset_720p_id="neg-sh-010-t1",
        qc=QCReport(
            id="qc-sh-010-t1",
            take_id="sh-010-t1",
            shot_id="sh-010",
            verdict=QCVerdict.PASS,
            identity_ok=True,
            morph_ok=True,
            flicker_ok=True,
            duration_ok=True,
            legal_ok=True,
        ),
    )
    project = Project(id="ep-pilot-night-press", bible=_bible(), shots=[shot], takes=[take])
    data = project.model_dump(mode="json")
    again = Project.model_validate(data)
    assert again.shot("sh-010").focal_mm == 40
    assert again.takes_for("sh-010")[0].qc.verdict == QCVerdict.PASS


def test_demo_fixture_valid() -> None:
    project = demo_project()
    assert project.bible.locked is True
    assert project.shot("sh-010").finish.value == "deliverable"
    assert len(project.takes_for("sh-010")) == 2
    assert project.compute.path.value == "local"
    assert project.compute.profile == "spark-4x-v1"


def test_lease_site_needs_a_human() -> None:
    from diony_contracts.enums import ComputePath
    from diony_contracts.models import ClusterSite
    from pydantic import ValidationError

    try:
        ClusterSite(path=ComputePath.LEASE, profile="lease-gpu-v1")
        raise AssertionError("expected failure")
    except ValidationError:
        pass
    try:
        ClusterSite(
            path=ComputePath.LEASE,
            profile="spark-4x-v1",
            exception_by="Rohn Springfield",
        )
        raise AssertionError("expected failure")
    except ValidationError:
        pass
    site = ClusterSite(
        path=ComputePath.LEASE,
        profile="lease-gpu-v1",
        provider="coreweave",
        gpu_class="4×H100 80GB",
        exception_by="Rohn Springfield",
    )
    assert site.path == ComputePath.LEASE
    try:
        ClusterSite(path=ComputePath.LOCAL, profile="lease-gpu-v1")
        raise AssertionError("expected failure")
    except ValidationError:
        pass


def test_b300_site_needs_a_human() -> None:
    from diony_contracts.enums import ComputePath
    from diony_contracts.models import ClusterSite
    from pydantic import ValidationError

    try:
        ClusterSite(path=ComputePath.B300, profile="b300-v1")
        raise AssertionError("expected failure")
    except ValidationError:
        pass
    try:
        ClusterSite(
            path=ComputePath.B300,
            profile="spark-4x-v1",
            exception_by="Rohn Springfield",
        )
        raise AssertionError("expected failure")
    except ValidationError:
        pass
    site = ClusterSite(
        path=ComputePath.B300,
        profile="b300-v1",
        provider="this rack",
        exception_by="Rohn Springfield",
    )
    assert site.path == ComputePath.B300
    assert site.gpu_class == "1×B300"
    try:
        ClusterSite(path=ComputePath.LOCAL, profile="b300-v1")
        raise AssertionError("expected failure")
    except ValidationError:
        pass


def test_dual_site_needs_a_human() -> None:
    from diony_contracts.enums import ComputePath
    from diony_contracts.models import ClusterSite
    from pydantic import ValidationError

    try:
        ClusterSite(path=ComputePath.DUAL, profile="spark-2x-v1")
        raise AssertionError("expected failure")
    except ValidationError:
        pass
    try:
        ClusterSite(
            path=ComputePath.DUAL,
            profile="spark-4x-v1",
            exception_by="Rohn Springfield",
        )
        raise AssertionError("expected failure")
    except ValidationError:
        pass
    site = ClusterSite(
        path=ComputePath.DUAL,
        profile="spark-2x-v1",
        provider="this rack",
        exception_by="Rohn Springfield",
    )
    assert site.path == ComputePath.DUAL
    assert site.gpu_class == "2×GB10"
    try:
        ClusterSite(path=ComputePath.LOCAL, profile="spark-2x-v1")
        raise AssertionError("expected failure")
    except ValidationError:
        pass
