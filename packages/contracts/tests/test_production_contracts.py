"""Blueprint 26 production package contracts: script, bibles, clip units, reviews."""

import pytest
from pydantic import ValidationError

from diony_contracts.director import CloudGenerationMode, CloudProvider
from diony_contracts.production import (
    AudioPacket,
    CharacterProfile,
    ClipReview,
    ClipUnit,
    ContinuityLock,
    ProductionPackage,
    PromptVersion,
    Scene,
    Script,
    ScriptLine,
    SettingProfile,
    VoiceProfile,
)


def _script() -> Script:
    lines = [
        ScriptLine(line_id="ln-1", kind="heading", text="INT. DINER - NIGHT", scene_id="sc-1"),
        ScriptLine(
            line_id="ln-2", kind="action", text="MARA slides into the booth.", scene_id="sc-1"
        ),
        ScriptLine(line_id="ln-3", kind="cue", text="MARA", speaker="MARA", scene_id="sc-1"),
        ScriptLine(
            line_id="ln-4",
            kind="dialogue",
            text="Not tonight. Tonight we drive.",
            speaker="MARA",
            scene_id="sc-1",
        ),
    ]
    scene = Scene(
        scene_id="sc-1",
        heading="INT. DINER - NIGHT",
        setting_ref="set-diner",
        cast=["chr-mara"],
        story_time="Night 1",
        line_ids=[l.line_id for l in lines],
    )
    return Script(
        id="scr-1",
        logline="A getaway driver chooses the road.",
        runtime_target_s=60,
        beats=[{"beat": "the decision", "story_time": "Night 1"}],
        scenes=[scene],
        lines=lines,
    )


def test_script_scene_lines_must_exist() -> None:
    script = _script()
    script.scenes[0].line_ids.append("ln-missing")
    with pytest.raises(ValidationError):
        Script.model_validate(script.model_dump())


def test_script_locks_with_a_timestamp() -> None:
    script = _script()
    assert script.locked is False
    script.lock()
    assert script.locked is True and script.locked_at is not None


def test_clip_unit_requires_a_positive_quantized_duration() -> None:
    with pytest.raises(ValidationError):
        ClipUnit(
            clip_id="cl-1",
            scene_id="sc-1",
            order=1,
            clip_class="silent-picture",
            duration_s=0,
            camera_idea="wide establish",
            audio_packet=AudioPacket(clip_class="silent-picture"),
        )


def test_prompt_versions_append_and_latest_wins() -> None:
    clip = ClipUnit(
        clip_id="cl-1",
        scene_id="sc-1",
        order=1,
        clip_class="sync-dialogue",
        duration_s=8,
        camera_idea="close-up on Mara",
        provider=CloudProvider.GOOGLE_FLOW,
        model="omni-flash",
        mode=CloudGenerationMode.T2V,
        audio_packet=AudioPacket(clip_class="sync-dialogue"),
    )
    clip.add_prompt(PromptVersion(version="v1", prompt="first"))
    clip.add_prompt(PromptVersion(version="v2-fix-face", prompt="second", note="fix-face"))
    assert clip.latest_prompt().prompt == "second"
    with pytest.raises(ValueError):
        clip.add_prompt(PromptVersion(version="v1", prompt="dup"))


def test_character_profile_carries_forbidden_drift_defaults() -> None:
    char = CharacterProfile(char_id="chr-mara", name="Mara")
    assert "no hair restyle" in char.forbidden_drift
    assert char.sheet_version == 1


def test_voice_profile_defaults_to_an_unapproved_omni_voice() -> None:
    voice = VoiceProfile(voice_id="vox-mara", engine_ref="Achird")
    assert voice.engine == "omni_preset"
    assert voice.approved is False


def test_production_package_refuses_a_clip_from_an_unknown_scene() -> None:
    script = _script()
    clip = ClipUnit(
        clip_id="cl-1",
        scene_id="sc-404",
        order=1,
        clip_class="silent-picture",
        duration_s=6,
        camera_idea="wide",
        audio_packet=AudioPacket(clip_class="silent-picture"),
    )
    with pytest.raises(ValidationError):
        ProductionPackage(
            id="pkg-1",
            session_id="s-1",
            idea="diner heist",
            script=script,
            characters=[CharacterProfile(char_id="chr-mara", name="Mara")],
            settings=[SettingProfile(setting_id="set-diner", name="Diner")],
            clips=[clip],
        )


def test_clip_review_verdict_and_families() -> None:
    review = ClipReview(
        id="rev-1",
        clip_id="cl-1",
        verdict="warn",
        families={"duration": "pass", "identity": "unmeasured"},
        issues=["identity unmeasured: no sheet embeddings on this host"],
        recommended_op="accept",
    )
    assert review.families["identity"] == "unmeasured"


def test_continuity_lock_recipes_are_typed() -> None:
    lock = ContinuityLock(
        lock_recipe="character_asset",
        character_packs=[
            {"char_id": "chr-mara", "sheet_version": 1, "pack_kind": "flow_character"}
        ],
    )
    assert lock.lock_recipe == "character_asset"
    with pytest.raises(ValidationError):
        ContinuityLock(lock_recipe="vibes")
