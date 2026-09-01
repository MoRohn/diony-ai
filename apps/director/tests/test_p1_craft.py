"""P1 craft layer: screenwriter v2 (wardrobe/grade/vision), one-beat shot
splitting with authored joins, per-dialect sound, assembly trims and beds,
wired QC families, and the closed ledger leaks."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from diony_contracts.director import CloudGenerationMode, CloudProvider, DirectorOpType
from diony_contracts.production import ClipReview, Scene, Script, ScriptLine
from diony_director import (
    assembly,
    compiler,
    estimate as estimate_mod,
    ledger,
    packagebuilder,
    revisions,
    screenwriter,
    splitter,
)

HAVE_FFMPEG = shutil.which("ffmpeg") is not None


def _brain_body(**overrides) -> dict:
    body = {
        "logline": "A keeper answers the door.",
        "beats": [{"beat": "the knock", "story_time": "Night 1"}],
        "settings": [{
            "setting_id": "set-lh", "name": "Lighthouse Gallery", "kind": "INT",
            "room_tone": "wind against glass",
            "time_weather": "Night 1, storm light",
            "grade_language": "brass lamplight against storm blue",
            "camera_kit": "32mm, low angle, slow push-ins",
        }],
        "characters": [{
            "char_id": "chr-el", "name": "ELIAS",
            "wardrobe": "salt-stained grey peacoat over a cream fisherman's sweater",
        }],
        "scenes": [{
            "heading": "INT. LIGHTHOUSE GALLERY - NIGHT",
            "setting_ref": "set-lh",
            "story_time": "Night 1",
            "cast": ["chr-el"],
            "emotional_job": "make the audience dread the door",
            "lines": [
                {"kind": "action", "text": "ELIAS turns to the stairwell door."},
                {"kind": "dialogue", "text": "I know that knock.", "speaker": "ELIAS"},
            ],
        }],
    }
    body.update(overrides)
    return body


class _BrainResponse:
    status_code = 200

    def __init__(self, body: dict):
        self._body = body

    def json(self) -> dict:
        return {
            "choices": [{"message": {"content": json.dumps(self._body)}}],
            "usage": {"prompt_tokens": 1200, "completion_tokens": 640},
        }


# --- W1: screenwriter schema v2 + vision ------------------------------------


def test_brain_craft_fields_reach_the_bibles(monkeypatch) -> None:
    monkeypatch.setenv("DIONY_DIRECTOR_BRAIN_URL", "http://127.0.0.1:9999")
    monkeypatch.setattr(screenwriter.httpx, "post", lambda *a, **k: _BrainResponse(_brain_body()))
    draft = screenwriter.write_script("a lighthouse keeper hears a knock", runtime_target_s=30)
    setting = draft.settings[0]
    assert setting.time_weather == "Night 1, storm light"
    assert setting.grade_language == "brass lamplight against storm blue"
    assert setting.camera_kit == "32mm, low angle, slow push-ins"
    assert draft.characters[0].wardrobe["default"].startswith("salt-stained")
    assert draft.script.scenes[0].emotional_job == "make the audience dread the door"
    assert draft.warnings == []
    assert draft.brain_usage == {"prompt_tokens": 1200, "completion_tokens": 640}


def test_missing_craft_fields_backfill_with_named_warnings(monkeypatch) -> None:
    monkeypatch.setenv("DIONY_DIRECTOR_BRAIN_URL", "http://127.0.0.1:9999")
    body = _brain_body()
    del body["characters"][0]["wardrobe"]
    del body["settings"][0]["grade_language"]
    monkeypatch.setattr(screenwriter.httpx, "post", lambda *a, **k: _BrainResponse(body))
    draft = screenwriter.write_script("a keeper", runtime_target_s=30)
    assert draft.characters[0].wardrobe["default"], "wardrobe must exist even when defaulted"
    assert draft.settings[0].grade_language
    assert any("wardrobe" in w and "ELIAS" in w for w in draft.warnings)
    assert any("grade_language" in w for w in draft.warnings)


def test_reference_images_reach_the_brain_as_pixels(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DIONY_DIRECTOR_BRAIN_URL", "http://127.0.0.1:9999")
    still = tmp_path / "lead.png"
    still.write_bytes(b"\x89PNG\r\n\x1a\npixels")
    seen = {}

    def fake_post(url, **kwargs):
        seen["json"] = kwargs.get("json")
        return _BrainResponse(_brain_body())

    monkeypatch.setattr(screenwriter.httpx, "post", fake_post)
    screenwriter.write_script("a keeper", runtime_target_s=30, reference_images=[str(still)])
    content = seen["json"]["messages"][1]["content"]
    assert isinstance(content, list), "with references the user turn is multimodal"
    image_parts = [p for p in content if p.get("type") == "image_url"]
    assert len(image_parts) == 1
    assert image_parts[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert "MATCH what the images actually show" in content[0]["text"]


def test_the_template_dresses_its_cast() -> None:
    draft = screenwriter.write_script("two rivals meet at dawn", runtime_target_s=45)
    for char in draft.characters:
        assert char.wardrobe.get("default")


# --- W2: one beat per clip, authored joins, real camera sentences -----------


def _two_beat_script() -> tuple[Script, list, list]:
    draft = screenwriter.write_script("a getaway driver leaves a neon diner", runtime_target_s=60)
    lines = [
        ScriptLine(line_id="ln-1", kind="heading", text="INT. NEON DINER - NIGHT", scene_id="sc-1"),
        ScriptLine(line_id="ln-2", kind="action", text="LEAD counts the keys twice.", scene_id="sc-1"),
        ScriptLine(line_id="ln-3", kind="action", text="SECOND slides the map across.", scene_id="sc-1"),
        ScriptLine(line_id="ln-4", kind="dialogue", text="Tonight we drive.", speaker="LEAD", scene_id="sc-1"),
        ScriptLine(line_id="ln-5", kind="action", text="SECOND nods once.", scene_id="sc-1"),
    ]
    script = Script(
        id="scr-2b",
        runtime_target_s=30,
        scenes=[Scene(
            scene_id="sc-1", heading="INT. NEON DINER - NIGHT", setting_ref="set-primary",
            cast=["chr-lead", "chr-second"], line_ids=[l.line_id for l in lines],
        )],
        lines=lines,
    )
    return script, draft.characters, draft.settings


def test_each_action_beat_becomes_its_own_clip() -> None:
    script, characters, settings = _two_beat_script()
    clips = splitter.split_script(script, characters, settings)
    picture = [c for c in clips if c.clip_class in {"silent-picture", "ambience-led", "score-led"}]
    assert len(picture) == 3, "two action beats before dialogue + one after = three picture clips"
    assert all(len(c.script_line_ids) == 1 for c in picture)


def test_camera_language_is_sentences_never_truncated_prose() -> None:
    script, characters, settings = _two_beat_script()
    clips = splitter.split_script(script, characters, settings)
    for clip in clips:
        assert "coverage —" not in clip.camera_idea, "the [:48] prose fragment is gone"
        assert not clip.camera_idea.endswith("…")
    openers = [c for c in clips if c.camera_idea.startswith("Wide establishing")]
    assert len(openers) == 1


def test_two_hander_dialogue_gets_over_the_shoulder_coverage() -> None:
    script, characters, settings = _two_beat_script()
    clips = splitter.split_script(script, characters, settings)
    dialogue = next(c for c in clips if c.clip_class == "sync-dialogue")
    assert "Over-the-shoulder" in dialogue.camera_idea
    assert "LEAD" in dialogue.camera_idea and "SECOND" in dialogue.camera_idea
    reaction = clips[clips.index(dialogue) + 1]
    assert "Reaction close-up on SECOND" in reaction.camera_idea, "the listener reacts, not the speaker"


def test_joins_are_authored_bridge_reaction_cut_and_scene_cut() -> None:
    script, characters, settings = _two_beat_script()
    clips = splitter.split_script(script, characters, settings)
    dialogue = next(c for c in clips if c.clip_class == "sync-dialogue")
    assert dialogue.join_next == "reaction_cut"
    assert clips[-1].join_next == "cut", "the last clip of a scene is a hard cut"
    assert clips[0].join_next == "bridge"


def test_a_scene_opening_on_dialogue_buys_no_empty_establishing_clip() -> None:
    draft = screenwriter.write_script("a getaway driver leaves town", runtime_target_s=60)
    lines = [
        ScriptLine(line_id="ln-1", kind="heading", text="INT. CAR - NIGHT", scene_id="sc-1"),
        ScriptLine(line_id="ln-2", kind="dialogue", text="Go. Now.", speaker="LEAD", scene_id="sc-1"),
    ]
    script = Script(
        id="scr-d1", runtime_target_s=15,
        scenes=[Scene(scene_id="sc-1", heading="INT. CAR - NIGHT", setting_ref="set-primary",
                      cast=["chr-lead"], line_ids=["ln-1", "ln-2"])],
        lines=lines,
    )
    clips = splitter.split_script(script, draft.characters, draft.settings)
    assert len(clips) == 1
    assert clips[0].clip_class == "sync-dialogue"


def test_sound_lines_become_foley_on_the_nearest_picture() -> None:
    draft = screenwriter.write_script(
        "a getaway driver decides to leave town from a neon diner", runtime_target_s=60
    )
    clips = splitter.split_script(draft.script, draft.characters, draft.settings)
    opener = clips[0]
    assert opener.clip_class == "ambience-led", "the template's sound line upgrades the opener"
    assert opener.audio_packet.foley, "the sound line lands as a foley event"


# --- W6: every dialect directs its soundtrack -------------------------------


def _package(policy: str = "auto"):
    draft = screenwriter.write_script(
        "a getaway driver decides to leave town from a neon diner", runtime_target_s=60
    )
    return packagebuilder.build_package("s-p1", "a getaway", draft, provider_policy=policy)


def test_the_veo_dialect_finally_directs_sound(monkeypatch) -> None:
    package = _package(policy="google-flow")
    picture = next(c for c in package.clips if c.model.startswith("veo"))
    version = compiler.compile_clip_prompt(picture, package)
    assert "Sound:" in version.prompt
    assert "Never:" in version.prompt, "the forbidden list reaches the prompt"


def test_wardrobe_reaches_the_prompt_now() -> None:
    package = _package()
    with_cast = next(c for c in package.clips if c.continuity.character_packs)
    version = compiler.compile_clip_prompt(with_cast, package)
    assert "wardrobe:" in version.prompt


def test_score_direction_reaches_score_led_clips() -> None:
    package = _package()
    scored = next(c for c in package.clips if c.clip_class == "score-led")
    version = compiler.compile_clip_prompt(scored, package)
    # a scored clip names its music as one continuing piece — and never also
    # says "no music" (the s-1b80548c4d contradiction that produced a ringtone)
    assert "score:" in version.prompt
    assert "continuing at the same tempo" in version.prompt
    assert "no music" not in version.prompt


# --- W7: the ledger leaks are closed ----------------------------------------


def test_grok_clip_estimates_book_their_image_inputs() -> None:
    package = _package()
    still_locked = next(
        c for c in package.clips
        if c.provider is CloudProvider.GROK and c.continuity.lock_recipe == "still"
    )
    est = estimate_mod.estimate_op(
        provider=still_locked.provider,
        model=still_locked.model,
        mode=still_locked.mode,
        duration_s=still_locked.duration_s,
        resolution=still_locked.resolution,
        image_inputs=estimate_mod.image_inputs_for(still_locked),
    )
    assert any(line.unit == "image_input" for line in est.lines)


def test_grok_extend_revisions_book_input_video_seconds() -> None:
    package = _package()
    clip = next(c for c in package.clips if c.provider is CloudProvider.GROK)
    clip.add_prompt(compiler.compile_clip_prompt(clip, package))
    review = ClipReview(
        id="r-ext", clip_id=clip.clip_id, verdict="fail",
        families={"duration": "fail"}, issues=["measured 2.0s vs booked 6s"],
    )
    op, _ = revisions.plan_revision(
        review, clip, package, session_id="s-p1", revision_count=0, failure_memory=set()
    )
    assert op is not None and op.op is DirectorOpType.EXTEND
    assert any(line.unit == "video_input_second" for line in op.estimate.lines)


def test_grok_actuals_meter_the_inputs_that_really_attached() -> None:
    from diony_contracts.director import DirectorOp

    est = estimate_mod.estimate_op(
        provider=CloudProvider.GROK, model="grok-imagine-video-1.5",
        mode=CloudGenerationMode.T2V, duration_s=6, resolution="720p",
    )
    op = DirectorOp(
        id="op-act", session_id="s-a", op=DirectorOpType.GENERATE,
        provider=CloudProvider.GROK, model="grok-imagine-video-1.5",
        mode=CloudGenerationMode.T2V, duration_s=6, resolution="720p",
        artifact_refs={"references": ["a.png", "b.png"]}, estimate=est,
    )
    breakdown, _, _ = ledger._grok_actuals(op, 6)
    bare = DirectorOp(
        id="op-bare", session_id="s-a", op=DirectorOpType.GENERATE,
        provider=CloudProvider.GROK, model="grok-imagine-video-1.5",
        mode=CloudGenerationMode.T2V, duration_s=6, resolution="720p", estimate=est,
    )
    bare_breakdown, _, _ = ledger._grok_actuals(bare, 6)
    assert breakdown.total_usd_micros == bare_breakdown.total_usd_micros + 2 * 10_000


def test_grok_sheet_stills_request_the_2k_they_pay_for(monkeypatch) -> None:
    from diony_director.providers import grok

    seen = {}

    class _Response:
        status_code = 200

        def json(self):
            return {"data": [{"url": "https://imgen.x.ai/still.png"}]}

    def fake_post(url, **kwargs):
        seen["json"] = kwargs.get("json")
        return _Response()

    monkeypatch.setattr(grok.httpx, "post", fake_post)
    grok.submit_image("casting photo", resolution="2k")
    assert seen["json"]["resolution"] == "2k"


# --- W3: assembly trims, per-clip leveling, room bed ------------------------


def test_effective_windows_trim_the_grid_surplus() -> None:
    probes = [{"duration_s": 8.0}, {"duration_s": 4.0}]
    specs = [{"needed_s": 5.2}, {}]
    windows = assembly.effective_windows(probes, specs)
    assert windows[0] == (0.0, 5.2), "the omni round-up ships as picture, not dead air"
    assert windows[1] == (0.0, 4.0)


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not installed")
def test_assembly_trims_and_beds_the_program(tmp_path) -> None:
    def make(path, duration):
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc2=size=320x180:duration={duration}",
             "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
             "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(path)],
            check=True, capture_output=True,
        )

    a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
    make(a, 3.0)
    make(b, 2.0)
    result = assembly.assemble(
        [a, b], tmp_path / "film.mp4",
        specs=[{"needed_s": 1.5}, {"join": "cut"}],
    )
    assert result.path.is_file()
    assert 3.0 <= result.duration_s <= 4.2, "clip A ships its 1.5s of need, not its 3s booking"


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not installed")
def test_assembly_accepts_a_named_look_profile(tmp_path) -> None:
    def make(path, duration):
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc2=size=320x180:duration={duration}",
             "-c:v", "libx264", "-preset", "ultrafast", str(path)],
            check=True, capture_output=True,
        )

    a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
    make(a, 1.0)
    make(b, 1.0)
    result = assembly.assemble([a, b], tmp_path / "film.mp4", look_profile="cinematic_warm")
    assert result.look == "cinematic_warm"
    assert result.path.is_file()


# --- W5: wired QC families ---------------------------------------------------


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not installed")
def test_review_measures_frames_and_chain_continuity(tmp_path) -> None:
    pytest.importorskip("numpy")
    from diony_director import review

    package = _package()
    clip = next(c for c in package.clips if c.provider is CloudProvider.GROK)

    def make(path):
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc2=size=320x180:duration={clip.duration_s}",
             "-f", "lavfi", "-i", f"sine=frequency=330:duration={clip.duration_s}",
             "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(path)],
            check=True, capture_output=True,
        )

    current, previous = tmp_path / "cur.mp4", tmp_path / "prev.mp4"
    make(current)
    make(previous)
    result = review.review_clip(current, clip, prev_clip_path=previous)
    assert result.families["frames"] == "pass"
    assert "black_frames" in result.measured
    assert result.families["continuity"] == "pass", "same-source clips must not read as drifted"
    assert "chain_color_drift" in result.measured


def test_revision_notes_key_on_the_failed_family() -> None:
    review = ClipReview(
        id="r-fam", clip_id="cl-1-1", verdict="fail",
        families={"continuity": "fail", "delivery": "pass"},
        issues=["colour drifted 0.140 from the previous shot"],
    )
    assert revisions._note_for(review) == "fix-continuity"
    frames = ClipReview(
        id="r-fr", clip_id="cl-1-1", verdict="fail",
        families={"frames": "fail"}, issues=["2 sampled frame(s) are black"],
    )
    assert revisions._note_for(frames) == "fix-frames"
