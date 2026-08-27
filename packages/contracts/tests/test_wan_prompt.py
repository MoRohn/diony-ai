from diony_contracts.enums import AssetKind, CustomerKind
from diony_contracts.models import Asset, Brief
from diony_contracts.studio import expand_idea
from diony_contracts.wan_prompt import (
    WAN22_PROMPT_STRATEGY_VERSION,
    prompt_receipt_sha256,
    repair_wan_prompt_plan,
    render_wan_prompt,
    validate_wan_prompt_dispatch,
    validate_wan_prompt_plan,
)


def test_idea_compiles_every_shot_into_the_wan22_four_layer_contract() -> None:
    project = expand_idea(
        Brief(
            idea=(
                "A courier with a weathered canvas satchel delivers a letter that "
                "rewrites the city map during a rain-soaked blue hour."
            ),
            customer_kind=CustomerKind.FEATURE,
            target_minutes=0.5,
        )
    )

    assert len({shot.prompt_plan.subject for shot in project.shots}) == 1
    for shot in project.shots:
        plan = shot.prompt_plan
        assert plan is not None
        assert plan.strategy_version == WAN22_PROMPT_STRATEGY_VERSION
        assert plan.word_count == len(shot.prompt.split())
        assert 80 <= plan.word_count <= 140
        assert plan.diagnostics == []
        assert 2 <= len(plan.identity_anchors) <= 4
        assert plan.high_noise_plan
        assert plan.low_noise_plan
        assert plan.environment_dynamics
        assert shot.prompt == render_wan_prompt(plan)
        assert shot.prompt.index(plan.subject) < shot.prompt.index(plan.motion)
        assert shot.prompt.index(plan.motion) < shot.prompt.index(plan.camera)
        assert shot.prompt.index(plan.camera) < shot.prompt.index(plan.scene)
        assert shot.prompt.index(plan.scene) < shot.prompt.index(plan.environment_dynamics)
        assert f"{shot.focal_mm:g}mm" in plan.camera
        assert f"T{shot.t_stop:g}" in plan.camera
        assert any(
            cue in plan.motion.casefold()
            for cue in (
                "slow",
                "gentle",
                "gradual",
                "steady",
                "measured",
                "brisk",
                "continuous",
            )
        )
        assert any(
            cue in plan.motion.casefold()
            for cue in ("left", "right", "toward", "away", "forward", "back", "up", "down")
        )
        assert "lighting" in plan.scene.casefold() or "light" in plan.scene.casefold()
        assert validate_wan_prompt_plan(plan) == []
        assert "rewrites the at" not in shot.prompt.casefold()
        assert "built from the idea" not in plan.scene.casefold()
        assert "rain-soaked blue hour" in plan.scene.casefold()


def test_prompt_contract_separates_dual_expert_intent_and_full_negative_conditioning() -> None:
    project = expand_idea(
        Brief(
            idea=(
                "A scarred mountain guide in a red waxed-canvas coat crosses a rope bridge "
                "during a snow squall, then turns toward her team."
            ),
            customer_kind=CustomerKind.FEATURE,
            target_minutes=0.5,
        )
    )
    shot = project.shots[0]
    plan = shot.prompt_plan

    assert plan is not None
    assert "layout" in plan.high_noise_plan.casefold()
    assert "motion" in plan.high_noise_plan.casefold()
    assert "detail" in plan.low_noise_plan.casefold()
    assert "identity" in plan.low_noise_plan.casefold()
    for term in (
        "morphing",
        "warping",
        "face deformation",
        "identity change",
        "flickering",
        "jitter",
        "static motion",
        "blurry",
        "low quality",
        "extra limbs",
        "distorted hands",
        "distorted fingers",
        "sudden cuts",
        "teleports",
    ):
        assert term in shot.negative_prompt.casefold()


def test_reference_subject_plan_preserves_the_supplied_medium_and_world() -> None:
    still = Asset(
        id="ast-team",
        kind=AssetKind.IMAGE,
        filename="team.png",
        mime="image/png",
        bytes=12,
        path="assets/ast-team/team.png",
    )
    project = expand_idea(
        Brief(
            idea=(
                "Using this attached team of characters and same background, animate the "
                "characters collaborating on a story wall."
            ),
            customer_kind=CustomerKind.SERIES,
            target_minutes=10 / 60,
            production_kind="sample",
            sample_seconds=10,
            attachment_ids=[still.id],
        ),
        assets=[still],
    )

    for shot in project.shots:
        assert shot.prompt_plan is not None
        assert "exact supplied" in shot.prompt_plan.subject.casefold()
        assert "original rendering medium" in shot.prompt_plan.scene.casefold()
        assert "photoreal" not in shot.prompt.casefold()
        assert "cartoon" not in shot.negative_prompt.casefold()
        assert "picture-in-picture" in shot.negative_prompt.casefold()


def test_character_only_reference_can_enter_the_scripted_world() -> None:
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
                "Animate this character from the attached image walking slowly across a "
                "real production interface under soft screen light."
            ),
            customer_kind=CustomerKind.SERIES,
            target_minutes=0.5,
            attachment_ids=[still.id],
        ),
        assets=[still],
    )

    shot = project.shots[0]
    assert shot.reference_lock == "character"
    assert "supplied background" not in shot.prompt_plan.scene.casefold()
    assert "new background" not in shot.negative_prompt.casefold()
    assert "original rendering medium" in shot.prompt_plan.scene.casefold()


def test_natural_idea_clauses_remain_complete_in_the_compiled_layers() -> None:
    project = expand_idea(
        Brief(
            idea=(
                "A midnight botanical archivist restores a storm-damaged glasshouse "
                "while paper moths circle her lantern; slow dolly in, rain-streaked "
                "windows, cyan moonlight and warm practicals."
            ),
            customer_kind=CustomerKind.FEATURE,
            target_minutes=5 / 60,
            production_kind="sample",
            sample_seconds=5,
        )
    )
    plan = project.shots[0].prompt_plan

    assert plan is not None
    assert plan.subject.startswith("A midnight botanical archivist,")
    assert "while paper," not in plan.subject
    assert plan.motion.startswith("restores a storm-damaged glasshouse")
    assert "rain-streaked windows" in plan.scene
    assert not plan.scene.casefold().startswith(("in,", "in;"))
    assert plan.diagnostics == []


def test_prompt_validator_detects_camera_conflicts_and_vague_motion() -> None:
    project = expand_idea(
        Brief(
            idea="A watchmaker follows a loose spring across a workbench.",
            customer_kind=CustomerKind.COMMERCIAL,
            target_minutes=0.5,
        )
    )
    plan = project.shots[0].prompt_plan
    assert plan is not None
    broken = plan.model_copy(
        update={
            "motion": "dancing",
            "camera": "static wide shot, slow push-in, 24mm lens at T4",
        }
    )

    diagnostics = validate_wan_prompt_plan(broken)

    assert "motion needs an explicit pace" in diagnostics
    assert "motion needs an explicit direction" in diagnostics
    assert "camera instructions conflict" in diagnostics


def test_prompt_receipt_covers_negative_conditioning_and_strategy_version() -> None:
    project = expand_idea(
        Brief(
            idea="A watchmaker follows a loose spring across a workbench.",
            customer_kind=CustomerKind.COMMERCIAL,
            target_minutes=0.5,
        )
    )
    original = project.shots[0]
    changed_negative = original.model_copy(deep=True)
    changed_negative.negative_prompt += ", duplicate watch hands"
    changed_strategy = original.model_copy(deep=True)
    assert changed_strategy.prompt_plan is not None
    changed_strategy.prompt_plan.strategy_version = "wan22-four-layer-v-next"

    assert prompt_receipt_sha256(original) != prompt_receipt_sha256(changed_negative)
    assert prompt_receipt_sha256(original) != prompt_receipt_sha256(changed_strategy)


def test_dispatch_guard_rejects_any_prompt_or_negative_drift_from_the_plan() -> None:
    project = expand_idea(
        Brief(
            idea="A watchmaker follows a loose spring across a workbench.",
            customer_kind=CustomerKind.COMMERCIAL,
            target_minutes=0.5,
        )
    )
    shot = project.shots[0]
    assert validate_wan_prompt_dispatch(shot) == []

    changed_prompt = shot.model_copy(deep=True)
    changed_prompt.prompt += " Surprise aerial cut."
    changed_negative = shot.model_copy(deep=True)
    changed_negative.negative_prompt = "watermark"

    assert "positive prompt does not match its typed plan" in validate_wan_prompt_dispatch(
        changed_prompt
    )
    assert "negative prompt does not match its typed plan" in validate_wan_prompt_dispatch(
        changed_negative
    )


def test_qc_diagnosis_repairs_only_the_failed_prompt_layer() -> None:
    project = expand_idea(
        Brief(
            idea="A watchmaker follows a loose spring across a workbench.",
            customer_kind=CustomerKind.COMMERCIAL,
            target_minutes=0.5,
        )
    )
    original = project.shots[0].prompt_plan
    assert original is not None

    motion_repair = repair_wan_prompt_plan(original, "Subject froze halfway through the clip")
    camera_repair = repair_wan_prompt_plan(original, "Camera jumped and cut mid-clip")
    texture_repair = repair_wan_prompt_plan(original, "Output looks plastic and airbrushed")
    voice_repair = repair_wan_prompt_plan(
        original,
        "Voice performance failed: qualified lip-sync confidence was below 0.90",
    )

    assert motion_repair is not None
    assert motion_repair.motion != original.motion
    assert motion_repair.subject == original.subject
    assert motion_repair.camera == original.camera
    assert motion_repair.scene == original.scene
    assert camera_repair is not None
    assert camera_repair.camera != original.camera
    assert camera_repair.subject == original.subject
    assert camera_repair.motion == original.motion
    assert camera_repair.scene == original.scene
    assert texture_repair is not None
    assert texture_repair.scene != original.scene
    assert texture_repair.subject == original.subject
    assert texture_repair.motion == original.motion
    assert texture_repair.camera == original.camera
    assert validate_wan_prompt_plan(motion_repair) == []
    assert validate_wan_prompt_plan(camera_repair) == []
    assert validate_wan_prompt_plan(texture_repair) == []
    assert voice_repair is not None
    assert voice_repair.motion != original.motion
    assert "phoneme" in voice_repair.motion.casefold()
    assert "mouth" in voice_repair.motion.casefold()
    assert voice_repair.subject == original.subject
    assert voice_repair.camera == original.camera
    assert voice_repair.scene == original.scene
    assert validate_wan_prompt_plan(voice_repair) == []


def test_prompt_plans_are_deterministic_for_the_same_idea() -> None:
    brief = Brief(
        idea="A lighthouse keeper paints the fog the color of warnings.",
        customer_kind=CustomerKind.FEATURE,
        target_minutes=0.5,
    )

    first = expand_idea(brief)
    second = expand_idea(brief)

    assert [shot.prompt for shot in first.shots] == [shot.prompt for shot in second.shots]
    assert [
        shot.prompt_plan.model_dump(mode="json") for shot in first.shots if shot.prompt_plan
    ] == [
        shot.prompt_plan.model_dump(mode="json") for shot in second.shots if shot.prompt_plan
    ]


def test_boundary_repair_falls_back_compactly_on_a_full_locked_plan() -> None:
    """An over-budget append must degrade to a compact repair, never to seed-only."""
    from diony_contracts.models import Asset, Brief
    from diony_contracts.studio import expand_idea, refresh_reference_shot
    from diony_contracts.wan_prompt import repair_wan_prompt_for_qc

    still = Asset(
        id="ast-character",
        kind="image",
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
            customer_kind="series",
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
        shot, "Shot boundary continuity failed (first/last SSIM 0.6100; required 0.75)."
    )
    assert "temporal discontinuity" in shot.negative_prompt
    assert "without a temporal jump" in shot.prompt_plan.motion
    assert 80 <= shot.prompt_plan.word_count <= 140


def test_spoken_plan_directs_the_performance_within_budget_and_stays_repairable() -> None:
    from diony_contracts.enums import CameraMove, ContinuityMode, ReferenceLock
    from diony_contracts.wan_prompt import (
        SPOKEN_AUDIO_STRATEGY,
        compile_wan_prompt_plan,
        render_negative_prompt,
        render_wan_prompt,
        repair_wan_prompt_plan,
        spoken_wan_prompt_plan,
        validate_wan_prompt_plan,
    )

    idea = (
        "In the slightly messy shared kitchen, soft morning light filters as Marcus looks at "
        "them expectantly, already nodding. Lila doesn't understand what he means yet."
    )
    plan = compile_wan_prompt_plan(
        idea=idea,
        world="A claymation flat-share: cramped kitchen, mismatched mugs, morning light",
        wardrobe="Show wardrobe, one silhouette",
        shot_index=1,
        focal_mm=50,
        t_stop=2.8,
        lighting_ratio="4:1 key/fill, motivated",
        camera_move=CameraMove.DOLLY_IN,
        reference_lock=ReferenceLock.CHARACTER,
        continuity_mode=ContinuityMode.REFERENCE,
        generation_method="s2v-14B",
        audio_strategy="native_s2v",
        sample_seconds=5,
    )
    # The template bug that doubled the word is gone from newly compiled scenes.
    assert "motivated motivated" not in render_wan_prompt(plan)

    spoken = spoken_wan_prompt_plan(plan, speaker="Marcus", focal_mm=50)
    assert spoken is not None
    assert spoken.audio_strategy == SPOKEN_AUDIO_STRATEGY
    assert validate_wan_prompt_plan(spoken) == []
    prompt = render_wan_prompt(spoken)
    assert "Marcus speaks the line straight toward camera" in prompt
    assert "mouth closed and still through each pause" in prompt
    assert "medium close-up, 50mm lens at T2.8, static camera with no movement" in prompt
    # No blocking beat, no push-in, no truncated fragment of another character.
    assert "push-in" not in prompt and "shifting weight" not in prompt
    assert "Lila" not in prompt and "nodding" not in prompt
    assert spoken.subject == plan.subject and spoken.identity_anchors == plan.identity_anchors
    assert 80 <= spoken.word_count <= 140
    assert "frozen lips" in render_negative_prompt(spoken)
    # Deterministic and idempotent: re-directing a spoken plan changes nothing.
    again = spoken_wan_prompt_plan(spoken, speaker="Marcus", focal_mm=50)
    assert again is not None and render_wan_prompt(again) == prompt

    # A lip-sync retake keeps the direction and only sharpens the negatives.
    repaired = repair_wan_prompt_plan(
        spoken, "Voice performance failed: lip-sync confidence 0.87 is below 0.90"
    )
    assert repaired is not None
    assert render_wan_prompt(repaired) == prompt
    assert validate_wan_prompt_plan(repaired) == []
