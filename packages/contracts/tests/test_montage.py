"""Blueprint 24 montage intelligence: rundown review, diversification, decisions,
looks, subtitles, final review math, sizing, and prompt craft lint."""

from __future__ import annotations

import pytest

from diony_contracts import Brief, CustomerKind, expand_idea
from diony_contracts.enums import CameraMove
from diony_contracts.montage import (
    Decision,
    DecisionCategory,
    DecisionOption,
    LookProfile,
    ReviewStatus,
    ReviewVerdict,
    ShotFacts,
    ShotSize,
    AudioSpotcheck,
    PromisePreservation,
    SubtitleCheck,
    TechnicalProbe,
    VisualSpotcheck,
    append_decision,
    dialogue_cues,
    diversify_coverage,
    evaluate_final_review,
    format_sizing,
    look_filter,
    render_srt,
    render_vtt,
    review_rundown,
    review_shots,
    shot_size_for,
    size_production,
    verdict_for_score,
    wrap_cue_text,
)
from diony_contracts.wan_prompt import lint_wan_prompt_plan, strip_mood_adjectives


def _facts(sizes: list[str], *, static=None, lighting=None, hero=None, spoken=None, subject="") -> list[ShotFacts]:
    rows = []
    for index, size in enumerate(sizes):
        rows.append(
            ShotFacts(
                id=f"sh-{index:03d}",
                shot_size=ShotSize(size),
                static=bool(static[index]) if static else False,
                lighting_key=(lighting[index] if lighting else f"key {index}"),
                hero=bool(hero[index]) if hero else index == 1,
                spoken=bool(spoken[index]) if spoken else False,
                subject=subject or f"a named subject {index}",
            )
        )
    return rows


def test_shot_size_matches_prompt_framing_vocabulary() -> None:
    assert shot_size_for(24) == ShotSize.WIDE
    assert shot_size_for(40) == ShotSize.MEDIUM
    assert shot_size_for(50) == ShotSize.MEDIUM_CLOSE_UP
    assert shot_size_for(90) == ShotSize.CLOSE_UP


def test_verdict_thresholds() -> None:
    assert verdict_for_score(0.0) == ReviewVerdict.STRONG
    assert verdict_for_score(2.4) == ReviewVerdict.ACCEPTABLE
    assert verdict_for_score(3.0) == ReviewVerdict.REVISE
    assert verdict_for_score(4.2) == ReviewVerdict.FAIL


def test_varied_rundown_reviews_strong() -> None:
    review = review_rundown(_facts(["wide", "medium", "close_up", "medium_close_up", "wide", "close_up"]))
    assert review.verdict == ReviewVerdict.STRONG
    assert review.violations == []
    assert review.hero_count == 1


def test_monotone_rundown_names_every_violation() -> None:
    review = review_rundown(
        _facts(
            ["medium"] * 6,
            static=[True] * 6,
            lighting=["soft key"] * 6,
            hero=[False] * 6,
            subject="a person in a modern office",
        )
    )
    assert review.verdict == ReviewVerdict.REVISE and review.score == 3.6
    joined = " ".join(review.violations)
    assert "medium framing books 6 of 6" in joined
    assert "6 consecutive medium shots" in joined
    assert "locked-off" in joined
    assert "one lighting plan" in joined
    assert "no hero shot" in joined
    assert "generic phrasing" in joined
    assert review.suggestions


def test_spoken_shots_form_their_own_class() -> None:
    # Alternating close-ups on speakers are a dialogue scene, not a run.
    review = review_rundown(
        _facts(
            ["wide", "close_up", "close_up", "close_up", "medium", "close_up"],
            spoken=[False, True, True, True, False, True],
            static=[False, True, True, True, False, True],
        )
    )
    assert not any("consecutive" in item for item in review.violations)
    assert not any("locked-off" in item for item in review.violations)


def test_locked_background_waives_camera_checks() -> None:
    facts = _facts(["medium"] * 5, static=[True] * 5, lighting=["one"] * 5)
    for item in facts:
        item.locked_background = True
    review = review_rundown(facts)
    assert review.waived and review.verdict == ReviewVerdict.STRONG


def test_diversify_breaks_runs_and_static_monotony() -> None:
    pool = [
        {"focal_mm": 24, "t_stop": 4.0, "lighting_ratio": "sky", "camera_move": CameraMove.STATIC, "audio": None},
        {"focal_mm": 85, "t_stop": 2.0, "lighting_ratio": "eyes", "camera_move": CameraMove.DOLLY_IN, "audio": None},
    ]
    recipes = [
        {"focal_mm": 35, "t_stop": 2.8, "lighting_ratio": "x", "camera_move": CameraMove.STATIC, "audio": None}
        for _ in range(6)
    ]
    adjusted, changes = diversify_coverage(recipes, pool)
    sizes = [shot_size_for(rec["focal_mm"]) for rec in adjusted]
    assert sizes[2] != ShotSize.MEDIUM
    static = sum(1 for rec in adjusted if rec["camera_move"] == CameraMove.STATIC)
    assert static / len(adjusted) <= 0.6
    assert changes and all(":" in line for line in changes)
    # Dialogue recipes are never reframed.
    spoken = [dict(rec, audio=True) for rec in recipes]
    same, none = diversify_coverage(spoken, pool)
    assert none == [] and all(rec["focal_mm"] == 35 for rec in same)


def test_expand_idea_records_the_review_and_stays_strong() -> None:
    project = expand_idea(Brief(idea="A lighthouse keeper repairs the lamp before the storm.", customer_kind=CustomerKind.SERIES, target_minutes=1))
    assert project.rundown_review is not None
    assert project.rundown_review.shot_count == len(project.shots)
    assert project.rundown_review.verdict in (ReviewVerdict.STRONG, ReviewVerdict.ACCEPTABLE)
    assert review_shots(project.shots).score == project.rundown_review.score


def test_decision_requires_a_listed_selection_and_caps_the_snapshot() -> None:
    with pytest.raises(ValueError):
        Decision(id="d-1", stage="camera", category=DecisionCategory.RETAKE, subject="x",
                 options=[DecisionOption(id="a", label="a")], selected="b", reason="r")
    log: list[Decision] = []
    for n in range(405):
        append_decision(log, Decision(id=f"d-{n}", stage="camera", category=DecisionCategory.SEED_SELECTION,
                                      subject="seed", options=[DecisionOption(id="a", label="a")], selected="a", reason="r"))
    assert len(log) == 400 and log[0].id == "d-5"


def test_look_filter_blends_and_neutral_is_empty() -> None:
    assert look_filter(LookProfile.NEUTRAL, 0.6) == ""
    warm = look_filter(LookProfile.CINEMATIC_WARM, 0.6)
    assert warm.startswith("split=2[lookplate][looksrc];[looksrc]colorbalance")
    assert "all_opacity=0.600" in warm
    assert look_filter(LookProfile.MOODY_DARK, 1.0).startswith("curves=")
    lut = look_filter(LookProfile.NEUTRAL, 0.7, lut_path="/v/lo:ok.cube")
    assert "lut3d=file='/v/lo\\:ok.cube'" in lut and "all_opacity=0.700" in lut
    for profile in LookProfile:
        chain = look_filter(profile, 1.0)
        assert "saturation=1.2" not in chain and "saturation=1.3" not in chain


def test_subtitle_cues_follow_the_timeline_and_render() -> None:
    project = expand_idea(
        Brief(idea='MARA: "We leave at dawn, before the tide turns against us." TOM: "Then pack tonight."',
              customer_kind=CustomerKind.SERIES, target_minutes=0.5)
    )
    cues = dialogue_cues(project)
    assert cues, "authored dialogue yields cues"
    assert cues[0].start_seconds >= 0 and all(c.end_seconds > c.start_seconds for c in cues)
    assert [c.index for c in cues] == list(range(1, len(cues) + 1))
    srt = render_srt(cues)
    assert srt.startswith("1\n00:00:") and " --> " in srt
    vtt = render_vtt(cues)
    assert vtt.startswith("WEBVTT") and "<v " in vtt
    assert wrap_cue_text("word " * 30).count("\n") <= 1


def test_final_review_verdicts() -> None:
    good = evaluate_final_review(
        timeline_id="tl", master_path="m.mov",
        technical=TechnicalProbe(valid_container=True, width=1920, height=1080, fps=23.976, duration_seconds=30, frames=719, has_audio=True, codec="prores"),
        visual=VisualSpotcheck(frames_sampled=5), audio=AudioSpotcheck(dialogue_expected=True, silent=False, clipping=False),
        promise=PromisePreservation(target_seconds=30, delivered_seconds=30, duration_ok=True, fps_ok=True, loudness_ok=True, motion_ratio=0.9, motion_ok=True),
        subtitles=SubtitleCheck(expected=True, present=True, cue_count=3),
    )
    assert good.status == ReviewStatus.PASS and good.recommended_action.value == "present_to_user"
    black = evaluate_final_review(
        timeline_id="tl", master_path="m.mov",
        technical=TechnicalProbe(valid_container=True, has_audio=True, codec="prores"),
        visual=VisualSpotcheck(frames_sampled=5, black_frames=1), audio=AudioSpotcheck(),
        promise=PromisePreservation(), subtitles=SubtitleCheck(),
    )
    assert black.status == ReviewStatus.FAIL and black.recommended_action.value == "retake"
    soft = evaluate_final_review(
        timeline_id="tl", master_path="m.mov",
        technical=TechnicalProbe(valid_container=True, has_audio=True, codec="prores"),
        visual=VisualSpotcheck(frames_sampled=5), audio=AudioSpotcheck(),
        promise=PromisePreservation(target_seconds=60, delivered_seconds=40, duration_ok=False, motion_ratio=0.5, motion_ok=False),
        subtitles=SubtitleCheck(),
    )
    assert soft.status == ReviewStatus.REVISE and "40.0s against a 60.0s goal" in " ".join(soft.issues_found)
    silent = evaluate_final_review(
        timeline_id="tl", master_path="m.mov",
        technical=TechnicalProbe(valid_container=True, has_audio=True, codec="prores"),
        visual=VisualSpotcheck(), audio=AudioSpotcheck(dialogue_expected=True, silent=True),
        promise=PromisePreservation(), subtitles=SubtitleCheck(),
    )
    assert silent.status == ReviewStatus.FAIL


def test_sizing_never_fabricates_without_measurements() -> None:
    unmeasured = size_production(shot_count=20, camera_p50_seconds=300, finish_p50_seconds=40, measured_clips=2)
    assert unmeasured.basis == "unmeasured" and unmeasured.estimated_seconds_low is None
    assert format_sizing(unmeasured) == ""
    measured = size_production(shot_count=20, hero_count=2, camera_p50_seconds=300, finish_p50_seconds=40, measured_clips=12)
    assert measured.planned_takes == 22
    assert measured.estimated_seconds_low == 22 * 340
    assert measured.estimated_seconds_high == round(22 * 340 * 1.3, 1)
    assert "×1.3 retake allowance" in format_sizing(measured)


def test_mood_adjectives_are_stripped_and_linted() -> None:
    assert strip_mood_adjectives("A beautiful, epic woman walks through a stunning cinematic forest at 4k") == "A woman walks through a forest"
    assert strip_mood_adjectives("An epic and majestic city") == "A city"
    assert strip_mood_adjectives("A fisherman pulls a rope at dawn") == "A fisherman pulls a rope at dawn"
    project = expand_idea(Brief(idea="A breathtaking, cinematic hero walks toward the epic city gates.", customer_kind=CustomerKind.SERIES, target_minutes=0.5))
    plan = project.shots[0].prompt_plan
    assert plan is not None
    assert "breathtaking" not in plan.subject.casefold() and "epic" not in plan.motion.casefold()
    assert plan.craft_notes == []
    # A refinement that reintroduces mood words is flagged, but the scene layer's
    # technical "cinematic Rec.709" phrase is not.
    noisy = plan.model_copy(update={"motion": "walks epically toward the beautiful gates with steady weight", "subject": "a person; " + plan.subject})
    notes = lint_wan_prompt_plan(noisy)
    assert any("mood adjectives" in note for note in notes)
    assert any("generic phrasing" in note for note in notes)
    assert not any(note.startswith("scene leans") for note in lint_wan_prompt_plan(plan))
