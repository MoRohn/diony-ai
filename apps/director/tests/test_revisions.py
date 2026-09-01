"""The fix ladder: cheapest fix that preserves lock, memory of what failed, hard budgets."""

from diony_contracts.director import CloudGenerationMode, CloudProvider, DirectorOpType
from diony_contracts.production import ClipReview
from diony_director import packagebuilder, revisions, screenwriter
from diony_director.compiler import compile_clip_prompt


def _package():
    draft = screenwriter.write_script(
        "a getaway driver decides to leave town from a neon diner", runtime_target_s=60
    )
    package = packagebuilder.build_package("s-x", "a getaway driver", draft)
    for clip in package.clips:
        clip.add_prompt(compile_clip_prompt(clip, package))
    return package


def _review(clip_id: str, families: dict, issues: list[str]) -> ClipReview:
    return ClipReview(id="rev-1", clip_id=clip_id, verdict="fail", families=families, issues=issues)


def test_audio_failure_on_a_flow_clip_plans_an_edit_in_place() -> None:
    package = _package()
    clip = next(c for c in package.clips if c.clip_class == "sync-dialogue")
    rev = _review(clip.clip_id, {"audio": "fail"}, ["no audio stream"])
    op, narration = revisions.plan_revision(
        rev, clip, package, session_id="s-x", revision_count=0, failure_memory=set()
    )
    assert op is not None
    assert op.op is DirectorOpType.EDIT
    assert op.mode is CloudGenerationMode.V2V_EDIT
    assert op.provider is CloudProvider.GOOGLE_FLOW
    assert op.estimate is not None
    assert "$" in narration


def test_short_duration_on_a_grok_clip_plans_an_extension() -> None:
    package = _package()
    clip = next(c for c in package.clips if c.provider is CloudProvider.GROK)
    rev = _review(clip.clip_id, {"duration": "fail"}, ["measured 3.0s vs booked 6s"])
    op, _ = revisions.plan_revision(
        rev, clip, package, session_id="s-x", revision_count=0, failure_memory=set()
    )
    assert op is not None
    assert op.op is DirectorOpType.EXTEND
    assert op.mode is CloudGenerationMode.EXTENSION


def test_generic_failure_regenerates_with_a_new_prompt_version() -> None:
    package = _package()
    clip = next(c for c in package.clips if c.provider is CloudProvider.GROK)
    rev = _review(clip.clip_id, {"delivery": "fail"}, ["empty media"])
    op, _ = revisions.plan_revision(
        rev, clip, package, session_id="s-x", revision_count=0, failure_memory=set()
    )
    assert op is not None
    assert op.op is DirectorOpType.GENERATE
    assert op.prompt_version.startswith("v2-")
    assert op.prompt != clip.prompt_versions[0].prompt


def test_a_failed_prompt_is_never_resubmitted_verbatim() -> None:
    package = _package()
    clip = next(c for c in package.clips if c.provider is CloudProvider.GROK)
    rev = _review(clip.clip_id, {"delivery": "fail"}, ["empty media"])
    memory: set = set()
    op1, _ = revisions.plan_revision(
        rev, clip, package, session_id="s-x", revision_count=0, failure_memory=memory
    )
    clip.add_prompt(revisions.as_prompt_version(op1))
    memory.add(revisions.failure_key(op1))
    op2, _ = revisions.plan_revision(
        rev, clip, package, session_id="s-x", revision_count=1, failure_memory=memory
    )
    assert op2 is not None
    assert revisions.failure_key(op2) != revisions.failure_key(op1)


def test_the_revision_budget_is_a_hard_stop() -> None:
    package = _package()
    clip = package.clips[0]
    rev = _review(clip.clip_id, {"delivery": "fail"}, ["empty media"])
    op, narration = revisions.plan_revision(
        rev, clip, package, session_id="s-x", revision_count=3, failure_memory=set(),
        max_revisions_per_clip=3,
    )
    assert op is None
    assert "budget" in narration.lower()
