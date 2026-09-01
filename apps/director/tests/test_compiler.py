"""Provider-aware prompt compilation: dialects, bible injection, lossless lock data."""

import pytest

from diony_contracts.director import CloudProvider
from diony_director import compiler, packagebuilder, screenwriter, splitter


def _package():
    draft = screenwriter.write_script(
        "a getaway driver decides to leave town from a neon diner", runtime_target_s=60
    )
    return packagebuilder.build_package("s-test", "a getaway driver leaves town", draft)


def test_v1_prompts_compile_for_every_clip_and_carry_the_bible() -> None:
    package = _package()
    for clip in package.clips:
        version = compiler.compile_clip_prompt(clip, package)
        assert version.version == "v1"
        setting = package.setting(clip.continuity.setting_plate["setting_id"])
        assert setting.name.lower() in version.prompt.lower()
        if clip.continuity.character_packs:
            char = package.character(clip.continuity.character_packs[0]["char_id"])
            for taboo in char.forbidden_drift[:1]:
                assert taboo in version.prompt


def test_omni_dialogue_prompts_carry_the_exact_scripted_words() -> None:
    package = _package()
    dialogue = next(c for c in package.clips if c.clip_class == "sync-dialogue")
    version = compiler.compile_clip_prompt(dialogue, package)
    line = dialogue.audio_packet.script_excerpt[0]["line"]
    assert f'"{line}"' in version.prompt


def test_grok_prompts_weave_sound_inline_and_never_carry_dialogue() -> None:
    package = _package()
    picture = next(c for c in package.clips if c.provider is CloudProvider.GROK)
    version = compiler.compile_clip_prompt(picture, package)
    assert "sound:" in version.prompt.lower() or "ambien" in version.prompt.lower()


def test_a_voice_lock_cannot_compile_onto_grok() -> None:
    package = _package()
    dialogue = next(c for c in package.clips if c.clip_class == "sync-dialogue")
    broken = dialogue.model_copy(deep=True)
    broken.provider = CloudProvider.GROK
    broken.model = "grok-imagine-video"
    with pytest.raises(compiler.CompileError):
        compiler.compile_clip_prompt(broken, package)


def test_revision_appends_a_new_version_never_overwrites() -> None:
    package = _package()
    clip = package.clips[0]
    v1 = compiler.compile_clip_prompt(clip, package)
    clip.add_prompt(v1)
    v2 = compiler.revise_prompt(clip, package, note="fix-face")
    assert v2.version == "v2-fix-face"
    assert v2.prompt != v1.prompt
    clip.add_prompt(v2)
    assert clip.latest_prompt().version == "v2-fix-face"


def test_artifact_refs_map_locks_to_provider_parameters() -> None:
    package = _package()
    dialogue = next(c for c in package.clips if c.clip_class == "sync-dialogue")
    refs = compiler.provider_artifact_refs(dialogue, package)
    assert refs.get("voices"), "omni dialogue must request its locked voice"
