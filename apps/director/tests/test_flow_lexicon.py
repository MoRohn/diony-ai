"""Flow lexicon templating: WORLD + CAMERA doctrine on the Google route only.

The 22-command cheat sheet distilled: one visual world fills the Veo style
slot, camera shorthand expands inline, and none of it ever touches a Grok
prompt — /tokens on Grok stay literal text.
"""

import json

import pytest

from diony_contracts.director import CloudProvider
from diony_director import compiler, flow_lexicon, packagebuilder, screenwriter, session


@pytest.fixture(autouse=True)
def _clean_state():
    session.reset()
    yield
    session.reset()


def _package(sid: str, policy: str):
    draft = screenwriter.write_script(
        "a getaway driver decides to leave town from a neon diner", runtime_target_s=60
    )
    return packagebuilder.build_package(sid, "a getaway driver leaves town", draft, provider_policy=policy)


# --- expansion ---------------------------------------------------------------


def test_camera_shorthand_expands_inline() -> None:
    text, world, has_cam = flow_lexicon.expand("/orbit: around the hero at dawn")
    assert "180-degree orbit" in text
    assert "/orbit" not in text
    assert has_cam and world == ""


def test_world_token_is_extracted_not_inlined() -> None:
    text, world, has_cam = flow_lexicon.expand("/cyberpunk: she crosses the market")
    assert world == "cyberpunk"
    assert "neon" not in text, "a world is a style, not shot prose"
    assert text == "she crosses the market"
    assert not has_cam


def test_paired_shorthand_splits_into_both_slots() -> None:
    text, world, has_cam = flow_lexicon.expand("/filmic + /dollyin: she opens the case")
    assert world == "filmic" and has_cam
    assert "dolly-in" in text and "+" not in text


def test_unknown_tokens_pass_through_untouched() -> None:
    text, world, has_cam = flow_lexicon.expand("/madeupthing: she waits")
    assert "/madeupthing" in text and world == "" and not has_cam


def test_last_world_wins_one_world_per_clip() -> None:
    _, world, _ = flow_lexicon.expand("/filmic /epic: the horizon breaks")
    assert world == "epic"


def test_mid_word_slashes_are_prose_not_tokens() -> None:
    text, world, has_cam = flow_lexicon.expand("a pan/tracking hybrid through day/night cycles")
    assert text == "a pan/tracking hybrid through day/night cycles"
    assert world == "" and not has_cam
    assert flow_lexicon.unknown_tokens("day/night w/ neon and/or rain") == []


def test_tokens_match_case_insensitively() -> None:
    text, _, has_cam = flow_lexicon.expand("/Closeup: on her hands")
    assert has_cam and "micro-expression" in text and "/Closeup" not in text


def test_world_extraction_leaves_no_punctuation_debris() -> None:
    text, world, _ = flow_lexicon.expand("/dreamy, she waits by the water")
    assert world == "dreamy" and text == "she waits by the water"


def test_unknown_tokens_reports_likely_typos_once() -> None:
    assert flow_lexicon.unknown_tokens("/closup: then /closup again /orbit") == ["/closup"]


# --- session template --------------------------------------------------------


def test_template_defaults_to_baseline_world_and_no_camera() -> None:
    assert session.flow_template_for("s-x") == {"world": flow_lexicon.BASELINE_WORLD, "camera": ""}


def test_unknown_template_keys_are_refused() -> None:
    with pytest.raises(ValueError):
        session.set_flow_template("s-x", world="vaporwave")
    with pytest.raises(ValueError):
        session.set_flow_template("s-x", camera="crashzoom")


def test_template_survives_persist_and_rehydrate(tmp_path) -> None:
    sid = session.create_session()
    session.set_flow_template(sid, world="luxury", camera="dollyin")
    session.reset()
    assert session.flow_template_for(sid)["world"] == flow_lexicon.BASELINE_WORLD
    restored = session.rehydrate_from_vault()
    assert restored >= 1
    assert session.flow_template_for(sid) == {"world": "luxury", "camera": "dollyin"}


# --- compiled prompts: Google route ------------------------------------------


def _veo_picture(package):
    """The Veo prose dialect stays a tested capability behind an explicit
    model choice (the Flow policy now books omni-flash across the board) —
    force a picture clip onto it, on Veo's non-Ultra grid."""
    clip = next(
        c for c in package.clips if c.clip_class not in {"sync-dialogue", "vo-over"}
    )
    clip.model = "veo-3.1-fast"
    clip.duration_s = 8
    return clip


def test_veo_prompts_carry_the_session_world_clause() -> None:
    sid = "s-veo-world"
    session.set_flow_template(sid, world="cyberpunk")
    package = _package(sid, "google-flow")
    picture = _veo_picture(package)
    prompt = compiler.compile_clip_prompt(picture, package).prompt
    assert "Dense neon decay" in prompt
    assert "Shot on a cinema camera" not in prompt, "the world replaces the baseline, never stacks"


def test_veo_prompts_default_to_the_baseline_world() -> None:
    package = _package("s-veo-base", "google-flow")
    picture = _veo_picture(package)
    prompt = compiler.compile_clip_prompt(picture, package).prompt
    assert "Shot on a cinema camera" in prompt


def test_inline_world_on_the_shot_beats_the_session_template() -> None:
    sid = "s-veo-inline"
    session.set_flow_template(sid, world="cyberpunk")
    package = _package(sid, "google-flow")
    picture = _veo_picture(package)
    picture.camera_idea = "/dreamy: she drifts through the flooded arcade"
    prompt = compiler.compile_clip_prompt(picture, package).prompt
    assert "Soft emotional unreality" in prompt
    assert "Dense neon decay" not in prompt


def test_world_only_shot_never_resurrects_the_literal_token() -> None:
    package = _package("s-veo-worldonly", "google-flow")
    picture = _veo_picture(package)
    picture.camera_idea = "/dreamy"
    prompt = compiler.compile_clip_prompt(picture, package).prompt
    assert "/dreamy" not in prompt
    assert "Soft emotional unreality" in prompt
    assert " . " not in prompt and not prompt.startswith(".")


def test_camera_shorthand_expands_in_veo_shot_prose() -> None:
    package = _package("s-veo-cam", "google-flow")
    picture = _veo_picture(package)
    picture.camera_idea = "/closeup: on her eyes as the engine turns"
    prompt = compiler.compile_clip_prompt(picture, package).prompt
    assert "micro-expression" in prompt and "/closeup" not in prompt


def test_omni_dialogue_rides_the_same_world_as_the_pictures() -> None:
    sid = "s-omni-world"
    session.set_flow_template(sid, world="cyberpunk")
    package = _package(sid, "google-flow")
    dialogue = next(c for c in package.clips if c.model == "omni-flash")
    prompt = compiler.compile_clip_prompt(dialogue, package).prompt
    assert "Dense neon decay" in prompt, "spoken shots must hold the scene's look"


def test_omni_baseline_world_leaves_the_dialect_untouched() -> None:
    package = _package("s-omni-base", "google-flow")
    dialogue = next(c for c in package.clips if c.model == "omni-flash")
    prompt = compiler.compile_clip_prompt(dialogue, package).prompt
    assert "Shot on a cinema camera" not in prompt


def test_omni_shot_shorthand_expands_too() -> None:
    package = _package("s-omni-cam", "google-flow")
    dialogue = next(c for c in package.clips if c.model == "omni-flash")
    dialogue.camera_idea = "/closeup: as she answers"
    prompt = compiler.compile_clip_prompt(dialogue, package).prompt
    assert "micro-expression" in prompt and "/closeup" not in prompt


def test_mixed_package_keeps_the_world_off_omni_dialogue() -> None:
    """Auto policy: pictures on Grok, dialogue on omni. Styling only the
    spoken shots would break the scene's one-look doctrine — the session
    world stays off a mixed package's dialogue clips."""
    sid = "s-mixed"
    session.set_flow_template(sid, world="cyberpunk")
    package = _package(sid, "auto")
    assert any(c.provider is CloudProvider.GROK for c in package.clips)
    dialogue = next(c for c in package.clips if c.model == "omni-flash")
    prompt = compiler.compile_clip_prompt(dialogue, package).prompt
    assert "Dense neon decay" not in prompt


def test_inline_world_is_authored_law_even_on_a_mixed_package() -> None:
    package = _package("s-mixed-inline", "auto")
    dialogue = next(c for c in package.clips if c.model == "omni-flash")
    dialogue.camera_idea = "/dreamy: close on her face"
    prompt = compiler.compile_clip_prompt(dialogue, package).prompt
    assert "Soft emotional unreality" in prompt


# --- the Grok route never speaks the lexicon ---------------------------------


def test_grok_prompts_ignore_the_template_and_keep_realism() -> None:
    sid = "s-grok-safe"
    session.set_flow_template(sid, world="cyberpunk", camera="orbit")
    package = _package(sid, "grok")
    picture = next(c for c in package.clips if c.provider is CloudProvider.GROK)
    prompt = compiler.compile_clip_prompt(picture, package).prompt
    assert "Dense neon decay" not in prompt and "180-degree orbit" not in prompt
    assert compiler.REALISM in prompt


def test_grok_shot_speaks_the_grok_dialect_never_the_flow_one() -> None:
    """/orbit is route-dialect-aware: on Grok it expands with the Grok
    guide's phrasing, and the Flow clause never leaks across."""
    package = _package("s-grok-lit", "grok")
    picture = next(c for c in package.clips if c.provider is CloudProvider.GROK)
    picture.camera_idea = "/orbit: around the hero"
    prompt = compiler.compile_clip_prompt(picture, package).prompt
    assert "camera orbits the subject" in prompt
    assert "180-degree orbit" not in prompt, "the Flow clause stays on the Flow lane"


def test_flow_only_tokens_stay_literal_on_grok() -> None:
    package = _package("s-grok-flowtok", "grok")
    picture = next(c for c in package.clips if c.provider is CloudProvider.GROK)
    picture.camera_idea = "/filmic /dollyin: toward her face"
    prompt = compiler.compile_clip_prompt(picture, package).prompt
    assert "/filmic" in prompt and "/dollyin" in prompt
    assert "authentic film grain" not in prompt and "dolly-in" not in prompt


# --- chat lane (single-shot proposals) ---------------------------------------


def test_flow_chat_prompt_carries_world_and_session_camera() -> None:
    sid = "s-chat-flow"
    session.set_flow_template(sid, world="minimal", camera="dollyout")
    prompt = session.compile_prompt("a lone tree on a causeway", CloudProvider.GOOGLE_FLOW, sid)
    assert "One decisive idea" in prompt and "dolly-out" in prompt


def test_flow_chat_inline_camera_suppresses_the_template_camera() -> None:
    sid = "s-chat-inline"
    session.set_flow_template(sid, camera="dollyout")
    prompt = session.compile_prompt("/orbit: the silver case on the table", CloudProvider.GOOGLE_FLOW, sid)
    assert "180-degree orbit" in prompt
    assert "dolly-out" not in prompt, "one camera idea per clip"


def test_grok_chat_prompt_is_untouched_by_the_template() -> None:
    sid = "s-chat-grok"
    session.set_flow_template(sid, world="epic", camera="droneview")
    prompt = session.compile_prompt("a lone tree on a causeway", CloudProvider.GROK, sid)
    assert "Blockbuster scale" not in prompt and "aerial view" not in prompt
    assert "Cinematic single shot" in prompt


# --- custom lexicon file -----------------------------------------------------


def test_custom_lexicon_entries_merge_over_builtins(tmp_path) -> None:
    from diony_contracts.cluster import cluster_root

    custom = cluster_root() / ".cluster" / "flow_lexicon.json"
    custom.parent.mkdir(parents=True, exist_ok=True)
    custom.write_text(
        json.dumps(
            {
                "worlds": {"housestyle": {"label": "House", "clause": "The house look: warm rim light."}},
                "cameras": {"crashzoom": {"label": "Crash zoom", "clause": "Sudden crash zoom to the face"}},
            }
        )
    )
    assert "housestyle" in flow_lexicon.worlds()
    session.set_flow_template("s-custom", world="housestyle", camera="crashzoom")
    prompt = session.compile_prompt("she turns", CloudProvider.GOOGLE_FLOW, "s-custom")
    assert "house look" in prompt and "crash zoom" in prompt.lower()


def test_corrupt_custom_lexicon_never_breaks_the_builtins(tmp_path) -> None:
    from diony_contracts.cluster import cluster_root

    custom = cluster_root() / ".cluster" / "flow_lexicon.json"
    custom.parent.mkdir(parents=True, exist_ok=True)
    custom.write_text("{not json")
    assert set(flow_lexicon.WORLDS) <= set(flow_lexicon.worlds())


def test_custom_key_colliding_with_the_other_family_is_dropped() -> None:
    from diony_contracts.cluster import cluster_root

    custom = cluster_root() / ".cluster" / "flow_lexicon.json"
    custom.parent.mkdir(parents=True, exist_ok=True)
    custom.write_text(
        json.dumps({"worlds": {"orbit": {"label": "Orbit world", "clause": "confusing"}}})
    )
    assert flow_lexicon.worlds().get("orbit") is None, "a camera key cannot double as a world"
    assert "orbit" in flow_lexicon.cameras()


def test_a_vanished_custom_world_falls_back_to_baseline_not_a_crash() -> None:
    from diony_contracts.cluster import cluster_root

    custom = cluster_root() / ".cluster" / "flow_lexicon.json"
    custom.parent.mkdir(parents=True, exist_ok=True)
    custom.write_text(
        json.dumps({"worlds": {"housestyle": {"label": "House", "clause": "The house look."}}})
    )
    sid = "s-vanish"
    session.set_flow_template(sid, world="housestyle")
    custom.unlink()
    package = _package(sid, "google-flow")
    picture = _veo_picture(package)
    prompt = compiler.compile_clip_prompt(picture, package).prompt
    assert "Shot on a cinema camera" in prompt, "compile survives on the baseline clause"
    dialogue = next(c for c in package.clips if c.model == "omni-flash")
    assert compiler.compile_clip_prompt(dialogue, package).prompt


def test_an_authored_drone_opener_books_the_still_plate_recipe() -> None:
    draft = screenwriter.write_script(
        "a getaway driver decides to leave town from a neon diner", runtime_target_s=60
    )
    first_action = next(l for l in draft.script.lines if l.kind == "action")
    first_action.shot = "/droneview: over the flooded boulevard at dawn"
    package = packagebuilder.build_package(
        "s-drone", "a getaway", draft, provider_policy="google-flow"
    )
    opener = next(c for c in package.clips if c.clip_id == "cl-1-1")
    assert opener.continuity.lock_recipe == "still"
    prompt = compiler.compile_clip_prompt(opener, package).prompt
    assert "aerial" in prompt.lower() and "/droneview" not in prompt


# --- HTTP surface ------------------------------------------------------------


def test_lexicon_catalog_endpoint_serves_worlds_and_cameras() -> None:
    from fastapi.testclient import TestClient

    from diony_director.app import app

    client = TestClient(app)
    body = client.get("/api/director/flow/lexicon").json()
    keys = {w["key"] for w in body["worlds"]}
    assert {"filmic", "cyberpunk", "epic", flow_lexicon.BASELINE_WORLD} <= keys
    assert {c["key"] for c in body["cameras"]} >= {"orbit", "dollyin", "timelapse"}
    assert body["google_route_only"] is True
    assert len(body["pairings"]) == 6


def test_template_locks_with_the_script() -> None:
    from fastapi.testclient import TestClient

    from diony_director.app import app

    client = TestClient(app)
    sid = client.post("/api/director/sessions").json()["session_id"]
    res = client.post(
        f"/api/director/sessions/{sid}/produce",
        json={"idea": "a getaway driver leaves town", "flow_world": "filmic"},
    )
    assert res.status_code == 200
    assert session.flow_template_for(sid)["world"] == "filmic"
    client.post(f"/api/director/sessions/{sid}/lock_script")
    res = client.post(
        f"/api/director/sessions/{sid}/message",
        json={"text": "one more shot", "flow_world": "epic"},
    )
    assert res.status_code == 409
    assert "locked" in res.json()["detail"]


def test_omitted_flow_fields_keep_the_template_and_never_409() -> None:
    """A client that doesn't speak the flow fields must neither clear a
    chosen template nor trip the post-lock guard on an innocent message."""
    from fastapi.testclient import TestClient

    from diony_director.app import app

    client = TestClient(app)
    sid = client.post("/api/director/sessions").json()["session_id"]
    client.post(
        f"/api/director/sessions/{sid}/produce",
        json={
            "idea": "a getaway driver leaves town",
            "flow_world": "filmic",
            "flow_camera": "dollyin",
        },
    )
    client.post(f"/api/director/sessions/{sid}/lock_script")
    res = client.post(
        f"/api/director/sessions/{sid}/message", json={"text": "one bonus shot"}
    )
    assert res.status_code == 200
    assert session.flow_template_for(sid) == {"world": "filmic", "camera": "dollyin"}


def test_typoed_shorthand_warns_in_the_shot_edit_and_the_proposal() -> None:
    from fastapi.testclient import TestClient

    from diony_director.app import app

    client = TestClient(app)
    sid = client.post("/api/director/sessions").json()["session_id"]
    client.post(
        f"/api/director/sessions/{sid}/produce",
        json={"idea": "a getaway driver decides to leave town from a neon diner"},
    )
    draft = client.get(f"/api/director/sessions/{sid}/draft").json()
    action = next(l for l in draft["script"]["lines"] if l["kind"] == "action")
    res = client.post(
        f"/api/director/sessions/{sid}/draft/line",
        json={"line_id": action["line_id"], "shot": "/closup: on the keys"},
    )
    assert res.status_code == 200, "a typo warns, it never blocks the edit"
    assert res.json()["shot_warnings"] == ["/closup"]

    proposal = session.propose(sid, "/closup: she waits", provider="google-flow")
    assert "/closup" in proposal.reply and "literal" in proposal.reply


def test_advisories_flag_stacked_worlds_and_cameras() -> None:
    notes = flow_lexicon.advisories("/filmic /epic: the horizon breaks")
    assert notes and "/epic wins" in notes[0] and "/filmic" in notes[0]
    assert flow_lexicon.advisories("/filmic: she crosses the market") == []
    assert flow_lexicon.advisories("no shorthand at all") == []
    notes = flow_lexicon.advisories("/orbit /dollyin: at her face")
    assert any("camera behaviors" in n for n in notes)


def test_stacked_worlds_warn_in_the_flow_proposal() -> None:
    sid = session.create_session()
    proposal = session.propose(
        sid, "/filmic /epic: the horizon breaks", provider="google-flow"
    )
    assert "/epic wins" in proposal.reply and "/filmic" in proposal.reply


def test_adjacent_camera_tokens_join_with_a_comma_not_a_run_on() -> None:
    text, _, has_cam = flow_lexicon.expand("/closeup /orbit: she turns")
    assert has_cam
    assert "bokeh, Smooth 180-degree orbit" in text, "a comma splits adjacent clauses"


def test_shot_lines_are_directable_until_the_lock() -> None:
    from fastapi.testclient import TestClient

    from diony_director.app import app

    client = TestClient(app)
    sid = client.post("/api/director/sessions").json()["session_id"]
    client.post(
        f"/api/director/sessions/{sid}/produce",
        json={"idea": "a getaway driver decides to leave town from a neon diner"},
    )
    draft = client.get(f"/api/director/sessions/{sid}/draft").json()
    action = next(l for l in draft["script"]["lines"] if l["kind"] == "action")
    heading = next(l for l in draft["script"]["lines"] if l["kind"] == "heading")

    res = client.post(
        f"/api/director/sessions/{sid}/draft/line",
        json={"line_id": action["line_id"], "shot": "/closeup: on the keys in her hand"},
    )
    assert res.status_code == 200
    edited = next(l for l in res.json()["script"]["lines"] if l["line_id"] == action["line_id"])
    assert edited["shot"] == "/closeup: on the keys in her hand"

    res = client.post(
        f"/api/director/sessions/{sid}/draft/line",
        json={"line_id": heading["line_id"], "shot": "/orbit"},
    )
    assert res.status_code == 422, "a heading carries no shot"

    res = client.post(
        f"/api/director/sessions/{sid}/draft/line", json={"line_id": action["line_id"]}
    )
    assert res.status_code == 422, "an empty edit is a client error"


def test_malformed_custom_entries_never_break_a_compile() -> None:
    """A bare-string or non-string-clause entry is skipped, never a 500 —
    the module promises customization can't break boot or a compile."""
    from diony_contracts.cluster import cluster_root

    custom = cluster_root() / ".cluster" / "flow_lexicon.json"
    custom.parent.mkdir(parents=True, exist_ok=True)
    custom.write_text(
        json.dumps(
            {
                "worlds": {
                    "noir": "moody shadows",
                    "glass": {"clause": ["not", "text"]},
                    "good": {"label": "Good", "clause": "A clean custom look."},
                }
            }
        )
    )
    table = flow_lexicon.worlds()
    assert "noir" not in table and "glass" not in table
    assert "good" in table
    _, world, _ = flow_lexicon.expand("/good: she waits")
    assert world == "good"


def test_template_camera_yields_to_authored_camera_prose() -> None:
    sid = "s-cam-prose"
    session.set_flow_template(sid, camera="orbit")
    prompt = session.compile_prompt(
        "slow dolly in on a watch face", CloudProvider.GOOGLE_FLOW, sid
    )
    assert "180-degree orbit" not in prompt, "authored camera prose rules"
    prompt = session.compile_prompt(
        "a lone tree on a causeway", CloudProvider.GOOGLE_FLOW, sid
    )
    assert "180-degree orbit" in prompt


def test_settings_endpoint_404s_an_unknown_session() -> None:
    from fastapi.testclient import TestClient

    from diony_director.app import app

    client = TestClient(app)
    assert client.get("/api/director/sessions/s-nope/settings").status_code == 404
    sid = client.post("/api/director/sessions").json()["session_id"]
    assert client.get(f"/api/director/sessions/{sid}/settings").status_code == 200


def test_blocking_locks_arm_on_every_google_dialect() -> None:
    """The s-2e0a74644b regression class doesn't care which lane a clip
    rides — a back-to-camera / concealment cue arms the hard lock on the
    Veo and omni dialects exactly as it does on Grok."""
    package = _package("s-lock-all", "google-flow")
    picture = _veo_picture(package)
    picture.camera_idea = "Seen from behind, back to camera, hood drawn"
    prompt = compiler.compile_clip_prompt(picture, package).prompt
    assert "Blocking lock" in prompt and "never turns to face the camera" in prompt
    dialogue = next(c for c in package.clips if c.model == "omni-flash")
    dialogue.camera_idea = "Seen from behind, face hidden in the hood"
    prompt = compiler.compile_clip_prompt(dialogue, package).prompt
    assert "Blocking lock" in prompt and "face is never visible" in prompt


def test_restyle_allowed_after_a_finished_film_reproduces() -> None:
    """A stale package from a finished film is not a lock: once a re-produce
    writes a fresh unlocked draft, the Flow template may change again even
    though _PACKAGES[sid] still holds the old package."""
    from fastapi import HTTPException

    from diony_director.app import _plan_flow_template

    sid = session.create_session()
    # a locked draft over a package — template is frozen
    draft = screenwriter.write_script("a neon diner heist", runtime_target_s=30)
    session._DRAFTS[sid] = {"status": "ready", "draft": draft, "reply": "", "error": ""}
    session.lock_script(sid)
    assert session.script_locked(sid)
    with pytest.raises(HTTPException) as err:
        _plan_flow_template(sid, "luxury", None)
    assert err.value.status_code == 409

    # re-produce: a fresh unlocked draft replaces _DRAFTS, package lingers
    fresh = screenwriter.write_script("a different chase", runtime_target_s=30)
    session._DRAFTS[sid] = {"status": "ready", "draft": fresh, "reply": "", "error": ""}
    assert session.package_for(sid) is not None, "the old package still lingers"
    assert not session.script_locked(sid)
    commit = _plan_flow_template(sid, "luxury", None)  # no 409 this time
    assert commit is not None
    commit()
    assert session.flow_template_for(sid)["world"] == "luxury"


def test_a_rejected_message_never_half_mutates_program_settings() -> None:
    """aspect + a template change in one message where the template 409s must
    leave the aspect untouched — validation runs for all before any commits."""
    from fastapi import HTTPException

    from diony_director.app import MessageIn, _apply_program_settings

    sid = session.create_session()
    draft = screenwriter.write_script("a neon diner heist", runtime_target_s=30)
    session._DRAFTS[sid] = {"status": "ready", "draft": draft, "reply": "", "error": ""}
    session.lock_script(sid)  # templates now frozen; no footage yet, so aspect is free
    assert session.aspect_for(sid) == "16:9"
    body = MessageIn(text="roll it", aspect="9:16", grok_camera="pushin")
    with pytest.raises(HTTPException) as err:
        _apply_program_settings(sid, body)
    assert err.value.status_code == 409
    assert session.aspect_for(sid) == "16:9", "the aspect must NOT have committed"


def test_flow_style_clause_is_inviolable_under_length_pressure(monkeypatch) -> None:
    """The world/style clause is booked at priority 0 like the old _VEO_STYLE:
    a long idea shrinks, the cinematography never ships half-truncated."""
    from diony_contracts.director import CloudProvider

    session.set_flow_template("s-press", world="cyberpunk")
    monkeypatch.setattr("diony_director.compiler.prompt_limit", lambda p: 160)
    idea = "a courier " + "races past ruined arcades and flooded neon streets " * 6
    prompt = session.compile_prompt(idea, CloudProvider.GOOGLE_FLOW, "s-press")
    assert "Dense neon decay" in prompt, "the world clause survives length pressure whole"
