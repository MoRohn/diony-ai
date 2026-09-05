"""Grok lexicon templating: MOTION + CAMERA + ATMOSPHERE on the Grok route only.

The Grok Imagine 1.5 guide distilled: reliable camera phrasings expand from
/shorthand, unreliable lock wording is hardened, atmosphere is a session
signature, an opening still carries the i2v directive — and none of it ever
touches a Flow/Veo prompt.
"""

import json

import pytest

from diony_contracts.director import CloudProvider
from diony_director import compiler, grok_lexicon, packagebuilder, screenwriter, session


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


# --- expansion and hardening -------------------------------------------------


def test_camera_shorthand_expands_with_the_guide_phrasing() -> None:
    text, camera_seen, _ = grok_lexicon.expand("/pushin: on her hands")
    assert "camera slowly pushes in" in text and "/pushin" not in text
    assert camera_seen


def test_atmosphere_shorthand_expands_inline() -> None:
    text, _, atmo_seen = grok_lexicon.expand("she waits, /dust in the backlight")
    assert "dust particles swirl around the subject" in text
    assert atmo_seen


def test_unknown_and_mid_word_tokens_pass_through() -> None:
    text, camera_seen, atmo_seen = grok_lexicon.expand("day/night cycle with /madeup: flair")
    assert "day/night" in text and "/madeup" in text
    assert not camera_seen and not atmo_seen
    assert grok_lexicon.unknown_tokens("day/night w/ rain") == []
    assert grok_lexicon.unknown_tokens("/madeup then /madeup /dust") == ["/madeup"]


def test_unreliable_camera_locks_are_hardened() -> None:
    assert grok_lexicon.harden_camera_locks("Stable camera on the door") == (
        "locked camera on the door"
    )
    assert grok_lexicon.harden_camera_locks("a steady shot holds") == (
        "a locked camera shot holds"
    ), "the rewrite stays grammatical — a noun phrase becomes a noun phrase"
    assert grok_lexicon.harden_camera_locks("the camera holds steady") == (
        "the camera not moving"
    )
    assert (
        grok_lexicon.harden_camera_locks("camera not moving") == "camera not moving"
    ), "the reliable form is left alone"


def test_locked_token_uses_the_reliable_negative_form() -> None:
    text, _, _ = grok_lexicon.expand("/locked: on the doorway")
    assert "camera not moving" in text


def test_adjacent_tokens_join_with_a_comma_not_a_run_on() -> None:
    text, camera_seen, atmo_seen = grok_lexicon.expand("/locked /dust: on the diner door")
    assert "camera not moving, dust particles swirl around the subject on the diner door" == text
    assert camera_seen and atmo_seen


def test_prose_continuation_after_a_token_stays_seamless() -> None:
    text, camera_seen, _ = grok_lexicon.expand("/pushin: on her hands")
    assert text == "camera slowly pushes in on her hands", "no comma splits a continuation"
    assert camera_seen


# --- session template --------------------------------------------------------


def test_template_defaults_to_empty() -> None:
    assert session.grok_template_for("s-x") == {"camera": "", "atmosphere": ""}


def test_unknown_template_keys_are_refused() -> None:
    with pytest.raises(ValueError):
        session.set_grok_template("s-x", camera="crashzoom")
    with pytest.raises(ValueError):
        session.set_grok_template("s-x", atmosphere="blizzard")


def test_template_survives_persist_and_rehydrate(tmp_path) -> None:
    sid = session.create_session()
    session.set_grok_template(sid, camera="orbit", atmosphere="dust")
    session.reset()
    assert session.grok_template_for(sid) == {"camera": "", "atmosphere": ""}
    assert session.rehydrate_from_vault() >= 1
    assert session.grok_template_for(sid) == {"camera": "orbit", "atmosphere": "dust"}


# --- compiled prompts: Grok route --------------------------------------------


def test_session_atmosphere_rides_grok_picture_clips() -> None:
    sid = "s-grok-atmo"
    session.set_grok_template(sid, atmosphere="dust")
    package = _package(sid, "grok")
    picture = next(c for c in package.clips if c.provider is CloudProvider.GROK)
    prompt = compiler.compile_clip_prompt(picture, package).prompt
    assert "Dust particles swirl around the subject" in prompt
    assert compiler.REALISM in prompt, "the house ground truth stays"


def test_inline_atmosphere_suppresses_the_session_one() -> None:
    sid = "s-grok-atmo-inline"
    session.set_grok_template(sid, atmosphere="dust")
    package = _package(sid, "grok")
    picture = next(c for c in package.clips if c.provider is CloudProvider.GROK)
    picture.camera_idea = "/rain over the intersection"
    prompt = compiler.compile_clip_prompt(picture, package).prompt
    assert "rain falls in the foreground" in prompt
    assert "dust particles" not in prompt.lower(), "atmosphere never stacks over an inline choice"


def test_mixed_package_keeps_the_atmosphere_off_grok_pictures() -> None:
    """Auto policy: pictures on Grok, dialogue on omni. An atmosphere the
    Flow lane can never carry would make the spoken shots mismatch the
    pictures — the session signature stays off a mixed package."""
    sid = "s-grok-mixed"
    session.set_grok_template(sid, atmosphere="rain")
    package = _package(sid, "auto")
    assert any(c.provider is not CloudProvider.GROK for c in package.clips)
    picture = next(c for c in package.clips if c.provider is CloudProvider.GROK)
    prompt = compiler.compile_clip_prompt(picture, package).prompt
    assert "Rain falls in the foreground" not in prompt


def test_inline_atmosphere_is_authored_law_even_on_a_mixed_package() -> None:
    package = _package("s-grok-mixed-inline", "auto")
    picture = next(c for c in package.clips if c.provider is CloudProvider.GROK)
    picture.camera_idea = "/dust over the intersection"
    prompt = compiler.compile_clip_prompt(picture, package).prompt
    assert "dust particles swirl around the subject" in prompt


def test_hardening_never_touches_innocent_steady_prose() -> None:
    assert grok_lexicon.harden_camera_locks("steady handheld pan across the room") == (
        "steady handheld pan across the room"
    ), "only the guide's unreliable lock phrases rewrite"


def test_an_expanded_clause_arms_the_blocking_lock() -> None:
    from diony_contracts.cluster import cluster_root

    custom = cluster_root() / ".cluster" / "grok_lexicon.json"
    custom.parent.mkdir(parents=True, exist_ok=True)
    custom.write_text(
        json.dumps(
            {"cameras": {"tailcam": {"label": "Tail cam", "clause": "camera follows from directly behind"}}}
        )
    )
    package = _package("s-grok-tail", "grok")
    picture = next(c for c in package.clips if c.provider is CloudProvider.GROK)
    picture.camera_idea = "/tailcam: as she walks away"
    prompt = compiler.compile_clip_prompt(picture, package).prompt
    assert "Blocking lock" in prompt and "never crosses the axis" in prompt


def test_advisories_flag_atmosphere_pileups_and_resolution_asks() -> None:
    assert grok_lexicon.advisories("/dust /rain /fog /smoke /wind: chaos")[0].startswith(
        "5 atmosphere elements"
    )
    notes = grok_lexicon.advisories("the horse rears in 4K ultra-hd")
    assert len(notes) == 1 and "4K" in notes[0]
    assert grok_lexicon.advisories("/dust /rain: fine") == []

    proposal = session.propose("s-adv", "the horse rears in 8k", provider="grok")
    assert "wastes the generation" in proposal.reply


def test_session_camera_never_forces_scripted_shots() -> None:
    sid = "s-grok-cam-scripted"
    session.set_grok_template(sid, camera="orbit")
    package = _package(sid, "grok")
    picture = next(c for c in package.clips if c.provider is CloudProvider.GROK)
    prompt = compiler.compile_clip_prompt(picture, package).prompt
    assert "camera orbits the subject" not in prompt, "authored shots rule — one camera idea per clip"


def test_authored_steady_shot_is_hardened_at_compile() -> None:
    package = _package("s-grok-harden", "grok")
    picture = next(c for c in package.clips if c.provider is CloudProvider.GROK)
    picture.camera_idea = "Steady shot on the diner door"
    prompt = compiler.compile_clip_prompt(picture, package).prompt
    assert "locked camera shot on the diner door" in prompt
    assert "steady shot" not in prompt.lower()


# --- the Flow route never speaks the Grok lexicon ----------------------------


def test_veo_prompts_ignore_the_grok_template_and_tokens() -> None:
    sid = "s-veo-safe"
    session.set_grok_template(sid, camera="orbit", atmosphere="dust")
    package = _package(sid, "google-flow")
    # the Veo dialect stays a tested capability behind an explicit model choice
    picture = next(
        c for c in package.clips if c.clip_class not in {"sync-dialogue", "vo-over"}
    )
    picture.model = "veo-3.1-fast"
    picture.duration_s = 8
    prompt = compiler.compile_clip_prompt(picture, package).prompt
    assert "dust particles swirl" not in prompt.lower()
    assert "camera orbits the subject slowly" not in prompt

    picture.camera_idea = "/pushin: toward the window"
    prompt = compiler.compile_clip_prompt(picture, package).prompt
    assert "/pushin" in prompt, "a Grok-only token stays literal on the Flow lane"


def test_flow_chat_prompt_is_untouched_by_the_grok_template() -> None:
    sid = "s-chat-flow-safe"
    session.set_grok_template(sid, camera="orbit", atmosphere="dust")
    prompt = session.compile_prompt("a lone tree on a causeway", CloudProvider.GOOGLE_FLOW, sid)
    assert "dust particles" not in prompt.lower() and "camera orbits" not in prompt


# --- chat lane (single-shot proposals) ---------------------------------------


def test_grok_chat_prompt_carries_camera_and_atmosphere() -> None:
    sid = "s-chat-grok"
    session.set_grok_template(sid, camera="pushin", atmosphere="wind")
    prompt = session.compile_prompt("she waits on the bench", CloudProvider.GROK, sid)
    assert prompt.startswith("she waits on the bench."), "motion leads — the model weighs early words most"
    assert "Camera slowly pushes in." in prompt
    assert "The wind moves hair and clothing." in prompt
    assert "Cinematic single shot" in prompt


def test_grok_chat_inline_tokens_suppress_the_template() -> None:
    sid = "s-chat-grok-inline"
    session.set_grok_template(sid, camera="pushin", atmosphere="wind")
    prompt = session.compile_prompt("/locked /rain: she waits", CloudProvider.GROK, sid)
    assert "camera not moving" in prompt and "rain falls in the foreground" in prompt
    assert "pushes in" not in prompt and "wind moves" not in prompt


def test_grok_chat_hardens_unreliable_lock_wording() -> None:
    prompt = session.compile_prompt("steady shot as the horse rears", CloudProvider.GROK, "s-h")
    assert "locked camera shot as the horse rears" in prompt
    assert "steady shot" not in prompt.lower()


def test_an_opening_still_adds_the_i2v_directive_on_grok_only() -> None:
    grok = session.compile_prompt("the horse rears", CloudProvider.GROK, "s-i", has_start_image=True)
    assert grok_lexicon.I2V_DIRECTIVE in grok
    bare = session.compile_prompt("the horse rears", CloudProvider.GROK, "s-i")
    assert grok_lexicon.I2V_DIRECTIVE not in bare
    flow = session.compile_prompt("the horse rears", CloudProvider.GOOGLE_FLOW, "s-i", has_start_image=True)
    assert grok_lexicon.I2V_DIRECTIVE not in flow


# --- custom lexicon file -----------------------------------------------------


def test_custom_entries_merge_and_cross_family_collisions_drop() -> None:
    from diony_contracts.cluster import cluster_root

    custom = cluster_root() / ".cluster" / "grok_lexicon.json"
    custom.parent.mkdir(parents=True, exist_ok=True)
    custom.write_text(
        json.dumps(
            {
                "cameras": {"crashzoom": {"label": "Crash zoom", "clause": "camera crash-zooms to the face"}},
                "atmospheres": {"pushin": {"label": "Bad", "clause": "confusing"}, "embers": {"label": "Embers", "clause": "embers drift upward through the frame"}},
            }
        )
    )
    assert "crashzoom" in grok_lexicon.cameras()
    assert "pushin" not in grok_lexicon.atmospheres(), "a camera key cannot double as an atmosphere"
    session.set_grok_template("s-custom", camera="crashzoom", atmosphere="embers")
    prompt = session.compile_prompt("she turns", CloudProvider.GROK, "s-custom")
    assert "crash-zooms" in prompt and "Embers drift upward" in prompt


def test_a_vanished_custom_key_falls_back_quietly() -> None:
    from diony_contracts.cluster import cluster_root

    custom = cluster_root() / ".cluster" / "grok_lexicon.json"
    custom.parent.mkdir(parents=True, exist_ok=True)
    custom.write_text(json.dumps({"cameras": {"crashzoom": {"label": "CZ", "clause": "camera crash-zooms"}}}))
    sid = "s-vanish"
    session.set_grok_template(sid, camera="crashzoom")
    custom.unlink()
    prompt = session.compile_prompt("she turns", CloudProvider.GROK, sid)
    assert prompt and "crash-zooms" not in prompt


# --- HTTP surface ------------------------------------------------------------


def test_grok_lexicon_catalog_endpoint() -> None:
    from fastapi.testclient import TestClient

    from diony_director.app import app

    client = TestClient(app)
    body = client.get("/api/director/grok/lexicon").json()
    assert {c["key"] for c in body["cameras"]} >= {"pushin", "orbit", "locked", "freeze"}
    assert {a["key"] for a in body["atmospheres"]} >= {"dust", "rain", "wind", "smoke"}
    assert body["grok_route_only"] is True


def test_grok_template_locks_with_the_script_and_none_keeps() -> None:
    from fastapi.testclient import TestClient

    from diony_director.app import app

    client = TestClient(app)
    sid = client.post("/api/director/sessions").json()["session_id"]
    res = client.post(
        f"/api/director/sessions/{sid}/produce",
        json={"idea": "a getaway driver leaves town", "grok_atmosphere": "dust"},
    )
    assert res.status_code == 200
    assert session.grok_template_for(sid)["atmosphere"] == "dust"
    client.post(f"/api/director/sessions/{sid}/lock_script")
    res = client.post(f"/api/director/sessions/{sid}/message", json={"text": "one more shot"})
    assert res.status_code == 200, "omitted fields keep the template — never a 409"
    assert session.grok_template_for(sid)["atmosphere"] == "dust"
    res = client.post(
        f"/api/director/sessions/{sid}/message",
        json={"text": "one more", "grok_atmosphere": "rain"},
    )
    assert res.status_code == 409


def test_settings_endpoint_serves_the_stored_program_settings() -> None:
    """The resumed room syncs its controls from this — defaults sent over a
    stored template would silently clear it."""
    from fastapi.testclient import TestClient

    from diony_director.app import app

    client = TestClient(app)
    sid = client.post("/api/director/sessions").json()["session_id"]
    session.set_aspect(sid, "9:16")
    session.set_flow_template(sid, world="filmic", camera="dollyin")
    session.set_grok_template(sid, camera="orbit", atmosphere="dust")
    body = client.get(f"/api/director/sessions/{sid}/settings").json()
    assert body == {
        "aspect": "9:16",
        "voice": "",
        "style": "narrated",
        "flow_template": {"world": "filmic", "camera": "dollyin"},
        "grok_template": {"camera": "orbit", "atmosphere": "dust"},
    }


def test_typoed_grok_shorthand_warns_in_the_proposal() -> None:
    sid = "s-warn"
    proposal = session.propose(sid, "/pushn: she waits", provider="grok")
    assert "/pushn" in proposal.reply and "literal" in proposal.reply


def test_shot_edit_warns_only_on_tokens_neither_lexicon_knows() -> None:
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
        json={"line_id": action["line_id"], "shot": "/pushin with /closup framing"},
    )
    assert res.status_code == 200
    assert res.json().get("shot_warnings") == ["/closup"], (
        "/pushin is Grok shorthand — only the token neither route knows warns"
    )

def test_template_camera_yields_to_authored_camera_prose() -> None:
    sid = "s-grok-cam-prose"
    session.set_grok_template(sid, camera="pushin")
    prompt = session.compile_prompt(
        "camera pans across the harbor at dawn", CloudProvider.GROK, sid
    )
    assert "pushes in" not in prompt, "authored camera prose rules — one move per prompt"
    prompt = session.compile_prompt(
        "a lighthouse braces against the surf", CloudProvider.GROK, sid
    )
    assert "Camera slowly pushes in." in prompt


def test_session_atmosphere_yields_to_scripted_weather() -> None:
    """The scripted setting's weather is law — a session atmosphere that
    contradicts it ("Rain falls" over "Night 1, clear") is suppressed."""
    sid = "s-atmo-weather"
    session.set_grok_template(sid, atmosphere="rain")
    assert grok_lexicon.session_atmosphere_clause(sid, weather="Night 1, clear") == ""
    assert (
        "rain falls"
        in grok_lexicon.session_atmosphere_clause(sid, weather="Night 1, overcast").lower()
    )
    assert "rain falls" in grok_lexicon.session_atmosphere_clause(sid).lower()


def test_cross_route_shot_token_warns_only_when_the_route_is_decided(monkeypatch) -> None:
    """A Flow-only token like /filmic on a Grok-committed session will ride
    literal — a high-confidence warning. Under auto the route is undecided,
    so a route-specific token stays quiet (only unknown-to-both warns)."""
    import diony_contracts.cluster as cluster
    from fastapi.testclient import TestClient

    from diony_director.app import app

    client = TestClient(app)

    def _shot_warn(engine_choice):
        monkeypatch.setattr(cluster, "read_engine_choice", lambda: engine_choice)
        sid = client.post("/api/director/sessions").json()["session_id"]
        client.post(
            f"/api/director/sessions/{sid}/produce",
            json={"idea": "a getaway driver leaves a neon diner at night"},
        )
        draft = client.get(f"/api/director/sessions/{sid}/draft").json()
        action = next(l for l in draft["script"]["lines"] if l["kind"] == "action")
        res = client.post(
            f"/api/director/sessions/{sid}/draft/line",
            json={"line_id": action["line_id"], "shot": "/filmic: on the keys"},
        )
        return res.json().get("shot_warnings", [])

    assert _shot_warn("grok") == ["/filmic"], "Flow-only token rides literal on Grok"
    assert _shot_warn("auto") == [], "route undecided — a route-specific token is silent"
    assert _shot_warn("google-flow") == [], "/filmic is native on the Flow route"
