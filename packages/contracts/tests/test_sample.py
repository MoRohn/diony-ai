import pytest

from diony_contracts.enums import CustomerKind, ProductionKind
from diony_contracts.models import Asset, Brief
from diony_contracts.stitch import stitch_events
from diony_contracts.studio import expand_idea


def test_sample_brief_owns_an_exact_five_to_ten_second_timeframe() -> None:
    five = Brief(
        idea="A glassblower shapes a tiny sun before dawn.",
        customer_kind=CustomerKind.FEATURE,
        production_kind=ProductionKind.SAMPLE,
        sample_seconds=5,
    )
    ten = Brief(
        idea="A glassblower shapes a tiny sun before dawn.",
        customer_kind=CustomerKind.FEATURE,
        production_kind=ProductionKind.SAMPLE,
        sample_seconds=10,
    )

    assert five.target_minutes == pytest.approx(5 / 60)
    assert ten.target_minutes == pytest.approx(10 / 60)
    with pytest.raises(ValueError, match="between 5 and 10 seconds"):
        Brief(
            idea="A glassblower shapes a tiny sun before dawn.",
            customer_kind=CustomerKind.FEATURE,
            production_kind=ProductionKind.SAMPLE,
            sample_seconds=4,
        )
    with pytest.raises(ValueError, match="between 5 and 10 seconds"):
        Brief(
            idea="A glassblower shapes a tiny sun before dawn.",
            customer_kind=CustomerKind.FEATURE,
            production_kind=ProductionKind.SAMPLE,
            sample_seconds=11,
        )


def test_program_brief_does_not_accept_a_sample_only_length() -> None:
    with pytest.raises(ValueError, match="program target_minutes"):
        Brief(
            idea="A glassblower shapes a tiny sun before dawn.",
            customer_kind=CustomerKind.FEATURE,
            target_minutes=10 / 60,
        )


def test_sample_expansion_uses_references_and_hits_the_requested_timeframe() -> None:
    still = Asset(
        id="ast-sample-subject",
        kind="image",
        filename="glassblower.png",
        mime="image/png",
        bytes=128,
        path="assets/ast-sample-subject/glassblower.png",
        sha256="sample-reference-sha",
    )
    brief = Brief(
        idea="Animate this image as a glassblower shaping a tiny sun.",
        customer_kind=CustomerKind.FEATURE,
        production_kind=ProductionKind.SAMPLE,
        sample_seconds=7,
        attachment_ids=[still.id],
    )

    project = expand_idea(brief, assets=[still])
    _, total_frames = stitch_events(project)

    assert project.brief is not None
    assert project.brief.production_kind == ProductionKind.SAMPLE
    assert len(project.shots) == 2
    assert total_frames / project.bible.fps == pytest.approx(7, abs=0.06)
    assert all(shot.duration_frames / shot.fps <= 5.1 for shot in project.shots)
    assert project.shots[0].continuity_mode == "reference"
    assert project.shots[1].continuity_mode == "previous_frame"
    assert project.shots[1].overlap_frames == 8
    assert all(shot.keyframe_asset_ids == [still.id] for shot in project.shots)
    assert all("sample" in shot.prompt.casefold() for shot in project.shots)
