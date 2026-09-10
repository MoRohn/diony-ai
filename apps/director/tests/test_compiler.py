"""Provider-aware prompt compilation: dialects, bible injection, lossless lock data."""

import pytest

from diony_contracts.director import CloudProvider
from diony_director import compiler, packagebuilder, screenwriter


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


def test_dialogue_prompts_never_carry_the_scripted_words() -> None:
    # Blueprint 28: the words are recorded in post — a prompt that quotes the
    # line asks the provider to invent a voice, and every clip gets a
    # different one. The prompt acts the delivery and mutes the track.
    package = _package()
    dialogue = next(c for c in package.clips if c.clip_class == "sync-dialogue")
    version = compiler.compile_clip_prompt(dialogue, package)
    line = dialogue.audio_packet.script_excerpt[0]["line"]
    assert f'"{line}"' not in version.prompt
    assert "no voice-over" in version.prompt
    if dialogue.audio_packet.on_camera:
        assert "is speaking mid-conversation" in version.prompt


def test_a_picture_clip_on_omni_flash_keeps_its_action() -> None:
    # The Flow lane routes picture clips through Omni 1.1 Flash too — the omni
    # dialect must carry the authored action, not just performance and sound.
    draft = screenwriter.write_script(
        "a getaway driver decides to leave town from a neon diner", runtime_target_s=60
    )
    package = packagebuilder.build_package(
        "s-omni-pic", "a getaway", draft, provider_policy="google-flow"
    )
    for clip in package.clips:
        action = compiler._action_text(clip, package)
        if clip.clip_class in {"sync-dialogue", "vo-over"} or not action:
            continue
        assert clip.model == "omni-flash"
        version = compiler.compile_clip_prompt(clip, package)
        head = action.split(".")[0].strip()
        assert head and head in version.prompt, (
            f"{clip.clip_id}: the authored action is missing from the omni prompt"
        )
        break
    else:
        pytest.fail("no picture clip with authored action to test")


def test_grok_prompts_weave_sound_inline_and_never_carry_dialogue() -> None:
    package = _package()
    picture = next(c for c in package.clips if c.provider is CloudProvider.GROK)
    version = compiler.compile_clip_prompt(picture, package)
    assert "sound:" in version.prompt.lower() or "ambien" in version.prompt.lower()


def test_a_voice_lock_compiles_on_any_route() -> None:
    # Blueprint 28: a voice lock binds the POST narrator, not the provider —
    # so it no longer constrains routing, even onto Grok.
    package = _package()
    dialogue = next(c for c in package.clips if c.clip_class == "sync-dialogue")
    rerouted = dialogue.model_copy(deep=True)
    rerouted.provider = CloudProvider.GROK
    rerouted.model = "grok-imagine-video"
    rerouted.duration_s = 6
    version = compiler.compile_clip_prompt(rerouted, package)
    assert version.version == "v1"
    assert "no voice-over" in version.prompt


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


def test_artifact_refs_never_register_provider_voices() -> None:
    # Blueprint 28: the provider never speaks — registering a voice with it
    # would invite an invented performance the narrator then fights.
    package = _package()
    dialogue = next(c for c in package.clips if c.clip_class == "sync-dialogue")
    refs = compiler.provider_artifact_refs(dialogue, package)
    assert "voices" not in refs
