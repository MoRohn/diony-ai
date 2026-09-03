"""Blueprint 27 — the Master AI Video Prompt Template as the Idea-to-Prompt
stage of every Director flow: one-shot bodies, screenplay briefs, per-clip
inheritance, duration control, persistence, and the async hold."""

from __future__ import annotations

import json
import time

import pytest
from master_brain_fakes import fast_master_derive, is_master_call, master_brain_body

from diony_contracts.director import CloudProvider
from diony_director import compiler, master_prompt, screenwriter, session


# --- the template and its derivation ----------------------------------------


def test_the_brain_is_told_the_template_verbatim_plus_duration_and_contract() -> None:
    text = master_prompt.system_prompt()
    assert text.startswith("MASTER AI VIDEO PROMPT TEMPLATE")
    for section in (
        "CONTEXT DERIVATION ENGINE",
        "17. FAILURE-PREVENTION CONSTRAINTS",
        "FINAL GENERATOR-READY PROMPT STRUCTURE",
        "OPTIMIZATION PASS",
        "CORE OPERATING PRINCIPLE",
        "TARGET DURATION",
        "OUTPUT CONTRACT",
    ):
        assert section in text
    assert "The clip ends with" in text


def test_offline_derivation_follows_the_generator_ready_structure() -> None:
    master = master_prompt.derive(
        "a getaway driver leaves a neon diner at night and walks to his car in the rain",
        target_duration_s=8,
    )
    assert master.source == "template"
    assert any("template" in w for w in master.warnings), "the stand-in is always named"
    prompt = master.prompt
    assert prompt.startswith("Create a cinematic realism video of")
    assert "The scene begins" in prompt
    assert "Lighting comes from neon signage" in prompt
    assert "Maintain strict continuity throughout" in prompt
    assert prompt.rstrip().count("The clip ends with") == 1
    assert "Constraints:" in prompt
    assert "Use the reference image" not in prompt
    assert 3 <= len(master.constraints) <= 6
    assert master.word_count == len(prompt.split())


@pytest.mark.parametrize(
    "seconds,beats",
    [(3, 2), (4, 2), (6, 3), (8, 3), (12, 4), (15, 4), (60, 6)],
)
def test_the_user_duration_scales_the_temporal_structure(seconds, beats) -> None:
    assert master_prompt.beat_budget(seconds) == beats
    master = master_prompt.derive("a dancer warms up", target_duration_s=seconds)
    assert len(master.beats) == beats
    assert master.target_duration_s == seconds
    assert f"Pace the action to play in about {seconds} seconds." in master.prompt


def test_a_reference_image_opens_the_prompt_and_locks_the_composition(tmp_path) -> None:
    still = tmp_path / "ref.png"
    still.write_bytes(b"\x89PNG\r\n\x1a\npixels")
    master = master_prompt.derive("she looks up", target_duration_s=4, reference_images=[str(still)])
    assert master.reference_image is True
    sentences = master.prompt.split(". ")
    assert master_prompt.REFERENCE_OPENER.rstrip(".") in sentences[1]
    assert "do not alter the reference-image composition" in master.constraints


def test_authored_elements_are_preserved_not_reinterpreted() -> None:
    master = master_prompt.derive(
        "a man waits", target_duration_s=6, authored={"camera": "Camera not moving"}
    )
    assert master.camera == "Camera not moving"
    assert "The camera not moving" in master.prompt
    assert "camera camera" not in master.prompt.lower()
    text = master_prompt.user_message("a man waits", 6, authored={"camera": "Camera not moving"})
    assert "ALREADY ESTABLISHED" in text and "Camera not moving" in text


# --- the brain path ------------------------------------------------------------


class _Resp:
    status_code = 200

    def __init__(self, body: dict, usage: dict | None = None):
        self._body = body
        self._usage = usage or {"prompt_tokens": 900, "completion_tokens": 300}

    def json(self) -> dict:
        return {
            "choices": [{"message": {"content": json.dumps(self._body)}}],
            "usage": self._usage,
        }


def test_brain_derivation_is_parsed_strictly_and_optimized(monkeypatch) -> None:
    monkeypatch.setenv("DIONY_DIRECTOR_BRAIN_URL", "http://127.0.0.1:9999")
    seen: dict = {}

    def fake_post(url, **kwargs):
        seen["url"] = url
        seen["json"] = kwargs["json"]
        # the brain forgot the ending line, repeated a sentence, and over-constrained
        body = master_brain_body(
            prompt=(
                "Create a cinematic realism video of a keeper opening a door. "
                "The camera holds a locked wide frame. The camera holds a locked wide frame. "
                "Lighting comes from the turning lamp."
            ),
            constraints=[f"constraint {i}" for i in range(9)],
        )
        return _Resp(body)

    monkeypatch.setattr(screenwriter.httpx, "post", fake_post)
    master = master_prompt.derive("a keeper opens a door", target_duration_s=8)
    assert seen["url"] == "http://127.0.0.1:9999/v1/chat/completions"
    assert seen["json"]["response_format"] == {"type": "json_object"}
    assert is_master_call(seen["json"])
    assert "TARGET DURATION: 8 seconds." in seen["json"]["messages"][1]["content"]
    assert master.source == "brain"
    assert master.estimated_duration_s == 8
    assert master.brain_usage == {"prompt_tokens": 900, "completion_tokens": 300}
    assert master.prompt.count("The camera holds a locked wide frame.") == 1
    assert "The clip ends with the keeper in the open doorway" in master.prompt
    assert "Pace the action to play in about 8 seconds." in master.prompt
    assert len(master.constraints) == 6
    assert master.prompt.rstrip().endswith("constraint 5.")
    assert any("trimmed 9 constraints" in w for w in master.warnings)
    assert any("repeated sentence" in w for w in master.warnings)
    assert master_prompt.optimize(master).prompt == master.prompt, "the pass is idempotent"


def test_brain_failure_is_a_named_state_never_a_template(monkeypatch) -> None:
    monkeypatch.setenv("DIONY_DIRECTOR_BRAIN_URL", "http://127.0.0.1:9999")

    def boom(*a, **k):
        raise screenwriter.httpx.HTTPError("down")

    monkeypatch.setattr(screenwriter.httpx, "post", boom)
    with pytest.raises(master_prompt.MasterUnavailable) as err:
        master_prompt.derive("a keeper", target_duration_s=8)
    assert "grok-4.6" in str(err.value) and "down" in str(err.value)


def test_a_brain_reply_without_beats_or_prompt_is_refused(monkeypatch) -> None:
    monkeypatch.setenv("DIONY_DIRECTOR_BRAIN_URL", "http://127.0.0.1:9999")
    # a screenplay-shaped reply is NOT a master prompt — the stage must not
    # accept the wrong artifact just because it is valid JSON
    monkeypatch.setattr(
        screenwriter.httpx,
        "post",
        lambda *a, **k: _Resp({"logline": "x", "beats": [{"beat": "b", "story_time": "t"}]}),
    )
    with pytest.raises(master_prompt.MasterUnavailable):
        master_prompt.derive("a keeper", target_duration_s=8)


def test_reference_stills_reach_the_master_brain_as_pixels(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DIONY_DIRECTOR_BRAIN_URL", "http://127.0.0.1:9999")
    still = tmp_path / "lead.png"
    still.write_bytes(b"\x89PNG\r\n\x1a\npixels")
    seen = {}

    def fake_post(url, **kwargs):
        seen["json"] = kwargs["json"]
        return _Resp(master_brain_body())

    monkeypatch.setattr(screenwriter.httpx, "post", fake_post)
    master = master_prompt.derive("a keeper", target_duration_s=8, reference_images=[str(still)])
    content = seen["json"]["messages"][1]["content"]
    assert isinstance(content, list)
    assert any(p.get("type") == "image_url" for p in content)
    assert "1 attached" in content[0]["text"]
    assert master.reference_image is True
    assert master.prompt.split(". ")[1].startswith("Use the reference image")


# --- one-shot: the master prompt IS the body ----------------------------------


def test_one_shot_ships_the_master_prompt_on_both_routes() -> None:
    sid = session.create_session()
    grok = session.propose(sid, "a woman dances in a neon diner", provider="grok", duration_s=8)
    assert grok.op.status == "proposed" and grok.op.prompt_version == "v1"
    assert grok.op.prompt.startswith("Create a cinematic realism video of a woman dances")
    assert "The clip ends with" in grok.op.prompt
    assert "No morphing" in grok.op.prompt, "the Grok ground-truth guard still rides"
    assert "Master prompt derived" in grok.reply
    assert "8s you booked fits" in grok.reply
    assert session.master_for_op(grok.op.id) is not None

    flow = session.propose(sid, "/filmic a woman dances", provider="google-flow", duration_s=8)
    assert "Create a" in flow.op.prompt and "The clip ends with" in flow.op.prompt
    assert "/filmic" not in flow.op.prompt, "inline shorthand is consumed, never shipped raw"
    assert "No morphing, no warped anatomy" in flow.op.prompt


def test_one_shot_camera_lock_hardening_applies_to_the_master_body(monkeypatch) -> None:
    monkeypatch.setattr(
        session.master_mod,
        "derive",
        lambda idea, **kw: master_prompt.optimize(
            master_prompt._template(idea, kw["target_duration_s"], False, {"camera": "steady camera on her"})
        ),
    )
    sid = session.create_session()
    op = session.propose(sid, "a woman waits", provider="grok", duration_s=6).op
    assert "steady camera" not in op.prompt.lower()
    assert "locked camera" in op.prompt.lower()


def test_duration_control_names_the_gap_between_derived_and_booked(monkeypatch) -> None:
    monkeypatch.setattr(
        session.master_mod,
        "derive",
        lambda idea, **kw: master_prompt.optimize(
            master_prompt._template(idea, kw["target_duration_s"], False, None).model_copy(
                update={"estimated_duration_s": 12}
            )
        ),
    )
    sid = session.create_session()
    short = session.propose(sid, "a long chase", provider="grok", duration_s=6)
    assert "plays about 12s, more than the 6s booked" in short.reply
    assert "set Duration to 12s" in short.reply
    assert short.op.duration_s == 6, "the user's booking stands — the note is advice"


def test_duration_note_flags_the_one_shot_cap() -> None:
    master = master_prompt.derive("a marathon", target_duration_s=15).model_copy(
        update={"estimated_duration_s": 22}
    )
    note = master_prompt.duration_note(master, 15, cap_s=15)
    assert "past the 15s one-shot cap" in note and "Draft a script" in note
    shorter = master.model_copy(update={"estimated_duration_s": 4})
    assert "less than the 15s booked" in master_prompt.duration_note(shorter, 15)


def test_one_shot_holds_as_deriving_while_the_brain_thinks(monkeypatch) -> None:
    import threading

    monkeypatch.setenv("DIONY_DIRECTOR_BRAIN_URL", "http://127.0.0.1:9999")
    gate = threading.Event()

    def fake_post(url, **kwargs):
        gate.wait(timeout=10)
        return _Resp(master_brain_body())

    monkeypatch.setattr(screenwriter.httpx, "post", fake_post)
    sid = session.create_session()
    proposal = session.propose(sid, "a keeper opens a door", provider="grok", duration_s=8)
    op = proposal.op
    assert op.status == "deriving"
    assert op.prompt_version == "v0-provisional"
    assert "a keeper opens a door" in op.prompt, "the provisional prompt is the honest raw idea"
    assert "Deriving the master prompt" in proposal.reply
    assert op.estimate is not None, "priced now — the hold is on the prompt, not the money"
    gate.set()
    for _ in range(100):
        if op.status != "deriving":
            break
        time.sleep(0.05)
    assert op.status == "proposed"
    assert op.prompt_version == "v1"
    assert "lighthouse keeper" in op.prompt
    assert "The clip ends with the keeper in the open doorway" in op.prompt
    assert "Master prompt derived" in op.rationale
    assert session.master_for_op(op.id) is not None


def test_a_deriving_op_cannot_run_and_a_failed_derivation_is_named(monkeypatch) -> None:
    import threading

    from fastapi.testclient import TestClient

    from diony_director import app as app_module

    monkeypatch.setenv("DIONY_DIRECTOR_BRAIN_URL", "http://127.0.0.1:9999")
    gate = threading.Event()

    def fake_post(url, **kwargs):
        gate.wait(timeout=10)
        raise RuntimeError("brain melted")

    monkeypatch.setattr(screenwriter.httpx, "post", fake_post)
    client = TestClient(app_module.app)
    sid = client.post("/api/director/sessions").json()["session_id"]
    body = client.post(
        f"/api/director/sessions/{sid}/message",
        json={"text": "a keeper opens a door", "provider": "grok", "duration_s": 8},
    ).json()
    op_id = body["op"]["id"]
    assert body["op"]["status"] == "deriving"
    run = client.post(f"/api/director/ops/{op_id}/run")
    assert run.status_code == 409 and "deriving" in run.json()["detail"]
    gate.set()
    for _ in range(100):
        state = client.get(f"/api/director/ops/{op_id}").json()
        if state["op"]["status"] != "deriving":
            break
        time.sleep(0.05)
    assert state["op"]["status"] == "proposed"
    assert state["op"]["prompt_version"] == "v0-provisional"
    assert "master derivation failed" in state["op"]["rationale"]
    assert "brain melted" in state["op"]["rationale"]
    assert state["master"] is None


# --- scripted: the screenplay realizes the artifact, every clip inherits it ---


def test_the_draft_carries_the_master_and_the_screenplay_realizes_it() -> None:
    sid = session.create_session()
    draft, reply = session.start_production(sid, "a keeper hears a knock at the lighthouse", 45)
    assert draft.master is not None
    assert draft.master.target_duration_s == 45
    assert "Master prompt derived" in reply
    state = session.draft_state(sid)
    assert state["master"]["prompt"].startswith("Create a")
    assert state["stage"] == "ready"
    # the ending is the last image on screen: the final scene's closing beat
    last_scene = draft.script.scenes[-1]
    last_line = draft.script.line(last_scene.line_ids[-1])
    assert draft.master.ending.split(",")[0] in last_line.text.lower()
    # the artifact's lighting names the setting's light
    assert draft.settings[0].time_weather.endswith(draft.master.lighting["source"])


def test_the_master_brief_reaches_the_screenplay_brain(monkeypatch) -> None:
    monkeypatch.setenv("DIONY_DIRECTOR_BRAIN_URL", "http://127.0.0.1:9999")
    monkeypatch.setenv("DIONY_DIRECTOR_BRAIN_REFINE", "0")
    seen: list[dict] = []
    screenplay = {
        "logline": "A keeper answers.",
        "beats": [{"beat": "the knock", "story_time": "Night 1"}],
        "settings": [{"setting_id": "set-lh", "name": "Lighthouse", "kind": "INT", "room_tone": "wind",
                      "time_weather": "night", "grade_language": "brass", "camera_kit": "32mm"}],
        "characters": [{"char_id": "chr-el", "name": "ELIAS", "wardrobe": "grey peacoat",
                        "age_body_notes": "sixty, weathered"}],
        "scenes": [{"heading": "INT. LIGHTHOUSE - NIGHT", "setting_ref": "set-lh", "story_time": "Night 1",
                    "cast": ["chr-el"], "emotional_job": "dread",
                    "lines": [{"kind": "action", "text": "ELIAS turns to the door.", "shot": "Wide, locked"},
                              {"kind": "sound", "text": "Three knocks."}]}],
    }

    def fake_post(url, **kwargs):
        seen.append(kwargs["json"])
        return _Resp(master_brain_body() if is_master_call(kwargs["json"]) else screenplay)

    monkeypatch.setattr(screenwriter.httpx, "post", fake_post)
    sid = session.create_session()
    session.begin_draft(sid, "a keeper hears a knock", 30)
    for _ in range(100):
        if session.draft_state(sid)["status"] in {"ready", "failed"}:
            break
        time.sleep(0.05)
    state = session.draft_state(sid)
    assert state["status"] == "ready", state["error"]
    assert [is_master_call(j) for j in seen] == [True, False], "master first, then the screenplay"
    brief = seen[1]["messages"][1]["content"]
    assert "MASTER PROMPT — the pre-split finalized artifact" in brief
    assert "Ending: the keeper in the open doorway" in brief
    assert "Continuity anchors: same peacoat" in brief
    assert state["master"]["source"] == "brain"


def test_every_split_clip_inherits_the_artifact_and_only_the_last_ends_it() -> None:
    sid = session.create_session()
    session.start_production(sid, "a keeper hears a knock at the lighthouse", 45)
    package, ops, _bible, _reply = session.lock_script(sid)
    assert package.master_prompt is not None
    master = package.master_prompt
    prompts = [clip.latest_prompt().prompt for clip in package.clips]
    assert len(prompts) >= 3
    for prompt in prompts:
        assert "Maintain strict continuity throughout: " + ", ".join(master.continuity_anchors) in prompt
        assert "Constraints: " + "; ".join(master.constraints) in prompt
        assert f"Lighting comes from {master.lighting['source']}" in prompt
    enders = [p for p in prompts if "The clip ends with" in p]
    assert enders == [prompts[-1]], "the ending lands on the last clip alone"
    # the money path sees the same prompts
    assert ops[-1].prompt == prompts[-1]


def test_the_artifact_is_route_neutral_across_dialects() -> None:
    from diony_contracts.cluster import write_engine_choice

    write_engine_choice("google-flow")
    sid = session.create_session()
    session.start_production(sid, "two friends argue in a diner, then laugh", 40)
    package, _ops, _b, _r = session.lock_script(sid)
    providers = {clip.provider for clip in package.clips}
    assert providers == {CloudProvider.GOOGLE_FLOW}
    # the Flow policy books one dialect now — omni-flash across the board
    dialects = {clip.model for clip in package.clips}
    assert dialects == {"omni-flash"}
    anchors = "Maintain strict continuity throughout: " + ", ".join(package.master_prompt.continuity_anchors)
    for clip in package.clips:
        assert anchors in clip.latest_prompt().prompt


def test_a_package_without_a_master_compiles_exactly_as_before() -> None:
    from diony_director import packagebuilder

    draft = screenwriter.write_script("a keeper hears a knock at the lighthouse", runtime_target_s=45)
    assert draft.master is None
    package = packagebuilder.build_package("s-none", "idea", draft)
    assert package.master_prompt is None
    prompt = compiler.compile_clip_prompt(package.clips[0], package).prompt
    assert "Maintain strict continuity throughout" not in prompt
    assert "Constraints:" not in prompt


def test_prompts_with_the_artifact_still_fit_every_provider_limit() -> None:
    from diony_director.providers.base import prompt_limit

    sid = session.create_session()
    session.start_production(sid, "a heist across a sleeping city " * 40, 120)
    package, ops, _b, _r = session.lock_script(sid)
    for op in ops:
        assert len(op.prompt) <= prompt_limit(op.provider.value)
        assert "No morphing" in op.prompt, "priority-0 guards survive the trim"


# --- persistence ---------------------------------------------------------------


def test_the_artifact_survives_state_json_for_drafts_and_one_shots() -> None:
    sid = session.create_session()
    session.start_production(sid, "a keeper hears a knock", 45)
    op = session.propose(sid, "a woman dances", provider="grok", duration_s=6).op
    draft_master = session.master_for_session(sid)
    op_master = session.master_for_op(op.id)
    assert draft_master is not None and op_master is not None
    session.persist(sid)
    session.reset()
    assert session.rehydrate_from_vault() == 1
    assert session.master_for_session(sid).prompt == draft_master.prompt
    assert session.master_for_op(op.id).prompt == op_master.prompt
    assert session.draft_state(sid)["master"]["prompt"] == draft_master.prompt


def test_a_restart_releases_a_deriving_op_with_its_provisional_prompt_named() -> None:
    from diony_contracts.director import DirectorOp

    sid = session.create_session()
    op = session.propose(sid, "a woman dances", provider="grok", duration_s=6).op
    op.status = "deriving"
    op.prompt_version = "v0-provisional"
    session.persist(sid)
    session.reset()
    session.rehydrate_from_vault()
    restored = session.ops_for(sid)[0]
    assert isinstance(restored, DirectorOp)
    assert restored.status == "proposed"
    assert "interrupted by a restart" in restored.rationale


# --- API surfaces ----------------------------------------------------------------


def test_the_master_endpoints_and_the_board_expose_the_artifact() -> None:
    from fastapi.testclient import TestClient

    from diony_director import app as app_module

    client = TestClient(app_module.app)
    template = client.get("/api/director/master/template").json()
    assert template["template"].startswith("MASTER AI VIDEO PROMPT TEMPLATE")
    assert "TARGET DURATION" in template["duration_section"]
    sid = client.post("/api/director/sessions").json()["session_id"]
    assert client.get(f"/api/director/sessions/{sid}/master").status_code == 404
    client.post(f"/api/director/sessions/{sid}/produce", json={"idea": "a keeper", "runtime_s": 45})
    master = client.get(f"/api/director/sessions/{sid}/master").json()["master"]
    assert master["prompt"].startswith("Create a")
    assert master["target_duration_s"] == 45
    board = client.get(f"/api/director/sessions/{sid}/board").json()
    assert board["master"]["prompt"] == master["prompt"]
    assert board["draft_stage"] == "ready"
    one = client.post(
        f"/api/director/sessions/{sid}/message",
        json={"text": "a woman dances", "provider": "grok", "duration_s": 6},
    ).json()
    state = client.get(f"/api/director/ops/{one['op']['id']}").json()
    assert state["master"]["target_duration_s"] == 6
    assert state["op"]["prompt"].startswith(state["master"]["prompt"][:40])


# --- review pass: edge cases -----------------------------------------------------


def test_the_anchor_reads_as_a_sentence_for_any_idea_shape() -> None:
    lead = master_prompt.derive("A man walks into the sea!", target_duration_s=6)
    assert lead.prompt.startswith("Create a cinematic realism video of a man walks into the sea.")
    named = master_prompt.derive("Mara walks into the sea", target_duration_s=6)
    assert "video of Mara walks" in named.prompt
    camera = master_prompt.derive("x", target_duration_s=6, authored={"camera": "The camera orbits her."})
    assert "The camera orbits her, maintaining" in camera.prompt
    assert "camera the camera" not in camera.prompt.lower()


def test_the_flow_world_is_not_pasted_into_the_style_slot() -> None:
    master = master_prompt.derive(
        "a woman waits", target_duration_s=6, authored={"world": "Shot on 35mm film, soft halation"}
    )
    assert master.prompt.startswith("Create a cinematic realism video of a woman waits")
    assert "Shot on 35mm" not in master.prompt, "the compiler owns the Flow style slot"
    grok = master_prompt.derive(
        "a woman waits", target_duration_s=6, authored={"atmosphere": "Dust drifts through the light."}
    )
    assert ", dust drifts through the light." in grok.prompt


def test_the_optimizer_normalizes_any_ending_lead_and_never_doubles_it() -> None:
    base = master_prompt.derive("a keeper", target_duration_s=8)
    variant = base.model_copy(
        update={"prompt": "Create a video of a keeper. The video ends on the keeper in the doorway.", "warnings": []}
    )
    out = master_prompt.optimize(variant)
    assert out.prompt.count("ends") == 1
    assert "The clip ends with the keeper in the doorway." in out.prompt
    assert any("normalized the ending" in w for w in out.warnings)


def test_the_optimizer_is_idempotent_on_warnings_too() -> None:
    master = master_prompt.derive("a keeper hears three knocks " * 12, target_duration_s=8)
    once = list(master.warnings)
    again = master_prompt.optimize(master).warnings
    assert again == once, "a re-run replaces its own warnings instead of repeating them"


def test_a_brain_that_paced_to_the_target_needs_no_second_pacing_line() -> None:
    base = master_prompt.derive("a keeper", target_duration_s=8)
    own = base.model_copy(update={"prompt": "Create a video of a keeper. The whole move plays in 8 seconds.", "warnings": []})
    out = master_prompt.optimize(own)
    assert "Pace the action" not in out.prompt
    other = base.model_copy(update={"prompt": "Create a video of a keeper. The whole move plays in 12 seconds.", "warnings": []})
    out2 = master_prompt.optimize(other)
    assert "Pace the action to play in about 8 seconds." in out2.prompt
    assert any("also mentions 12 seconds" in w for w in out2.warnings)


def test_master_lighting_yields_to_scene_light_in_a_multi_setting_film() -> None:
    sid = session.create_session()
    session.start_production(sid, "a heist across a sleeping city and a desert road", 90)
    package, _ops, _b, _r = session.lock_script(sid)
    assert len({s.setting_ref for s in package.script.scenes}) > 1
    source = package.master_prompt.lighting["source"]
    for clip in package.clips:
        prompt = clip.latest_prompt().prompt
        assert f"Lighting comes from {source}" not in prompt
        assert "Maintain strict continuity throughout" in prompt, "anchors are film-wide law"


def test_a_one_shot_room_still_answers_the_master_endpoint() -> None:
    from fastapi.testclient import TestClient

    from diony_director import app as app_module

    client = TestClient(app_module.app)
    sid = client.post("/api/director/sessions").json()["session_id"]
    client.post(f"/api/director/sessions/{sid}/message", json={"text": "a woman dances", "provider": "grok", "duration_s": 6})
    client.post(f"/api/director/sessions/{sid}/message", json={"text": "a man waits", "provider": "grok", "duration_s": 4})
    master = client.get(f"/api/director/sessions/{sid}/master").json()["master"]
    assert master["idea"] == "a man waits", "the newest shot's artifact"
