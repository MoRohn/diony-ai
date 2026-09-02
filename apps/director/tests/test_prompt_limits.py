"""Provider prompt limits are a compile-time invariant, never a provider rejection.

xAI rejects prompts over 4096 chars (seen live: "Prompt length exceeds the
maximum allowed length of 4096"). The compiler fits every prompt by priority
— dialogue and camera intent survive whole; action and bible blocks trim —
and the adapters refuse oversize prompts before any network call.
"""

import pytest

from diony_contracts.director import CloudProvider
from diony_director import compiler, packagebuilder, screenwriter, session, splitter
from diony_director.providers import base, grok


def _bloated_package():
    draft = screenwriter.write_script("a heist across a sleeping city " * 200, runtime_target_s=90)
    for char in draft.characters:
        char.age_body_notes = "an intensely specific person " * 120
        char.forbidden_drift = [f"no wardrobe deviation of kind {i} under any lighting" for i in range(60)]
    for setting in draft.settings:
        setting.architecture_notes = "a very long acoustic description " * 100
        setting.grade_language = "an elaborate grade description " * 80
    return packagebuilder.build_package("s-lim", "a heist", draft)


def test_every_compiled_prompt_fits_the_provider_limit() -> None:
    package = _bloated_package()
    for clip in package.clips:
        version = compiler.compile_clip_prompt(clip, package)
        limit = compiler.prompt_limit(clip.provider)
        assert len(version.prompt) <= limit, f"{clip.clip_id}: {len(version.prompt)} > {limit}"


def test_speechless_track_and_anti_artifact_survive_the_trim() -> None:
    # Blueprint 28: what must survive a hard trim is no longer the quoted
    # line (it never rides the prompt) but the mute-location doctrine that
    # keeps the provider from inventing a voice the narrator then fights.
    package = _bloated_package()
    dialogue = next(c for c in package.clips if c.clip_class in {"sync-dialogue", "vo-over"})
    version = compiler.compile_clip_prompt(dialogue, package)
    line = dialogue.audio_packet.script_excerpt[0]["line"]
    assert f'"{line}"' not in version.prompt
    assert "no voice-over" in version.prompt
    assert "No morphing" in version.prompt
    assert dialogue.camera_idea.split("—")[0].strip()[:20] in version.prompt


def test_revisions_fit_too() -> None:
    package = _bloated_package()
    clip = package.clips[0]
    clip.add_prompt(compiler.compile_clip_prompt(clip, package))
    revised = compiler.revise_prompt(clip, package, note="fix-face")
    assert len(revised.prompt) <= compiler.prompt_limit(clip.provider)
    assert "Revision:" in revised.prompt


def test_one_shot_ideas_are_capped_not_rejected() -> None:
    sid = session.create_session()
    proposal = session.propose(sid, "a truck at dusk, " * 800, duration_s=6)
    assert len(proposal.op.prompt) <= compiler.prompt_limit(proposal.op.provider)


def test_adapters_refuse_oversize_prompts_before_any_network_call(monkeypatch) -> None:
    def no_network(*a, **k):  # pragma: no cover - must never run
        raise AssertionError("an oversize prompt reached the network")

    monkeypatch.setattr(grok.httpx, "post", no_network)
    from diony_contracts.director import (
        CloudGenerationMode,
        CostEstimate,
        CostEstimateLine,
        DirectorOp,
        DirectorOpType,
    )

    op = DirectorOp(
        id="op-big",
        session_id="s-1",
        op=DirectorOpType.GENERATE,
        provider=CloudProvider.GROK,
        model="grok-imagine-video-1.5",
        mode=CloudGenerationMode.T2V,
        prompt="x" * 5000,
        duration_s=6,
        estimate=CostEstimate(
            id="e", workspace_id="w",
            lines=[CostEstimateLine(label="s", provider_id="grok", model="m", unit="u", usd_micros=1)],
        ),
    )
    with pytest.raises(base.CloudAdapterError) as err:
        grok.submit(op)
    assert "4096" in str(err.value)


def test_screenwriter_caps_a_pasted_novel_of_an_idea() -> None:
    draft = screenwriter.write_script("an idea " * 2000, runtime_target_s=60)
    clips = splitter.split_script(draft.script, draft.characters, draft.settings)
    package = packagebuilder.build_package("s-nov", "an idea", draft)
    for clip in package.clips:
        version = compiler.compile_clip_prompt(clip, package)
        assert len(version.prompt) <= compiler.prompt_limit(clip.provider)
