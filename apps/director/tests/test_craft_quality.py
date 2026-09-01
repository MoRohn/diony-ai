"""Studio-grade craft pipeline: authored shots rule the frame, the supervisor
lint catches slop before a cent is spent, the script-doctor round resolves it,
and every Grok prompt carries photographic ground truth."""

import json

import pytest

from diony_contracts.production import Scene, Script, ScriptLine
from diony_director import compiler, packagebuilder, screenwriter, splitter


def _draft():
    return screenwriter.write_script(
        "a getaway driver decides to leave town from a neon diner", runtime_target_s=60
    )


# --- the supervisor lint -----------------------------------------------------


def test_lint_flags_shotless_abstract_and_overlong_writing() -> None:
    body = {
        "scenes": [
            {
                "lines": [
                    {"kind": "action", "text": "Tension fills the room."},
                    {
                        "kind": "dialogue",
                        "text": (
                            "I am going to explain every single part of the plan to you "
                            "right now in one very long breath"
                        ),
                    },
                ]
            }
        ]
    }
    notes = screenwriter._script_lint(body)
    joined = " | ".join(notes)
    assert "no authored shot" in joined
    assert "abstract, not filmable" in joined
    assert "trim under 18" in joined
    assert "no sound line" in joined


def test_lint_flags_repeated_shots_and_identical_scene_shapes() -> None:
    scene = {
        "lines": [
            {"kind": "action", "text": "She stands.", "shot": "Medium single, slow push-in"},
            {"kind": "action", "text": "She waits.", "shot": "Medium single, slow push-in"},
            {"kind": "sound", "text": "Rain on tin."},
        ]
    }
    notes = screenwriter._script_lint({"scenes": [scene, dict(scene)]})
    joined = " | ".join(notes)
    assert "repeats back-to-back" in joined
    assert "identical shape" in joined


def test_a_clean_script_lints_clean() -> None:
    body = {
        "scenes": [
            {
                "lines": [
                    {
                        "kind": "action",
                        "text": "MARA slides the keys across the counter.",
                        "shot": "Top-down insert, keys crossing chipped formica",
                    },
                    {"kind": "sound", "text": "The diner compressor hums; keys scrape."},
                ]
            },
            {
                "lines": [
                    {
                        "kind": "dialogue",
                        "text": "We leave tonight.",
                        "speaker": "MARA",
                        "shot": "Close-up at eye level, neon rim light on wet hair",
                    },
                    {
                        "kind": "action",
                        "text": "COLE pockets the keys without looking down.",
                        "shot": "Medium two-shot, rack focus to the hand",
                    },
                    {"kind": "sound", "text": "A slow surf-rock instrumental from the kitchen radio."},
                ]
            },
        ]
    }
    assert screenwriter._script_lint(body) == []


# --- the script-doctor round -------------------------------------------------


def _minimal_body(logline: str, *, clean: bool) -> dict:
    line: dict = {"kind": "action", "text": "AVA sets the lantern on the rail."}
    if clean:
        line["shot"] = "Wide from the pier end, lantern flame the only warm light"
    lines = [line]
    if clean:
        lines.append({"kind": "sound", "text": "Halyards tick against masts."})
    return {
        "logline": logline,
        "beats": [{"beat": "b", "story_time": "Night 1"}],
        "settings": [{
            "setting_id": "set-a", "name": "Pier", "kind": "EXT",
            "room_tone": "waves", "time_weather": "Night 1, fog",
            "grade_language": "sodium against black water",
            "camera_kit": "32/50mm, slow arcs",
        }],
        "characters": [{
            "char_id": "chr-a", "name": "AVA",
            "age_body_notes": "late 30s, wiry, cropped grey-streaked hair",
            "wardrobe": "yellow oilskin over a navy sweater",
        }],
        "scenes": [{
            "heading": "EXT. PIER - NIGHT", "setting_ref": "set-a",
            "story_time": "Night 1", "cast": ["chr-a"], "emotional_job": "unease",
            "lines": lines,
        }],
    }


class _Resp:
    status_code = 200

    def __init__(self, body: dict):
        self._body = body

    def json(self):
        return {
            "choices": [{"message": {"content": json.dumps(self._body)}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
        }


def test_script_doctor_round_replaces_a_flagged_draft(monkeypatch) -> None:
    monkeypatch.setenv("DIONY_DIRECTOR_BRAIN_URL", "http://127.0.0.1:9999")
    monkeypatch.delenv("DIONY_DIRECTOR_BRAIN_REFINE", raising=False)
    calls = []

    def fake_post(url, *, what, json: dict, headers, timeout):  # noqa: A002
        calls.append(what)
        if what == "brain draft":
            return _Resp(_minimal_body("first draft", clean=False))
        return _Resp(_minimal_body("doctored", clean=True))

    from diony_director.providers import base as provider_base

    monkeypatch.setattr(provider_base, "post_with_retries", fake_post)
    draft = screenwriter.write_script("a pier at night", runtime_target_s=30)
    assert calls == ["brain draft", "brain refine"]
    assert draft.script.logline == "doctored"
    assert any("script-doctor pass resolved" in w for w in draft.warnings)
    # both rounds' spend is reported honestly
    assert draft.brain_usage["completion_tokens"] == 40


def test_script_doctor_failure_ships_the_first_draft_named(monkeypatch) -> None:
    monkeypatch.setenv("DIONY_DIRECTOR_BRAIN_URL", "http://127.0.0.1:9999")
    monkeypatch.delenv("DIONY_DIRECTOR_BRAIN_REFINE", raising=False)

    def fake_post(url, *, what, json: dict, headers, timeout):  # noqa: A002
        if what == "brain draft":
            return _Resp(_minimal_body("first draft", clean=False))
        raise RuntimeError("refine transport died")

    from diony_director.providers import base as provider_base

    monkeypatch.setattr(provider_base, "post_with_retries", fake_post)
    draft = screenwriter.write_script("a pier at night", runtime_target_s=30)
    assert draft.script.logline == "first draft"
    assert any("script-doctor pass unavailable" in w for w in draft.warnings)


def test_a_clean_draft_skips_the_doctor_round(monkeypatch) -> None:
    monkeypatch.setenv("DIONY_DIRECTOR_BRAIN_URL", "http://127.0.0.1:9999")
    monkeypatch.delenv("DIONY_DIRECTOR_BRAIN_REFINE", raising=False)
    calls = []

    def fake_post(url, *, what, json: dict, headers, timeout):  # noqa: A002
        calls.append(what)
        return _Resp(_minimal_body("clean first try", clean=True))

    from diony_director.providers import base as provider_base

    monkeypatch.setattr(provider_base, "post_with_retries", fake_post)
    draft = screenwriter.write_script("a pier at night", runtime_target_s=30)
    assert calls == ["brain draft"], "no notes → no second spend"
    assert draft.script.logline == "clean first try"


# --- authored shots rule the split ------------------------------------------


def _script_with_shots():
    lines = [
        ScriptLine(line_id="ln-1", kind="heading", text="INT. DINER - NIGHT", scene_id="sc-1"),
        ScriptLine(
            line_id="ln-2", kind="action", text="Mara wipes the counter.", scene_id="sc-1",
            shot="Overhead top-shot, the rag carving clean arcs through spilt sugar",
        ),
        ScriptLine(
            line_id="ln-3", kind="dialogue", text="We leave tonight.", speaker="MARA",
            scene_id="sc-1",
            shot="Low-angle close-up, jukebox glow under her jaw",
        ),
    ]
    return Script(
        id="scr-shots",
        runtime_target_s=30,
        scenes=[
            Scene(
                scene_id="sc-1", heading="INT. DINER - NIGHT", setting_ref="set-primary",
                cast=["chr-lead"], line_ids=[l.line_id for l in lines],
            )
        ],
        lines=lines,
    )


def test_authored_shots_become_the_camera_verbatim() -> None:
    draft = _draft()
    clips = splitter.split_script(_script_with_shots(), draft.characters, draft.settings)
    action = clips[0]
    spoken = next(c for c in clips if c.clip_class in {"sync-dialogue", "vo-over"})
    assert action.camera_idea.startswith("Overhead top-shot")
    assert spoken.camera_idea.startswith("Low-angle close-up")
    # a top-shot opener reads as the room — the plate may open it
    assert action.continuity.lock_recipe == "still"


def test_non_wide_authored_opener_releases_the_plate_first_frame() -> None:
    draft = _draft()
    script = _script_with_shots()
    script.lines[1].shot = "Macro insert on a sugar spoon, everything else falling away"
    clips = splitter.split_script(script, draft.characters, draft.settings)
    assert clips[0].continuity.lock_recipe == "ref", (
        "a macro opener generated FROM a wide plate first-frame fights itself"
    )


# --- the compiled picture carries photographic ground truth ------------------


def test_grok_prompts_carry_realism_and_no_filler() -> None:
    package = packagebuilder.build_package(
        "s-real", "a getaway", _draft(), provider_policy="grok"
    )
    for clip in package.clips:
        prompt = compiler.compile_clip_prompt(clip, package).prompt
        assert "natural skin texture" in prompt
        assert "The scene breathes" not in prompt


# --- scene-construction doctrine (s-2e0a74644b postmortem) -------------------


def test_blocking_cues_become_camera_law() -> None:
    package = packagebuilder.build_package(
        "s-block", "a getaway", _draft(), provider_policy="grok"
    )
    clip = next(c for c in package.clips if c.clip_class not in {"sync-dialogue", "vo-over"})
    clip.camera_idea = "Wide full shot from directly behind the hooded figure"
    prompt = compiler.compile_clip_prompt(clip, package).prompt
    assert "never crosses the axis" in prompt
    assert "face is never visible" in prompt

    clip.camera_idea = "Wide two-shot at eye level across the counter"
    prompt = compiler.compile_clip_prompt(clip, package).prompt
    assert "Blocking lock" not in prompt


def test_peopled_openers_compose_cast_into_the_plate() -> None:
    package = packagebuilder.build_package(
        "s-open", "a getaway", _draft(), provider_policy="grok"
    )
    opener = next(
        c for c in package.clips
        if c.continuity.lock_recipe == "still" and c.continuity.character_packs
    )
    setting = package.setting(str(opener.continuity.setting_plate["setting_id"]))
    setting.plate_asset_id = "/vault/plates/diner.jpg"
    for pack in opener.continuity.character_packs:
        try:
            package.character(str(pack["char_id"])).grok_ref_pack = ["/vault/sheets/a.jpg"]
        except KeyError:
            pass
    refs = compiler.provider_artifact_refs(opener, package)
    assert "start_image" not in refs, "an empty plate first-frame makes the cast pop in"
    references = refs.get("references") or []
    assert references and references[0] == "/vault/plates/diner.jpg", "the master room leads"


def test_anchor_drift_fails_a_mutated_set(tmp_path) -> None:
    import shutil
    import subprocess

    from diony_director import review as review_mod

    if not (shutil.which("ffmpeg")):
        pytest.skip("ffmpeg not installed")
    try:
        from diony_producer.quality import frame_channel_stats  # noqa: F401
    except ImportError:
        pytest.skip("producer quality tools unavailable")
    ffmpeg = shutil.which("ffmpeg")
    anchor = tmp_path / "anchor.jpg"
    clip_path = tmp_path / "clip.mp4"
    subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "color=c=navy:s=320x180:d=1", "-frames:v", "1", str(anchor)],
        check=True, capture_output=True,
    )
    subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "color=c=cyan:s=320x180:d=2:r=24", str(clip_path)],
        check=True, capture_output=True,
    )
    families: dict = {}
    measured: dict = {}
    issues: list = []
    review_mod._measure_continuity(
        clip_path, None, families, measured, issues, anchor_path=anchor
    )
    assert measured["anchor_color_drift"] > review_mod.ANCHOR_DRIFT_FAIL
    assert families["continuity"] == "fail"
    assert any("master look" in i for i in issues)


# --- the veo-3.1-quality lane (no reference images) --------------------------


def test_flow_picture_lane_rides_fast_and_quality_refs_stay_legal() -> None:
    """Pro plan doctrine: the policy routes picture to veo-3.1-fast (quality is
    Ultra-paygated). The quality lane stays a tested capability: force a clip
    onto it and its payload must remain legal (no reference images)."""
    package = packagebuilder.build_package(
        "s-veo-q", "a getaway", _draft(), provider_policy="google-flow"
    )
    picture = [c for c in package.clips if c.model.startswith("veo")]
    assert picture and all(c.model == "veo-3.1-fast" for c in picture)
    for clip in picture:
        clip.model = "veo-3.1-quality"  # the Ultra lane, exercised explicitly
        setting = package.setting(str(clip.continuity.setting_plate["setting_id"]))
        setting.plate_asset_id = "/vault/plates/diner.jpg"
        refs = compiler.provider_artifact_refs(clip, package)
        # quality has NO reference lane — the plate may only ride as an i2v
        # start frame; an illegal referenceImage payload must never compile
        assert not refs.get("references"), f"{clip.clip_id} built an illegal reference payload"
        from diony_director import capabilities

        from diony_contracts.director import CloudGenerationMode, DirectorOpType
        from tests.test_continuity_music import _gen_op

        op = _gen_op(
            "op-q", "s-veo-q", clip.clip_id,
            provider=clip.provider, model=clip.model, duration_s=clip.duration_s,
        )
        op.artifact_refs = dict(refs)
        assert capabilities.violations(op) == []


def test_quality_re_anchor_opens_from_the_scene_master_frame(monkeypatch, tmp_path) -> None:
    from types import SimpleNamespace

    from diony_contracts.director import CloudProvider
    from diony_director import app as app_module, session
    from tests.test_continuity_music import _gen_op

    package = packagebuilder.build_package(
        "s-veo-anchor", "a getaway", _draft(), provider_policy="google-flow"
    )
    clip = next(c for c in package.clips if c.model.startswith("veo"))
    clip.model = "veo-3.1-quality"  # force the Ultra lane to test its re-anchor

    op = _gen_op(
        "op-vq", "s-veo-anchor", clip.clip_id,
        provider=CloudProvider.GOOGLE_FLOW, model="veo-3.1-quality",
    )
    prev = _gen_op(
        "op-vp", "s-veo-anchor", "cl-prev",
        provider=CloudProvider.GOOGLE_FLOW, model="veo-3.1-quality", status="completed",
    )
    frame_path = str(tmp_path / "frames" / "op-vp.jpg")
    master_path = str(tmp_path / "frames" / "master-op-first.jpg")

    monkeypatch.setattr(session, "clip_for_op", lambda _op: clip)
    monkeypatch.setattr(session, "package_for", lambda _sid: package)
    monkeypatch.setattr(session, "apply_uploads_to_refs", lambda _sid, _c, _p, refs: refs)
    monkeypatch.setattr(session, "chain_position", lambda _sid, _oid: 2)
    monkeypatch.setattr(session, "previous_completed_clip_op", lambda _sid, _op: prev)
    monkeypatch.setattr(
        session, "runtime", lambda _oid: {"clip": SimpleNamespace(path=str(tmp_path / "prev.mp4"))}
    )
    monkeypatch.setattr(app_module, "_last_frame", lambda _p, _d, at_s=None, sid="": frame_path)
    monkeypatch.setattr(app_module, "_scene_master_frame", lambda _op: master_path)

    app_module._late_bind_locks(op)

    assert op.artifact_refs.get("start_image") == master_path, (
        "a no-reference model re-anchors from the scene's master frame"
    )
    assert "references" not in op.artifact_refs
