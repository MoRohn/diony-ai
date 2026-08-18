from diony_contracts.edits import apply_clip_edit, interpret_instruction, version_ladder
from diony_contracts.enums import Engine, PictureLockState
from diony_contracts.fixture import demo_project


def test_interpret_preserves_identity_by_default() -> None:
    intent = interpret_instruction("Make the rain heavier in the alley.")
    assert "identity" in intent.preserve
    assert "wardrobe" in intent.preserve
    assert intent.engine == Engine.VACE_FUN_A14B
    assert "regenerate from scratch" not in intent.vace_prompt.lower()


def test_interpret_allows_wardrobe_when_asked() -> None:
    intent = interpret_instruction("Change her vest to a red coat. Keep her face.")
    assert "identity" in intent.preserve
    assert "wardrobe" not in intent.preserve


def test_apply_edit_creates_child_version_not_overwrite() -> None:
    project = demo_project()
    parent_id = "sh-010-t2"
    project = apply_clip_edit(
        project,
        source_take_id=parent_id,
        instruction="Hold her eyes on the nick. No new face.",
    )
    parent = next(t for t in project.takes if t.id == parent_id)
    children = [t for t in project.takes if t.parent_take_id == parent_id]
    assert len(children) == 1
    child = children[0]
    assert child.version == parent.version + 1
    assert parent_id in child.lineage
    assert child.engine == Engine.VACE_FUN_A14B
    assert child.edit_id
    assert project.shot("sh-010").selected_take_id == child.id
    assert any(e.id == child.edit_id for e in project.edits)
    # parent artifact still listed
    assert parent.asset_720p_id is not None


def test_version_ladder_orders_lineage() -> None:
    project = demo_project()
    project = apply_clip_edit(
        project, source_take_id="sh-010-t2", instruction="Darker tungsten practicals."
    )
    child = next(t for t in project.takes if t.parent_take_id == "sh-010-t2")
    project = apply_clip_edit(
        project, source_take_id=child.id, instruction="Hold the rain on the window."
    )
    ladder = version_ladder(project, "sh-010")
    ids = [t.id for t in ladder if t.shot_id == "sh-010" and (t.parent_take_id or t.id == "sh-010-t2" or t.version >= 1)]
    selected = project.shot("sh-010").selected_take_id
    assert selected != "sh-010-t2"
    versions = [t.version for t in version_ladder(project, "sh-010")]
    assert versions == sorted(versions)


def test_picture_lock_refuses_clip_edit() -> None:
    project = demo_project()
    assert project.timeline is not None
    project.timeline.picture_lock = PictureLockState.LOCKED
    try:
        apply_clip_edit(
            project,
            source_take_id="sh-010-t2",
            instruction="Hold her eyes on the nick.",
        )
        raise AssertionError("expected picture-lock refusal")
    except ValueError as exc:
        assert "locked" in str(exc).lower()
