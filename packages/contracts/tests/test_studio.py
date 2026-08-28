from diony_contracts.enums import (
    AssetKind,
    CameraMove,
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
    # 5 minutes of TRT on the honest 81-frame lane with 8-frame stitch handles.
    assert len(project.shots) == 99
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
    assert len(short.shots) == 10
    mid = expand_idea(
        Brief(
            idea="A courier delivers a letter that rewrites the city map.",
            customer_kind=CustomerKind.FEATURE,
            target_minutes=10,
        )
    )
    assert len(mid.shots) == 120
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
    # Lane-true bookings stitch to the goal exactly, not approximately.
    assert five_total == round(5 * 60 * five.bible.fps)
    assert abs(five_trt - 300) < 0.1
    assert all(17 <= s.duration_frames <= 81 for s in five.shots)


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
    # A spoken Shot is a declared cut to its speaker (reference still, no
    # overlap, close-up focal) and the next Shot cuts back the same way; the
    # rest of the sequence chains from the previous final frame.
    spoken_indexes = {i for i, shot in enumerate(project.shots) if shot.audio_line}
    cut_indexes = spoken_indexes | {i + 1 for i in spoken_indexes}
    for i, shot in enumerate(project.shots[1:], start=1):
        if i in cut_indexes:
            assert shot.continuity_mode == ContinuityMode.REFERENCE and shot.overlap_frames == 0
        else:
            assert shot.continuity_mode == ContinuityMode.PREVIOUS_FRAME
    assert all(project.shots[i].focal_mm >= 50 for i in spoken_indexes)
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
    spoken_indexes = {i for i, shot in enumerate(project.shots) if shot.audio_line}
    cut_indexes = spoken_indexes | {i + 1 for i in spoken_indexes}
    assert all(
        shot.continuity_mode == ("reference" if i in cut_indexes else "previous_frame")
        for i, shot in enumerate(project.shots)
        if i > 0
    )
    assert all(shot.engine != Engine.S2V_14B for shot in project.shots)
    # The narrated opener is a spoken performance; the silent closer keeps its beat.
    assert project.shots[0].audio_line
    assert "speaks the line" in project.shots[0].prompt_plan.motion.casefold()
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
    assert project.shots[0].audio_line
    assert "speaks the line" in project.shots[0].prompt_plan.motion.casefold()
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
    assert all(17 <= shot.duration_frames <= 81 for shot in project.shots)
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

    assert len(project.shots) == 4
    assert all(shot.engine == Engine.I2V_A14B for shot in project.shots)
    assert all(shot.keyframe_asset_ids == [still.id] for shot in project.shots)
    assert all(shot.reference_lock == "character_and_background" for shot in project.shots)
    assert project.shots[0].continuity_mode == "reference"
    assert all(shot.continuity_mode == "previous_frame" for shot in project.shots[1:])
    assert all(shot.overlap_frames == 8 for shot in project.shots[1:])
    assert all("no presenter" in shot.prompt.casefold() for shot in project.shots)
    assert all("picture-in-picture" in shot.negative_prompt.casefold() for shot in project.shots)
    assert all(shot.camera_move == "static" for shot in project.shots)
    assert len({shot.focal_mm for shot in project.shots}) == 1
    assert all("television coverage" not in shot.prompt.casefold() for shot in project.shots)
    assert "previous final frame" in project.shots[1].prompt.casefold()
    assert all("title card" in shot.negative_prompt.casefold() for shot in project.shots)
    assert all("cartoon" not in shot.negative_prompt.casefold() for shot in project.shots)


def test_sample_respects_explicit_camera_and_lighting_direction() -> None:
    still = Asset(
        id="ast-heroine",
        kind=AssetKind.IMAGE,
        filename="heroine.png",
        mime="image/png",
        bytes=12,
        path="assets/ast-heroine/heroine.png",
    )
    project = expand_idea(
        Brief(
            idea=(
                "Exact attached heroine in the moonlit grove. Continuous 50mm close-up. "
                "She speaks while teal filaments pulse. "
                "Slow dolly-in; T2.8; cool key; warm rim. Narration: Every frame begins with light."
            ),
            customer_kind=CustomerKind.FEATURE,
            target_minutes=5 / 60,
            production_kind="sample",
            sample_seconds=5,
            attachment_ids=[still.id],
        ),
        assets=[still],
    )

    shot = project.shots[0]
    assert shot.focal_mm == 50
    assert shot.t_stop == 2.8
    assert shot.camera_move == CameraMove.DOLLY_IN
    assert shot.lighting_ratio == "cool key, warm rim"
    assert shot.prompt_plan.camera == "close-up, 50mm lens at T2.8, slow steady push-in"
    assert "wide shot" not in shot.prompt
    assert "static camera" not in shot.prompt


def test_dispatch_refresh_upgrades_a_prefixed_sample_camera_contract() -> None:
    still = Asset(
        id="ast-heroine",
        kind=AssetKind.IMAGE,
        filename="heroine.png",
        mime="image/png",
        bytes=12,
        path="assets/ast-heroine/heroine.png",
    )
    project = expand_idea(
        Brief(
            idea=(
                "Exact attached heroine. Continuous 50mm close-up. Slow dolly-in; "
                "T2.8; cool key; warm rim."
            ),
            customer_kind=CustomerKind.FEATURE,
            target_minutes=5 / 60,
            production_kind="sample",
            sample_seconds=5,
            attachment_ids=[still.id],
        ),
        assets=[still],
    )
    shot = project.shots[0]
    shot.focal_mm = 24
    shot.t_stop = 4
    shot.camera_move = CameraMove.STATIC
    shot.lighting_ratio = "open sky or wide practical, even"

    refresh_reference_shot(project, shot)

    assert shot.focal_mm == 50
    assert shot.t_stop == 2.8
    assert shot.camera_move == CameraMove.DOLLY_IN
    assert shot.lighting_ratio == "cool key, warm rim"
    assert shot.prompt_plan.camera == "close-up, 50mm lens at T2.8, slow steady push-in"


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

    for shot in project.shots:
        refresh_reference_shot(project, shot)

    assert all(shot.engine == Engine.I2V_A14B for shot in project.shots)
    assert all(shot.reference_lock == ReferenceLock.CHARACTER_AND_BACKGROUND for shot in project.shots)
    assert all(shot.character_ref_ids == ["char-lead"] for shot in project.shots)
    assert all(shot.keyframe_asset_ids == [still.id] for shot in project.shots)
    assert project.shots[0].continuity_mode == ContinuityMode.REFERENCE
    assert project.shots[1].continuity_mode == ContinuityMode.PREVIOUS_FRAME
    # The upgrade never inflates a booking past camera truth; the persisted
    # duration stands and AutoQC reconciles any remaining fiction to measured frames.
    assert project.shots[1].duration_frames == 73
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


def test_lane_durations_are_lane_true_and_stitch_to_the_exact_target() -> None:
    from diony_contracts.studio import (
        STITCH_OVERLAP_FRAMES,
        WAN_LANE_FRAMES,
        WAN_MIN_SHOT_FRAMES,
        _lane_durations,
    )

    for target in (17, 47, 81, 82, 120, 168, 240, 719, 7193):
        durations = _lane_durations(target)
        assert all(WAN_MIN_SHOT_FRAMES <= d <= WAN_LANE_FRAMES for d in durations)
        stitched = sum(durations) - STITCH_OVERLAP_FRAMES * (len(durations) - 1)
        assert stitched == max(WAN_MIN_SHOT_FRAMES, target)


def test_program_durations_are_monotonic_in_the_length_goal() -> None:
    from diony_contracts.studio import STITCH_OVERLAP_FRAMES, _program_durations

    def trt(minutes: float) -> int:
        durations = _program_durations(minutes)
        return sum(durations) - STITCH_OVERLAP_FRAMES * (len(durations) - 1)

    goals = [0.5, 1.0, 2.0, 3.0, 5.0]
    stitched = [trt(m) for m in goals]
    assert stitched == sorted(stitched)
    # A longer goal inside the quality window never yields a shorter program.
    assert trt(3.0) > trt(1.0)


def test_refresh_preserves_a_repaired_current_plan() -> None:
    """A QC repair applied after the contract refresh must survive re-dispatch."""
    from diony_contracts.wan_prompt import repair_wan_prompt_for_qc

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
    shot = project.shots[1]
    refresh_reference_shot(project, shot)
    assert repair_wan_prompt_for_qc(
        shot, "Measured motion failed (delta p95 0.0002); the plate is effectively frozen."
    )
    repaired_motion = shot.prompt_plan.motion
    repaired_prompt = shot.prompt
    assert "mid-clip freeze" in shot.negative_prompt

    refresh_reference_shot(project, shot)

    assert shot.prompt_plan.motion == repaired_motion
    assert shot.prompt == repaired_prompt
    assert "mid-clip freeze" in shot.negative_prompt


def test_spoken_shots_are_declared_cuts_to_the_speaker_and_back() -> None:
    from diony_contracts.enums import ContinuityMode
    from diony_contracts.fixture import demo_project
    from diony_contracts.studio import SPOKEN_FOCAL_MM, apply_spoken_framing, shot_speaks

    project = demo_project()
    shots = project.shots
    assert len(shots) >= 2
    spoken_index = next(i for i, shot in enumerate(shots) if shot_speaks(shot))
    spoken = shots[spoken_index]
    spoken.continuity_mode = ContinuityMode.PREVIOUS_FRAME
    spoken.overlap_frames = 8
    spoken.focal_mm = 24
    for index, shot in enumerate(shots):
        apply_spoken_framing(project, shot, index)
    assert spoken.continuity_mode == ContinuityMode.REFERENCE
    assert spoken.overlap_frames == 0
    assert spoken.focal_mm == SPOKEN_FOCAL_MM
    if spoken_index + 1 < len(shots):
        following = shots[spoken_index + 1]
        assert following.continuity_mode == ContinuityMode.REFERENCE
        assert following.overlap_frames == 0


def test_spoken_shots_install_the_spoken_plan_and_persisted_plans_upgrade() -> None:
    from diony_contracts.studio import refresh_reference_shot, shot_speaks, spoken_plan_applied
    from diony_contracts.wan_prompt import (
        SPOKEN_AUDIO_STRATEGY,
        render_wan_prompt,
        validate_wan_prompt_dispatch,
    )

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
    # The binding upgrade re-seats the narration; direct whoever speaks after it.
    from diony_contracts.studio import _upgrade_binding_reference_contract

    _upgrade_binding_reference_contract(project)
    spoken = next(shot for shot in project.shots if shot_speaks(shot))
    refresh_reference_shot(project, spoken)
    assert spoken.prompt_plan is not None
    assert spoken.prompt_plan.audio_strategy == SPOKEN_AUDIO_STRATEGY
    assert spoken_plan_applied(spoken)
    assert validate_wan_prompt_dispatch(spoken) == []
    assert "speaks the line straight toward camera" in spoken.prompt
    assert "static camera with no movement" in spoken.prompt
    assert spoken.prompt == render_wan_prompt(spoken.prompt_plan)
    # Refreshing again keeps the applied plan (a retake directive would survive).
    before = spoken.prompt
    refresh_reference_shot(project, spoken)
    assert spoken.prompt == before

    # A persisted pre-spoken plan (the five-layer beat) is upgraded on the next refresh.
    spoken.prompt_plan = spoken.prompt_plan.model_copy(update={"audio_strategy": "native_s2v"})
    spoken.prompt = render_wan_prompt(spoken.prompt_plan)
    assert not spoken_plan_applied(spoken)
    refresh_reference_shot(project, spoken)
    assert spoken.prompt_plan.audio_strategy == SPOKEN_AUDIO_STRATEGY
    assert validate_wan_prompt_dispatch(spoken) == []

    # Silent Shots keep the five-layer beat, and a Shot whose line moved away
    # sheds the stale performance direction on its next refresh.
    silent = next(shot for shot in project.shots if not shot_speaks(shot))
    silent.prompt_plan = spoken.prompt_plan.model_copy()
    silent.prompt = render_wan_prompt(silent.prompt_plan)
    assert not spoken_plan_applied(silent)
    refresh_reference_shot(project, silent)
    assert silent.prompt_plan is not None
    assert silent.prompt_plan.audio_strategy != SPOKEN_AUDIO_STRATEGY
    assert "speaks the line" not in silent.prompt
    assert validate_wan_prompt_dispatch(silent) == []


def test_dialogue_windows_are_balanced_and_never_leave_orphan_fragments() -> None:
    from diony_contracts.studio import _balanced_windows, _fit_dialogue_to_shots
    from diony_contracts.studio import ScriptDialogue

    text = (
        "We are starting a daily gratitude circle. I'll go first. I'm grateful for hustle, "
        "vision, and the fact that I haven't given up on you two."
    )
    words = text.split()
    assert len(words) == 26
    windows = _balanced_windows(words, 10)
    sizes = [end - start for start, end in windows]
    assert sum(sizes) == 26 and all(size <= 10 for size in sizes)
    assert min(sizes) >= 7, sizes  # the old chop produced 10 / 10 / 6; never a 2-word tail
    # A boundary landed on a clause end when one was within reach.
    assert any(words[end - 1].endswith((".", ",")) for _, end in windows[:-1])

    fitted = _fit_dialogue_to_shots(
        [ScriptDialogue(speaker="Marcus", text=text)], shot_count=6, seconds_per_shot=5.0
    )
    turns = [turn.text for window in fitted for turn in window]
    assert all(len(turn.split()) >= 3 for turn in turns), turns
    assert " ".join(turns) == text


def test_dialogue_windows_never_span_a_speaker_change_when_the_rundown_can_hold_it() -> None:
    """One camera setup animates one speaking face; a window with two speakers makes one mouth the other's line."""
    from diony_contracts.studio import ScriptDialogue, _fit_dialogue_to_shots

    lines = [
        ScriptDialogue(speaker="Marcus", text="I'm grateful for hustle and the fact that I haven't given up on you two."),
        ScriptDialogue(speaker="Lila", text="I'm grateful this will be over soon."),
        ScriptDialogue(speaker="Hugo", text="Same."),
    ]
    fitted = _fit_dialogue_to_shots(lines, shot_count=4, seconds_per_shot=5.0)
    assert len(fitted) == 4
    for window in fitted:
        assert len({turn.speaker for turn in window}) == 1, [t.speaker for t in window]
    assert " ".join(turn.text for window in fitted for turn in window) == " ".join(line.text for line in lines)

    # Too few shots for one per speaker: the words still fit, briskly, without refusing.
    packed = _fit_dialogue_to_shots(lines, shot_count=2, seconds_per_shot=5.0)
    assert len(packed) == 2
    assert " ".join(turn.text for window in packed for turn in window) == " ".join(line.text for line in lines)


KITCHEN_IDEA = (
    "In the slightly messy shared kitchen, soft morning light filters through the window. "
    "The three claymation characters are framed in a TV show episode style. Marcus stands in "
    "the centre, holding a small notebook. Lila sits on a stool to his right, arms crossed. "
    "Hugo stands to the left, hands clasped.\n\n"
    "Marcus: \"We are starting a daily gratitude circle. I'll go first.\"\n"
    "Marcus looks at them expectantly, already nodding.\n\n"
    "Lila doesn't uncross her arms.\n"
    "Lila: \"I'm grateful this will be over in under a minute.\"\n"
    "Hugo: \"I am grateful that neither of you can legally force me to continue this.\"\n"
)


def _kitchen_project():
    still = Asset(
        id="ast-kitchen",
        kind=AssetKind.IMAGE,
        filename="Scene1.jpg",
        mime="image/jpeg",
        bytes=12,
        path="assets/ast-kitchen/Scene1.jpg",
    )
    return expand_idea(
        Brief(
            idea=KITCHEN_IDEA,
            customer_kind=CustomerKind.SERIES,
            target_minutes=0.5,
            attachment_ids=[still.id],
        ),
        assets=[still],
    )


def test_dialogue_books_a_close_up_and_a_clean_scene_without_a_reference_lock() -> None:
    """Measured 2026-08-27: a lock-NONE brief shot its dialogue as 'wide shot, 24mm' over a
    close-up keyframe, and every prompt carried 'at them expectantly, already nodding. Lila
    doesn't uncross her arms' as scenery. Speech books the close-up whatever the lock says."""
    from diony_contracts.wan_prompt import is_spoken_plan, validate_wan_prompt_plan

    project = _kitchen_project()
    spoken = [i for i, shot in enumerate(project.shots) if shot.dialogue_turns]
    assert spoken, "the screenplay lines must reach the rundown"
    assert all(project.shots[i].reference_lock == ReferenceLock.NONE for i in spoken)
    for i in spoken:
        shot = project.shots[i]
        assert shot.continuity_mode == ContinuityMode.REFERENCE and shot.overlap_frames == 0
        assert shot.focal_mm >= 50
        assert "close-up" in shot.prompt_plan.camera and f"{shot.focal_mm:g}mm" in shot.prompt_plan.camera
        assert is_spoken_plan(shot.prompt_plan)
        assert "lighting" in shot.prompt_plan.scene
        assert "nodding" not in shot.prompt_plan.scene and "uncross" not in shot.prompt_plan.scene
        assert not validate_wan_prompt_plan(shot.prompt_plan)
        if i + 1 < len(project.shots):
            follower = project.shots[i + 1]
            assert follower.continuity_mode == ContinuityMode.REFERENCE and follower.overlap_frames == 0
            assert follower.keyframe_asset_ids == shot.keyframe_asset_ids, "the cut back has a still to cut back to"
    silent = [shot for shot in project.shots if not shot.dialogue_turns]
    assert all("nodding" not in shot.prompt_plan.scene for shot in silent), "the world summary is a sentence, not a fragment"


def test_refresh_reference_shot_reframes_a_persisted_wide_spoken_shot_on_lock_none() -> None:
    """A Project persisted under the old contract (24mm, previous-frame) retakes as a close-up."""
    from diony_contracts.studio import refresh_reference_shot
    from diony_contracts.wan_prompt import is_spoken_plan

    project = _kitchen_project()
    shot = next(item for item in project.shots if item.dialogue_turns)
    shot.focal_mm = 24.0
    shot.continuity_mode = ContinuityMode.PREVIOUS_FRAME
    shot.overlap_frames = 8
    refresh_reference_shot(project, shot)
    assert shot.focal_mm == 50.0
    assert shot.continuity_mode == ContinuityMode.REFERENCE and shot.overlap_frames == 0
    assert "medium close-up, 50mm" in shot.prompt_plan.camera
    assert is_spoken_plan(shot.prompt_plan)
    assert shot.prompt.startswith(shot.prompt_plan.subject.split(";")[0][:20])
    # A second refresh with nothing changed keeps the plan (a retake directive would survive).
    before = shot.prompt
    refresh_reference_shot(project, shot)
    assert shot.prompt == before


def test_world_summary_is_a_whole_placing_sentence() -> None:
    from diony_contracts.wan_prompt import _environment_from_world

    world = "An episode cold open built from the idea: " + KITCHEN_IDEA
    assert _environment_from_world(world) == (
        "In the slightly messy shared kitchen, soft morning light filters through the window"
    )


def test_dialogue_windows_are_counted_per_speaker() -> None:
    from diony_contracts.studio import ScriptDialogue, _dialogue_windows_needed

    lines = [ScriptDialogue(speaker="Marcus", text="one two three four five six seven"), ScriptDialogue(speaker="Lila", text="eight nine")]
    # 6 words per 3.378 s window: 7 words pooled with 2 would be 2 windows; per speaker it is 3.
    assert _dialogue_windows_needed(lines, 3.378) == 3
    assert _dialogue_windows_needed([], 3.378) == 0
