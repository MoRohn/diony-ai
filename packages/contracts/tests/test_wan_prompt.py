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
