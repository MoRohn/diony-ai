from diony_contracts.enums import (
    AssetKind,
    ComputePath,
    ContinuityMode,
    CustomerKind,
    Engine,
    FinishIntent,
    ReferenceLock,
)
from diony_contracts.models import Asset, Brief, ClusterSite
from diony_contracts.stitch import stitch_events
from diony_contracts.studio import (
    TARGET_ALLOTMENTS,
    allotment_label,
    blank_studio,
    expand_idea,
    refresh_reference_shot,
)


def test_expand_idea_grammar_on_every_shot() -> None:
    project = expand_idea(
        Brief(
            idea="A courier delivers a letter that rewrites the city map.",
            customer_kind=CustomerKind.FEATURE,
            target_minutes=5,
        )
    )
    assert project.bible.width == 1920
    assert len(project.shots) == 60
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
    assert len(short.shots) == 6
    mid = expand_idea(
        Brief(
            idea="A courier delivers a letter that rewrites the city map.",
            customer_kind=CustomerKind.FEATURE,
            target_minutes=10,
        )
    )
    assert len(mid.shots) == 60
    assert any("quality-lane" in n.body for n in mid.notes)


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


def test_short_goal_stitched_trt_is_near_the_picker() -> None:
    """5s Wan quality lane × shot count, minus overlap. Not wall-clock."""
    short = expand_idea(
        Brief(
            idea="A courier delivers a letter that rewrites the city map.",
            customer_kind=CustomerKind.FEATURE,
            target_minutes=0.5,
        )
    )
    _, total = stitch_events(short)
    trt = total / short.bible.fps
    assert 24 <= trt <= 36
    five = expand_idea(
        Brief(
            idea="A courier delivers a letter that rewrites the city map.",
            customer_kind=CustomerKind.FEATURE,
            target_minutes=5,
        )
    )
    _, five_total = stitch_events(five)
    five_trt = five_total / five.bible.fps
    assert 240 <= five_trt <= 320
    assert all(abs(s.duration_frames / s.fps - 5) < 0.2 for s in five.shots)


def test_visual_only_idea_does_not_invent_dialogue_or_s2v() -> None:
    project = expand_idea(
        Brief(
            idea=(
                "Illustrate this character from image, with no background, "
                "animated thinking, being creative, and directing."
            ),
            customer_kind=CustomerKind.SERIES,
            target_minutes=0.5,
        )
    )

    assert all(shot.audio_line is None for shot in project.shots)
    assert all(shot.engine != Engine.S2V_14B for shot in project.shots)


def test_subject_defining_image_conditions_every_shot() -> None:
    still = Asset(
        id="ast-character",
        kind=AssetKind.IMAGE,
        filename="character.png",
        mime="image/png",
        bytes=12,
        path="assets/ast-character/character.png",
    )
    project = expand_idea(
        Brief(
            idea=(
                "Illustrate this character from image, with no background, "
                "animated thinking, being creative, and directing."
            ),
            customer_kind=CustomerKind.SERIES,
            target_minutes=0.5,
            attachment_ids=[still.id],
        ),
        assets=[still],
    )

    assert all(shot.engine == Engine.I2V_A14B for shot in project.shots)
    assert all(shot.keyframe_asset_ids == [still.id] for shot in project.shots)
    assert all(shot.character_ref_ids == ["char-lead"] for shot in project.shots)


def test_attached_character_language_binds_reference_continuity_and_spoken_copy() -> None:
    still = Asset(
        id="ast-dionysius",
        kind=AssetKind.IMAGE,
        filename="Dionysius.png",
        mime="image/png",
        bytes=48,
        path="assets/ast-dionysius/Dionysius.png",
    )
    project = expand_idea(
        Brief(
            idea=(
                "Dionysius, the attached character welcomes people to Diony AI. "
                "Explains the product and uses motto, 'Own your Studio today, and produce at cost'."
            ),
            customer_kind=CustomerKind.SERIES,
            target_minutes=0.5,
            attachment_ids=[still.id],
        ),
        assets=[still],
    )

    assert all(shot.engine == Engine.I2V_A14B for shot in project.shots)
    assert all(shot.keyframe_asset_ids == [still.id] for shot in project.shots)
    assert all(shot.reference_lock == ReferenceLock.CHARACTER for shot in project.shots)
    assert project.shots[0].continuity_mode == ContinuityMode.REFERENCE
    assert all(
        shot.continuity_mode == ContinuityMode.PREVIOUS_FRAME
        for shot in project.shots[1:]
    )
    spoken = [shot.audio_line for shot in project.shots if shot.audio_line]
    assert spoken == [
        "Welcome to Diony AI. Own your Studio today, and produce at cost."
    ]


def test_exact_last_attached_illustration_drives_scripted_sequence_through_i2v() -> None:
    """Regression: an exact named character must not become an unconditioned coverage reel."""
    screens = [
        Asset(
            id=f"ast-screen-{index}",
            kind=AssetKind.IMAGE,
            filename=f"Studio{index}.png",
            mime="image/png",
            bytes=12,
            path=f"assets/ast-screen-{index}/Studio{index}.png",
        )
        for index in range(1, 6)
    ]
    character = Asset(
        id="ast-dionysius",
        kind=AssetKind.IMAGE,
        filename="Dionysius.png",
        mime="image/png",
        bytes=12,
        path="assets/ast-dionysius/Dionysius.png",
    )
    assets = [*screens, character]
    project = expand_idea(
        Brief(
            idea=(
                "Create a cinematic demo guided by the classical Dionysus character "
                "(the exact illustration provided). Dionysus is illustrated in the last "
                "attached image and appears as a living animated character.\n\n"
                "Scene 1 — Cold Open (0–8s)\n"
                "Dionysus walks into frame from the right and settles into a hold.\n"
                "Narration:\n\"Idea in. Show out.\"\n\n"
                "Scene 2 — Dual Choice Reveal (8–30s)\n"
                "Dionysus raises the grapes slowly, then turns toward the viewer.\n"
                "Narration:\n\"Own the source.\""
            ),
            customer_kind=CustomerKind.SERIES,
            target_minutes=0.5,
            attachment_ids=[asset.id for asset in assets],
        ),
        assets=assets,
    )

    assert all(shot.engine == Engine.I2V_A14B for shot in project.shots)
    assert all(shot.reference_lock == "character" for shot in project.shots)
    assert all(shot.keyframe_asset_ids == [character.id] for shot in project.shots)
    assert project.shots[0].continuity_mode == "reference"
    assert all(shot.continuity_mode == "previous_frame" for shot in project.shots[1:])
    assert all(shot.engine != Engine.S2V_14B for shot in project.shots)
    assert "walks" in project.shots[0].prompt_plan.motion.casefold()
    assert "raises" in project.shots[-1].prompt_plan.motion.casefold()
    assert any(shot.audio_line == "Idea in. Show out." for shot in project.shots)
    assert any(shot.audio_line == "Own the source." for shot in project.shots)


def test_retake_upgrade_keeps_the_explicit_last_attached_subject() -> None:
    screens = [
        Asset(
            id=f"ast-screen-{index}",
            kind=AssetKind.IMAGE,
            filename=f"Studio{index}.png",
            mime="image/png",
            bytes=12,
            path=f"assets/ast-screen-{index}/Studio{index}.png",
        )
        for index in range(1, 3)
    ]
    character = Asset(
        id="ast-dionysius",
        kind=AssetKind.IMAGE,
        filename="Dionysius.png",
        mime="image/png",
        bytes=12,
        path="assets/ast-dionysius/Dionysius.png",
    )
    assets = [*screens, character]
    project = expand_idea(
        Brief(
            idea=(
                "Use the exact Dionysus character in the last attached image.\n"
                "Scene 1 — Entrance (0–10s)\n"
                "Dionysus walks into the real Diony studio.\n"
                "Narration: The studio is open.\n"
                "Scene 2 — Closing (10–30s)\n"
                "Dionysus raises the grapes and turns toward the viewer.\n"
                "Narration: Own the source."
            ),
            customer_kind=CustomerKind.SERIES,
            target_minutes=0.5,
            attachment_ids=[asset.id for asset in assets],
        ),
        assets=assets,
    )
    for shot in project.shots:
        shot.reference_lock = ReferenceLock.NONE
        shot.continuity_mode = ContinuityMode.INDEPENDENT
        shot.character_ref_ids = []
        shot.keyframe_asset_ids = []

    for shot in project.shots:
        refresh_reference_shot(project, shot)

    assert all(shot.keyframe_asset_ids == [character.id] for shot in project.shots)
    assert project.bible.characters[0].still_asset_ids == [character.id]
    assert "walks" in project.shots[0].prompt_plan.motion.casefold()
    assert "raises" in project.shots[-1].prompt_plan.motion.casefold()
    assert any(shot.audio_line == "The studio is open." for shot in project.shots)
    assert any(shot.audio_line == "Own the source." for shot in project.shots)


def test_narration_without_a_recorded_voice_asset_does_not_schedule_s2v() -> None:
    project = expand_idea(
        Brief(
            idea=(
                "A precise product demonstration moves through three interface views. "
                "Narration: Choose the signed production system, then open Producer."
            ),
            customer_kind=CustomerKind.COMMERCIAL,
            target_minutes=0.5,
        )
    )

    assert all(shot.engine != Engine.S2V_14B for shot in project.shots)


def test_recorded_performance_audio_routes_the_performance_shot_to_s2v() -> None:
    voice = Asset(
        id="ast-performance-audio",
        kind=AssetKind.AUDIO,
        filename="performance.wav",
        mime="audio/wav",
        bytes=48,
        path="assets/ast-performance-audio/performance.wav",
    )
    still = Asset(
        id="ast-performer",
        kind=AssetKind.IMAGE,
        filename="performer.png",
        mime="image/png",
        bytes=48,
        path="assets/ast-performer/performer.png",
    )
    project = expand_idea(
        Brief(
            idea="Use this performer image. She sings the attached recorded chorus to camera.",
            customer_kind=CustomerKind.MUSIC_VIDEO,
            target_minutes=0.5,
            attachment_ids=[still.id, voice.id],
        ),
        assets=[still, voice],
    )

    performance = [shot for shot in project.shots if shot.engine == Engine.S2V_14B]
    assert performance
    assert all(shot.audio_asset_ids == [voice.id] for shot in performance)
    assert all(shot.keyframe_asset_ids == [still.id] for shot in performance)


def test_driving_video_and_character_still_route_shots_to_animate() -> None:
    still = Asset(
        id="ast-performer",
        kind=AssetKind.IMAGE,
        filename="performer.png",
        mime="image/png",
        bytes=48,
        path="assets/ast-performer/performer.png",
    )
    driving = Asset(
        id="ast-driving",
        kind=AssetKind.VIDEO,
        filename="driving.mp4",
        mime="video/mp4",
        bytes=96,
        path="assets/ast-driving/driving.mp4",
        metadata={"wan_animate_src_root": "/diony/vault/animate/ast-driving"},
    )
    project = expand_idea(
        Brief(
            idea="Use the exact performer image and attached driving performance for her motion.",
            customer_kind=CustomerKind.MUSIC_VIDEO,
            target_minutes=0.5,
            attachment_ids=[still.id, driving.id],
        ),
        assets=[still, driving],
    )

    assert all(shot.engine == Engine.ANIMATE_14B for shot in project.shots)
    assert all(shot.keyframe_asset_ids == [still.id] for shot in project.shots)
    assert all(shot.motion_reference_asset_ids == [driving.id] for shot in project.shots)
    assert all(shot.prompt_plan.generation_method == Engine.ANIMATE_14B.value for shot in project.shots)


def test_long_form_without_a_starting_still_chains_bounded_i2v_segments() -> None:
    project = expand_idea(
        Brief(
            idea=(
                "A courier in a navy raincoat crosses the station, checks the brass clock, "
                "then runs toward the departing train."
            ),
            customer_kind=CustomerKind.FEATURE,
            target_minutes=0.5,
        )
    )

    assert project.shots[0].engine == Engine.T2V_A14B
    assert project.shots[0].continuity_mode == ContinuityMode.INDEPENDENT
    assert all(shot.engine == Engine.I2V_A14B for shot in project.shots[1:])
    assert all(shot.continuity_mode == ContinuityMode.PREVIOUS_FRAME for shot in project.shots[1:])
    assert all(4 <= shot.duration_frames / shot.fps <= 7 for shot in project.shots)
    assert all("previous final frame" in shot.prompt.casefold() for shot in project.shots[1:])


def test_attached_character_sample_locks_every_shot_and_boundary() -> None:
    """Regression: the first Sample clip must not silently become an unrelated T2V shot."""
    still = Asset(
        id="ast-character",
        kind=AssetKind.IMAGE,
        filename="New.png",
        mime="image/png",
        bytes=12,
        path="assets/ast-character/New.png",
    )
    project = expand_idea(
        Brief(
            idea=(
                "Using this attached character and same background. Illustrate the character "
                "as if they are brainstorming, being creative, producing, and directing a scene."
            ),
            customer_kind=CustomerKind.SERIES,
            target_minutes=10 / 60,
            production_kind="sample",
            sample_seconds=10,
            attachment_ids=[still.id],
        ),
        assets=[still],
    )

    assert len(project.shots) == 2
    assert all(shot.engine == Engine.I2V_A14B for shot in project.shots)
    assert all(shot.keyframe_asset_ids == [still.id] for shot in project.shots)
    assert all(shot.reference_lock == "character_and_background" for shot in project.shots)
    assert project.shots[0].continuity_mode == "reference"
    assert project.shots[1].continuity_mode == "previous_frame"
    assert project.shots[1].overlap_frames == 8
    assert all("no presenter" in shot.prompt.casefold() for shot in project.shots)
    assert all("picture-in-picture" in shot.negative_prompt.casefold() for shot in project.shots)
    assert all(shot.camera_move == "static" for shot in project.shots)
    assert len({shot.focal_mm for shot in project.shots}) == 1
    assert all("television coverage" not in shot.prompt.casefold() for shot in project.shots)
    assert "previous final frame" in project.shots[1].prompt.casefold()
    assert all("title card" in shot.negative_prompt.casefold() for shot in project.shots)
    assert all("cartoon" not in shot.negative_prompt.casefold() for shot in project.shots)


def test_attached_team_of_characters_is_a_binding_reference() -> None:
    still = Asset(
        id="ast-team",
        kind=AssetKind.IMAGE,
        filename="Team.png",
        mime="image/png",
        bytes=12,
        path="assets/ast-team/Team.png",
    )
    project = expand_idea(
        Brief(
            idea=(
                "Using this attached team of characters and same background. Illustrate the "
                "characters collaborating on brainstorming and directing a scene."
            ),
            customer_kind=CustomerKind.SERIES,
            target_minutes=10 / 60,
            production_kind="sample",
            sample_seconds=10,
            attachment_ids=[still.id],
        ),
        assets=[still],
    )

    assert all(shot.engine == Engine.I2V_A14B for shot in project.shots)
    assert all(shot.reference_lock == "character_and_background" for shot in project.shots)
    assert all(shot.keyframe_asset_ids == [still.id] for shot in project.shots)


def test_retake_upgrades_a_pre_fix_plural_reference_project() -> None:
    """A Gallery failure created before the plural detector must be repairable in place."""
    still = Asset(
        id="ast-team",
        kind=AssetKind.IMAGE,
        filename="Team.png",
        mime="image/png",
        bytes=12,
        path="assets/ast-team/Team.png",
    )
    project = expand_idea(
        Brief(
            idea=(
                "Using this attached team of characters and same background. Illustrate the "
                "characters collaborating on brainstorming and directing a scene."
            ),
            customer_kind=CustomerKind.SERIES,
            target_minutes=10 / 60,
            production_kind="sample",
            sample_seconds=10,
            attachment_ids=[still.id],
        ),
        assets=[still],
    )
    # Reproduce the persisted contract from the failed pre-upgrade run.
    project.shots[0].engine = Engine.T2V_A14B
    for index, shot in enumerate(project.shots):
        shot.reference_lock = ReferenceLock.NONE
        shot.continuity_mode = ContinuityMode.INDEPENDENT
        shot.overlap_frames = 0
        shot.negative_prompt = "morphing face, identity drift"
        if index == 0:
            shot.character_ref_ids = []
            shot.keyframe_asset_ids = []
        else:
            shot.duration_frames -= 8

    refresh_reference_shot(project, project.shots[0])
    refresh_reference_shot(project, project.shots[1])

    assert all(shot.engine == Engine.I2V_A14B for shot in project.shots)
    assert all(shot.reference_lock == ReferenceLock.CHARACTER_AND_BACKGROUND for shot in project.shots)
    assert all(shot.character_ref_ids == ["char-lead"] for shot in project.shots)
    assert all(shot.keyframe_asset_ids == [still.id] for shot in project.shots)
    assert project.shots[0].continuity_mode == ContinuityMode.REFERENCE
    assert project.shots[1].continuity_mode == ContinuityMode.PREVIOUS_FRAME
    assert project.shots[1].duration_frames == 128
    assert all(shot.overlap_frames == 8 for shot in project.shots)
    assert all("picture-in-picture" in shot.negative_prompt for shot in project.shots)
    assert all("cartoon" not in shot.negative_prompt.casefold() for shot in project.shots)


def test_identical_briefs_receive_full_strength_unique_run_namespaces() -> None:
    brief = Brief(
        idea="A courier delivers a letter that rewrites the city map.",
        customer_kind=CustomerKind.FEATURE,
        target_minutes=0.5,
    )

    first = expand_idea(brief)
    second = expand_idea(brief)

    assert first.id != second.id
    assert len(first.id.rsplit("-", 1)[-1]) == 32
    assert len(second.id.rsplit("-", 1)[-1]) == 32
