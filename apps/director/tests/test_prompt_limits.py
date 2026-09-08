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


# --------------------------------------------------------------------------
# 2026-09-07, s-8a4239727b: fifteen Flow clips held at 4100–4258 chars. Every
# one had fitted at compile time; the likeness guard then replaced a name
# with a forty-character description AFTER the fit — in the compiler, and
# again at the submit gate beside a picture. The description now lands
# before the fit, and the gate refits whatever still does not fit.

STRATEGIST = {
    "name": "Marlene Voss",
    "descriptor": "the composed woman with the blonde-gray bob and cream blazer",
    "label": "The Strategist",
}


def _flow_package_with_a_real_person():
    from diony_contracts.production import MasterPrompt

    draft = screenwriter.write_script("a campaign ad across a sleeping city " * 200, runtime_target_s=90)
    for char in draft.characters:
        char.age_body_notes = "an intensely specific person " * 120
        char.forbidden_drift = [f"no wardrobe deviation of kind {i} under any lighting" for i in range(60)]
    for setting in draft.settings:
        setting.architecture_notes = "a very long acoustic description " * 100
        setting.grade_language = "an elaborate grade description " * 80
    package = packagebuilder.build_package("s-lim-flow", "a campaign ad", draft, provider_policy="google-flow")
    lead = package.characters[0]
    person = dict(STRATEGIST, name=lead.name, aliases=[lead.name, lead.name.split()[0]])
    package.master_prompt = MasterPrompt(
        idea="a campaign ad", target_duration_s=90, prompt="a campaign ad", real_people=[person]
    )
    return package, person


def _lead_clip(package, person):
    """A clip that shows the lead, named in its camera line — the piece that
    never gives way under length pressure, so the description must fit."""
    clip = next(c for c in package.clips if any(
        p.get("char_id") == package.characters[0].char_id for p in c.continuity.character_packs
    ))
    clip.camera_idea = f"Locked medium on {person['name']} at the rail, blazer sharp"
    return clip


def test_a_description_that_replaces_a_name_lands_before_the_fit() -> None:
    package, person = _flow_package_with_a_real_person()
    clip = _lead_clip(package, person)
    version = compiler.compile_clip_prompt(clip, package)
    limit = compiler.prompt_limit(clip.provider)
    assert len(version.prompt) <= limit, f"{len(version.prompt)} > {limit}"
    # the screenwriter writes cast names in caps, so the caps mention becomes
    # the label; either way the name is gone and the description is in
    assert person["name"] not in version.prompt
    assert person["label"].upper() in version.prompt or person["descriptor"] in version.prompt
    assert "No morphing" in version.prompt  # the guard survived the fit


def test_the_submit_gate_refits_a_prompt_the_description_outgrew(monkeypatch) -> None:
    from diony_contracts.director import (
        CloudGenerationMode,
        CostEstimate,
        CostEstimateLine,
        DirectorOp,
        DirectorOpType,
    )
    from diony_director import screenwriter as sw

    monkeypatch.setattr(sw, "brain_url", lambda: "")
    package, person = _flow_package_with_a_real_person()
    sid = package.session_id
    session._PACKAGES[sid] = package
    clip = _lead_clip(package, person)
    limit = compiler.prompt_limit(clip.provider)
    # the prompt an earlier build persisted: fitted with the name, then the
    # gate's description pushed it over — exactly what the room replays on Retry
    people = package.master_prompt.real_people
    package.master_prompt.real_people = []
    named = compiler.compile_clip_prompt(clip, package).prompt
    package.master_prompt.real_people = people
    assert person["name"] in named and len(named) <= limit
    # …sitting right at the limit, the way a fitted prompt does: the gate's
    # longer description is what tips it over
    guard = compiler.ANTI_ARTIFACT
    assert named.endswith(guard)
    room = limit - len(named)
    named = named[: -len(guard)] + ("The rail holds. " * (room // 16 + 1))[:room] + guard
    assert len(named) == limit
    op = DirectorOp(
        id="op-outgrown", session_id=sid, op=DirectorOpType.GENERATE, clip_id=clip.clip_id,
        provider=clip.provider, model=clip.model, mode=CloudGenerationMode.I2V,
        prompt=named, prompt_version="v1", duration_s=clip.duration_s,
        artifact_refs={"start_image": "user:frame"},
        estimate=CostEstimate(
            id="e", workspace_id="w",
            lines=[CostEstimateLine(label="s", provider_id="google-flow", model="m", unit="u", usd_micros=1)],
        ),
    )
    session._OPS[op.id] = op
    hits = session.guard_pictured_prompt(op, pictured=True)
    assert hits > 0
    assert len(op.prompt) <= limit, f"{len(op.prompt)} > {limit}"
    assert person["name"] not in op.prompt
    assert person["label"].upper() in op.prompt or person["descriptor"] in op.prompt
    assert "No morphing" in op.prompt and op.prompt.startswith("Locked medium on ")
    assert session.PROMPT_REFIT_MARK in op.rationale
    assert session.runtime(op.id)["prompt_refit"]["after"] <= limit
    # the adapter's last line of defense never fires now
    base.ensure_prompt_fits(op.prompt, "google-flow")


def test_a_revision_keeps_the_base_instead_of_capping_it_at_400_chars() -> None:
    package = _bloated_package()
    clip = package.clips[0]
    clip.add_prompt(compiler.compile_clip_prompt(clip, package))
    revised = compiler.revise_prompt(clip, package, note="fix-face")
    limit = compiler.prompt_limit(clip.provider)
    assert len(revised.prompt) <= limit
    assert revised.prompt.endswith(f"Revision: {compiler.REVISION_DIRECTIVES['fix-face']}")
    # the old fit capped the whole base to 400 chars the moment the rider
    # pushed it over; the base now fits against what the rider leaves
    v1 = clip.prompt_versions[0].prompt
    assert len(revised.prompt) > 1200, "the base kept its priorities, not a 400-char stump"
    assert len(revised.prompt) >= len(v1) - 400
    assert clip.camera_idea.split("—")[0].strip()[:20] in revised.prompt


def test_refit_text_keeps_the_camera_and_the_guard_and_trims_between() -> None:
    first = "Locked medium on the keeper at the rail."
    last = "No morphing, no floating motion, no warped anatomy."
    middle = " ".join(f"Sentence number {i} carries some scene business that can give way." for i in range(80))
    text = f"{first} {middle} {last}"
    out = compiler.refit_text(text, 1200)
    assert len(out) <= 1200
    assert out.startswith(first) and out.endswith(last)
    assert "Sentence number 0" in out  # trims from the end, gently first
    assert compiler.refit_text("short.", 1200) == "short."
