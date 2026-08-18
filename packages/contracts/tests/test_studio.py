from diony_contracts.enums import AssetKind, ComputePath, CustomerKind, FinishIntent
from diony_contracts.models import Asset, Brief, ClusterSite
from diony_contracts.studio import TARGET_ALLOTMENTS, allotment_label, blank_studio, expand_idea


def test_expand_idea_grammar_on_every_shot() -> None:
    project = expand_idea(
        Brief(
            idea="A courier delivers a letter that rewrites the city map.",
            customer_kind=CustomerKind.FEATURE,
            target_minutes=5,
        )
    )
    assert project.bible.width == 1920
    assert len(project.shots) == 10
    for shot in project.shots:
        assert shot.focal_mm > 0
        assert shot.finish == FinishIntent.DELIVERABLE
        assert "morphing" in shot.negative_prompt


def test_hour_brief_is_first_reel() -> None:
    project = expand_idea(
        Brief(
            idea="A season of a newsroom that prints the future.",
            customer_kind=CustomerKind.SERIES,
            target_minutes=60,
        )
    )
    assert len(project.shots) == 12
    assert any("First reel" in n.body for n in project.notes)


def test_length_allotments_are_goals() -> None:
    labels = [item["label"] for item in TARGET_ALLOTMENTS]
    assert labels == ["30s", "1m", "3m", "5m", "10m", "15m", "20m", "30m", "40m", "1hr"]
    assert allotment_label(0.5) == "30s"
    assert allotment_label(60) == "1hr"
    short = expand_idea(
        Brief(
            idea="A courier delivers a letter that rewrites the city map.",
            customer_kind=CustomerKind.FEATURE,
            target_minutes=0.5,
        )
    )
    assert len(short.shots) == 4
    mid = expand_idea(
        Brief(
            idea="A courier delivers a letter that rewrites the city map.",
            customer_kind=CustomerKind.FEATURE,
            target_minutes=10,
        )
    )
    assert len(mid.shots) == 12
    assert not any("First reel" in n.body for n in mid.notes)


def test_all_customer_kinds_cast() -> None:
    idea = "A baker times bread to a town siren."
    for kind in CustomerKind:
        project = expand_idea(Brief(idea=idea, customer_kind=kind, target_minutes=2))
        assert project.customer_kind == kind
        assert project.shots
        assert project.bible.characters


def test_expand_idea_uses_image_and_document_refs() -> None:
    still = Asset(
        id="ast-still",
        kind=AssetKind.IMAGE,
        filename="mara.png",
        mime="image/png",
        bytes=12,
        path="assets/ast-still/mara.png",
    )
    pages = Asset(
        id="ast-pages",
        kind=AssetKind.DOCUMENT,
        filename="treatment.md",
        mime="text/markdown",
        bytes=40,
        path="assets/ast-pages/treatment.md",
        extracted_text="A typesetter finds tomorrow already printed in lead.",
    )
    project = expand_idea(
        Brief(
            idea="look",
            customer_kind=CustomerKind.FEATURE,
            target_minutes=2,
            attachment_ids=["ast-still", "ast-pages"],
        ),
        assets=[still, pages],
    )
    assert project.assets[0].id == "ast-still"
    assert any(shot.keyframe_asset_ids for shot in project.shots)
    assert "typesetter" in project.bible.logline or "typesetter" in project.bible.world


def test_expand_idea_stamps_dual_compute() -> None:
    site = ClusterSite(
        path=ComputePath.DUAL,
        profile="spark-2x-v1",
        exception_by="Rohn Springfield",
    )
    project = expand_idea(
        Brief(
            idea="A courier delivers a letter that rewrites the city map.",
            customer_kind=CustomerKind.FEATURE,
            target_minutes=2,
        ),
        compute=site,
    )
    assert project.compute.path == ComputePath.DUAL
    assert project.compute.profile == "spark-2x-v1"
    assert project.fleet_mode == "SPLIT"


def test_blank_studio_is_untitled() -> None:
    project = blank_studio()
    assert project.id == "studio-blank"
    assert project.shots == []
    assert project.bible.title == "Untitled"
