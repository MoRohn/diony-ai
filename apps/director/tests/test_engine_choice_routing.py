"""Studio's engine choice drives the Director's routing."""

from diony_contracts.cluster import write_engine_choice
from diony_contracts.director import CloudProvider
from diony_director import compiler, packagebuilder, screenwriter, session, splitter


def _draft():
    return screenwriter.write_script(
        "a getaway driver decides to leave town from a neon diner", runtime_target_s=60
    )


def test_auto_provider_honors_the_studio_choice(tmp_path) -> None:
    write_engine_choice("google-flow")
    sid = session.create_session()
    proposal = session.propose(sid, "neon diner at night", duration_s=6)
    assert proposal.op.provider is CloudProvider.GOOGLE_FLOW
    # the Flow lane rides Omni 1.1 Flash — audio-native, frames, v2v, 10s grid
    assert proposal.op.model == "omni-flash"


def test_explicit_provider_always_beats_the_choice(tmp_path) -> None:
    write_engine_choice("google-flow")
    sid = session.create_session()
    proposal = session.propose(sid, "neon diner", provider="grok", duration_s=6)
    assert proposal.op.provider is CloudProvider.GROK


def test_grok_policy_routes_dialogue_to_grok_with_the_voice_unlocked() -> None:
    draft = _draft()
    clips = splitter.split_script(
        draft.script, draft.characters, draft.settings, provider_policy="grok"
    )
    dialogue = [c for c in clips if c.clip_class in {"sync-dialogue", "vo-over"}]
    assert dialogue
    for clip in clips:
        assert clip.provider is CloudProvider.GROK
    for clip in dialogue:
        assert clip.audio_packet.voice_ref == ""  # no lockable voice on Grok
        assert clip.audio_packet.script_excerpt  # the words still travel


def test_grok_policy_dialogue_compiles_with_a_mute_location_track() -> None:
    # Blueprint 28: the line is recorded in post — the Grok prompt acts the
    # delivery and declares the location track speechless, never the words.
    draft = _draft()
    package = packagebuilder.build_package("s-gp", "a getaway", draft, split=False)
    package.clips = splitter.split_script(
        draft.script, draft.characters, draft.settings, provider_policy="grok"
    )
    dialogue = next(c for c in package.clips if c.clip_class == "sync-dialogue")
    version = compiler.compile_clip_prompt(dialogue, package)
    line = dialogue.audio_packet.script_excerpt[0]["line"]
    assert f'"{line}"' not in version.prompt
    assert "no voice-over" in version.prompt


def test_flow_policy_routes_every_clip_through_omni_flash() -> None:
    # the whole Flow package — picture and dialogue — books on Omni 1.1 Flash:
    # one model, one look, characters + refs + frames + v2v on every clip
    draft = _draft()
    clips = splitter.split_script(
        draft.script, draft.characters, draft.settings, provider_policy="google-flow"
    )
    for clip in clips:
        assert clip.provider is CloudProvider.GOOGLE_FLOW
    assert clips and all(c.model == "omni-flash" for c in clips)


def test_lock_script_applies_the_studio_choice(tmp_path) -> None:
    write_engine_choice("grok")
    sid = session.create_session()
    session.start_production(sid, "a getaway driver leaves a neon diner", 60)
    package, ops, _bible, _reply = session.lock_script(sid)
    assert ops
    for op in ops:
        assert op.provider is CloudProvider.GROK
