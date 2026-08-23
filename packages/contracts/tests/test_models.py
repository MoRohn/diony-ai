import pytest

from diony_contracts.enums import (
    AssetKind,
    CameraMove,
    Engine,
    FinishIntent,
    ProductionStopDisposition,
    QCVerdict,
)
from diony_contracts.fixture import demo_project
from diony_contracts.models import (
    Bible,
    Character,
    Note,
    ProductionStopReceipt,
    ProductionRestartReceipt,
    ProductionPauseReceipt,
    Project,
    QCReport,
    Shot,
    Take,
    Timeline,
)


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


def test_qc_parity_dimensions_cannot_pass_a_measured_motion_failure() -> None:
    with pytest.raises(ValueError, match="hard QC fail"):
        QCReport(
            id="qc-motion",
            take_id="take-motion",
            shot_id="shot-motion",
            verdict=QCVerdict.PASS,
            identity_ok=True,
            morph_ok=True,
            flicker_ok=True,
            duration_ok=True,
            legal_ok=True,
            motion_ok=False,
            camera_ok=True,
            lighting_ok=True,
            production_clean_ok=True,
            audio_ready_ok=True,
        )


def test_continuity_qc_cannot_pass_without_verified_boundary() -> None:
    try:
        QCReport(
            id="qc-boundary",
            take_id="take-2",
            shot_id="shot-2",
            verdict=QCVerdict.PASS,
            identity_ok=True,
            morph_ok=True,
            flicker_ok=True,
            duration_ok=True,
            legal_ok=True,
            continuity_from_take_id="take-1",
            continuity_similarity=0.2,
            continuity_verified=False,
        )
        raise AssertionError("expected continuity failure")
    except ValueError as exc:
        assert "continuity" in str(exc).casefold()


def test_qc_roundtrip_preserves_measured_temporal_evidence() -> None:
    report = QCReport(
        id="qc-measured",
        take_id="take-measured",
        shot_id="shot-measured",
        verdict=QCVerdict.PASS,
        identity_ok=True,
        morph_ok=True,
        flicker_ok=True,
        duration_ok=True,
        legal_ok=True,
        measurement_version="diony-temporal-v1",
        temporal_delta_p95=0.081,
        temporal_residual_p95=0.072,
        flicker_luma_p95=0.019,
        measured_frame_count=41,
    )

    restored = QCReport.model_validate(report.model_dump(mode="json"))

    assert restored.measurement_version == "diony-temporal-v1"
    assert restored.temporal_delta_p95 == pytest.approx(0.081)
    assert restored.temporal_residual_p95 == pytest.approx(0.072)
    assert restored.flicker_luma_p95 == pytest.approx(0.019)
    assert restored.measured_frame_count == 41


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
    project.mark_production_start()
    project.mark_production_end()
    stamped = Project.model_validate(project.model_dump(mode="json"))
    assert stamped.production_started_at is not None
    assert stamped.production_ended_at is not None


def test_timeline_keeps_recoverable_gallery_removal_state() -> None:
    timeline = Timeline(
        id="tl-gallery",
        gallery_removed_at="2026-08-21T10:30:00Z",
    )

    restored = Timeline.model_validate(timeline.model_dump(mode="json"))

    assert restored.gallery_removed_at is not None


def test_project_roundtrips_a_durable_incomplete_production_stop() -> None:
    project = demo_project()
    project.production_stop = ProductionStopReceipt(
        disposition=ProductionStopDisposition.KEEP,
        completed_take_ids=["sh-010-t2", "sh-020-t1"],
        result_path=f"projects/{project.id}/program/tl-{project.id}/incomplete_720.mov",
        result_sha256="a" * 64,
        result_width=1280,
        result_height=720,
    )

    restored = Project.model_validate(project.model_dump(mode="json"))

    assert restored.production_stop is not None
    assert restored.production_stop.disposition == ProductionStopDisposition.KEEP
    assert restored.production_stop.completed_take_ids == ["sh-010-t2", "sh-020-t1"]
    assert restored.production_stop.result_path.endswith("incomplete_720.mov")


def test_project_roundtrips_a_same_project_production_restart_receipt() -> None:
    project = demo_project()
    stopped = ProductionStopReceipt(
        disposition=ProductionStopDisposition.KEEP,
        completed_take_ids=["sh-010-t1", "sh-020-t1"],
        result_path=f"projects/{project.id}/program/tl-{project.id}/incomplete_720.mov",
        result_sha256="b" * 64,
        result_width=1280,
        result_height=720,
    )
    project.production_restarts.append(
        ProductionRestartReceipt(
            reason="reference_and_performance_recalibration",
            previous_project_version=1,
            new_project_version=2,
            previous_stop=stopped,
            preserved_take_ids=["sh-010-t1", "sh-020-t1"],
            replacement_take_ids=["sh-010-t2", "sh-020-t2"],
            prompt_strategy_version="wan22-four-layer-v2",
            reference_asset_ids=["ast-dionysius"],
        )
    )

    restored = Project.model_validate(project.model_dump(mode="json"))

    assert restored.production_restarts[0].previous_stop.result_sha256 == "b" * 64
    assert restored.production_restarts[0].preserved_take_ids == [
        "sh-010-t1",
        "sh-020-t1",
    ]
    assert restored.production_restarts[0].replacement_take_ids == [
        "sh-010-t2",
        "sh-020-t2",
    ]


def test_project_roundtrips_a_durable_pause_after_current_take() -> None:
    project = demo_project()
    project.production_pause = ProductionPauseReceipt(
        reason="performance_qualification",
        after_take_id="sh-010-t2",
        remaining_take_ids=["sh-020-t2", "sh-030-t2"],
    )

    restored = Project.model_validate(project.model_dump(mode="json"))

    assert restored.production_pause is not None
    assert restored.production_pause.after_take_id == "sh-010-t2"
    assert restored.production_pause.remaining_take_ids == ["sh-020-t2", "sh-030-t2"]


def test_project_roundtrips_infrastructure_recovery_without_new_take_ids() -> None:
    from diony_contracts import ProductionInfrastructureRecovery, ProductionStopReceipt
    from diony_contracts.enums import ProductionStopDisposition

    project = demo_project()
    stop = ProductionStopReceipt(disposition=ProductionStopDisposition.DELETE)
    project.production_recoveries.append(
        ProductionInfrastructureRecovery(
            reason="gpu_service_restart",
            interrupted_stop=stop,
            resumed_take_ids=["sh-010-t1", "sh-020-t1"],
        )
    )

    restored = Project.model_validate_json(project.model_dump_json())
    assert restored.production_recoveries[0].reason == "gpu_service_restart"
    assert restored.production_recoveries[0].resumed_take_ids == ["sh-010-t1", "sh-020-t1"]


def test_demo_fixture_valid() -> None:
    project = demo_project()
    assert project.bible.locked is True
    assert project.shot("sh-010").finish.value == "deliverable"
    assert len(project.takes_for("sh-010")) == 2
    assert project.compute.path.value == "local"
    assert project.compute.profile == "spark-4x-v1"


def test_audio_assets_are_contract_owned() -> None:
    assert AssetKind.AUDIO.value == "audio"


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


def test_b200_site_needs_a_human() -> None:
    from diony_contracts.enums import ComputePath
    from diony_contracts.models import ClusterSite
    from pydantic import ValidationError

    try:
        ClusterSite(path=ComputePath.B200, profile="b200-v1")
        raise AssertionError("expected failure")
    except ValidationError:
        pass
    try:
        ClusterSite(
            path=ComputePath.B200,
            profile="b300-v1",
            exception_by="Rohn Springfield",
        )
        raise AssertionError("expected failure")
    except ValidationError:
        pass
    site = ClusterSite(
        path=ComputePath.B200,
        profile="b200-v1",
        provider="this rack",
        exception_by="Rohn Springfield",
    )
    assert site.path == ComputePath.B200
    assert site.gpu_class == "1×B200"
    try:
        ClusterSite(path=ComputePath.LOCAL, profile="b200-v1")
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
