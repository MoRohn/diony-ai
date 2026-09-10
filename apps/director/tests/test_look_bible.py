"""Blueprint 29 — the Look Bible and the style frame.

The 2026-09-02 chain (s-e790706066) printed twelve clips in eight visual
languages: nothing film-wide stated the look, three places hard-coded
photorealism, the only visual input a chained clip received was the previous
clip's last frame, an unmarked infographic opened the film, and no review
ever ran on a chain link. The doctrine under test:

- one LOOK BIBLE, derived once, carried byte-identical on every clip prompt of
  the film on every dialect, on every plate and sheet, and into the screenplay;
- the photoreal ground-truth clauses ride a photoreal look only;
- a chain link that cuts to a new place opens from a STYLE FRAME composed in
  the look, never from the previous clip's last frame;
- stills are references unless marked as the opener;
- chain links are reviewed, a failed look review holds the chain, and a
  retake rolls the link again.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from master_brain_fakes import master_brain_body

from diony_contracts.director import CloudProvider
from diony_contracts.production import LookBible, look_is_photoreal
from diony_director import (
    app as app_module,
)
from diony_director import (
    artifacts,
    compiler,
    master_prompt,
    packagebuilder,
    screenwriter,
    session,
)
from diony_director import (
    look as look_mod,
)
from diony_director import (
    review as review_mod,
)
from diony_director.providers import base, flow

ANIMATED = (
    "a 2.5D animated explainer about a young man in a sedan at night who watches a city consolidate"
)
PHOTOREAL = "a getaway driver decides to leave town from a neon diner"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24


def _sid() -> str:
    return f"s-test-{uuid.uuid4().hex[:8]}"


# --- the contract --------------------------------------------------------------


def test_the_block_is_deterministic_and_reads_as_one_sentence_of_law() -> None:
    look = LookBible(
        medium="2.5D animated explainer",
        render_language="flat shading, thin outlines",
        palette="muted navy with one amber accent",
        lens_grade="35mm-equivalent, soft contrast",
        lighting_system="one motivated key per scene",
        negatives=["no photoreal footage", "Never 3D gloss", "avoid text"],
    )
    block = look.block()
    assert block.startswith("LOOK BIBLE (holds for every shot of this film)")
    assert "medium: 2.5D animated explainer" in block
    assert "one studio, one film" in block
    # negatives read as nouns after "Never:" — never a double negative
    assert block.endswith("Never: photoreal footage; 3D gloss; text.")
    assert look.block() == block  # byte-identical every time
    assert LookBible().block() == ""


@pytest.mark.parametrize(
    "medium, photoreal",
    [
        ("photoreal live action on a digital cinema camera", True),
        ("cinematic realism", True),
        ("", True),
        ("2.5D animated explainer", False),
        ("hand-drawn anime, cel-shaded", False),
        ("animated documentary", False),
        ("isometric low-poly 3D animation", False),
        ("stop-motion claymation", False),
    ],
)
def test_photoreal_is_decided_by_the_medium(medium: str, photoreal: bool) -> None:
    assert look_is_photoreal(medium) is photoreal
    assert LookBible(medium=medium).photoreal is photoreal


# --- derivation ----------------------------------------------------------------


def test_the_template_derives_a_look_from_the_idea_and_names_it() -> None:
    master = master_prompt.derive(ANIMATED, target_duration_s=8)
    assert master.look is not None and master.look.source == "template"
    assert not master.look.photoreal
    assert "2.5D animated explainer" in master.look.medium
    assert "look 2.5D animated explainer" in master_prompt.summary_line(master)
    photoreal = master_prompt.derive(PHOTOREAL, target_duration_s=8)
    assert photoreal.look is not None and photoreal.look.photoreal


def test_the_brain_is_asked_for_the_look_and_its_answer_is_parsed(monkeypatch) -> None:
    monkeypatch.setenv("DIONY_DIRECTOR_BRAIN_URL", "http://127.0.0.1:9999")
    body = master_brain_body(
        look={
            "medium": "hand-inked animation on watercolour washes",
            "render_language": "visible brush texture, bleeding edges",
            "palette": "indigo and burnt orange",
            "lens_grade": "wide framing, painted depth",
            "lighting_system": "one warm key, cool ambient fill",
            "negatives": ["no photoreal footage", "no 3D gloss"],
        }
    )
    captured: dict = {}

    class _Response:
        status_code = 200

        def json(self) -> dict:
            return {"choices": [{"message": {"content": json.dumps(body)}}]}

    def fake_post(url, **kwargs):
        captured["json"] = kwargs.get("json")
        return _Response()

    monkeypatch.setattr(screenwriter.httpx, "post", fake_post)
    master = master_prompt.derive("a keeper waits", target_duration_s=8)
    system = captured["json"]["messages"][0]["content"]
    assert "LOOK BIBLE" in system and "look ({medium, render_language" in system
    assert master.look is not None and master.look.source == "brain"
    assert master.look.medium == "hand-inked animation on watercolour washes"
    assert master.look.negatives == ["no photoreal footage", "no 3D gloss"]
    assert not master.look.photoreal
    assert "Never: photoreal footage; 3D gloss." in master.look.block()


def test_a_brain_that_omits_the_look_gets_one_derived_and_named(monkeypatch) -> None:
    monkeypatch.setenv("DIONY_DIRECTOR_BRAIN_URL", "http://127.0.0.1:9999")
    body = master_brain_body(visual_style="animation")

    class _Response:
        status_code = 200

        def json(self) -> dict:
            return {"choices": [{"message": {"content": json.dumps(body)}}]}

    monkeypatch.setattr(screenwriter.httpx, "post", lambda *a, **k: _Response())
    master = master_prompt.derive("an animated keeper waits", target_duration_s=8)
    assert master.look is not None and master.look.medium
    assert not master.look.photoreal
    assert any("omitted the look bible" in w for w in master.warnings)


def test_the_clip_split_carries_scene_changes_and_openings(monkeypatch) -> None:
    monkeypatch.setenv("DIONY_DIRECTOR_BRAIN_URL", "http://127.0.0.1:9999")
    body = master_brain_body(
        clips=[
            {
                "prompt": "clip one",
                "duration_s": 8,
                "scene_change": True,
                "opening": "a sedan at a red light",
            },
            {
                "prompt": "clip two",
                "duration_s": 8,
                "scene_change": "true",
                "opening": "a skyline of towers",
            },
            {
                "prompt": "clip three",
                "duration_s": 8,
                "scene_change": False,
                "opening": "the same skyline",
            },
        ]
    )
    captured: dict = {}

    class _Response:
        status_code = 200

        def json(self) -> dict:
            return {"choices": [{"message": {"content": json.dumps(body)}}]}

    def fake_post(url, **kwargs):
        captured["json"] = kwargs.get("json")
        return _Response()

    monkeypatch.setattr(screenwriter.httpx, "post", fake_post)
    master = master_prompt.derive("a city consolidates", target_duration_s=24, clip_plan=[8, 8, 8])
    system = captured["json"]["messages"][0]["content"]
    assert '"scene_change", "opening"' in system
    assert "NEVER restates or varies the medium" in system
    # the opener opens the film — it is never a cut, whatever the brain said
    assert [c["scene_change"] for c in master.clips] == [False, True, False]
    assert master.clips[1]["opening"] == "a skyline of towers"
    # the template split never cuts: one continuous action
    monkeypatch.setenv("DIONY_DIRECTOR_BRAIN_URL", "")
    template = master_prompt.derive(
        "a keeper crosses the room", target_duration_s=24, clip_plan=[8, 8, 8]
    )
    assert all(c["scene_change"] is False for c in template.clips)
    assert all("opening" in c for c in template.clips)


# --- the compiler: every clip, every dialect, byte-identical -----------------------


def _package(idea: str, policy: str):
    sid = _sid()
    master = master_prompt.derive(idea, target_duration_s=60)
    draft = screenwriter.write_script(idea, runtime_target_s=60, master=master)
    package = packagebuilder.build_package(sid, idea, draft, provider_policy=policy)
    return package


@pytest.mark.parametrize("policy", ["grok", "google-flow"])
def test_every_clip_of_an_animated_film_carries_the_same_look_block(policy: str) -> None:
    package = _package(ANIMATED, policy)
    block = package.master_prompt.look.block()
    assert block and not package.master_prompt.look.photoreal
    prompts = [compiler.compile_clip_prompt(clip, package).prompt for clip in package.clips]
    assert prompts
    for prompt in prompts:
        assert block in prompt, "the look rides verbatim on every clip"
        assert compiler.REALISM not in prompt, "photoreal ground truth is a contradiction here"
        assert "Shot on a cinema camera" not in prompt, (
            "the baseline cinema clause yields to the look"
        )


@pytest.mark.parametrize("policy", ["grok", "google-flow"])
def test_a_photoreal_film_keeps_its_ground_truth_beside_the_look(policy: str) -> None:
    package = _package(PHOTOREAL, policy)
    look = package.master_prompt.look
    assert look is not None and look.photoreal
    for clip in package.clips:
        prompt = compiler.compile_clip_prompt(clip, package).prompt
        assert look.block() in prompt
        if clip.provider is CloudProvider.GROK:
            assert compiler.REALISM in prompt
        elif clip.model != "omni-flash":
            assert "Shot on a cinema camera" in prompt


def test_a_chosen_flow_world_still_rides_beside_a_stylized_look() -> None:
    from diony_director import flow_lexicon

    package = _package(ANIMATED, "google-flow")
    flow_lexicon.set_template(package.session_id, world="minimal")
    # the Veo dialect stays a tested capability behind an explicit model choice
    clip = next(c for c in package.clips if c.clip_class not in {"sync-dialogue", "vo-over"})
    clip.model = "veo-3.1-fast"
    clip.duration_s = 8
    prompt = compiler.compile_clip_prompt(clip, package).prompt
    assert flow_lexicon.worlds()["minimal"]["clause"] in prompt
    assert package.master_prompt.look.block() in prompt


def test_a_package_without_a_look_compiles_exactly_as_before() -> None:
    package = _package(PHOTOREAL, "grok")
    package.master_prompt.look = None
    for clip in package.clips:
        prompt = compiler.compile_clip_prompt(clip, package).prompt
        assert "LOOK BIBLE" not in prompt
        assert compiler.REALISM in prompt


# --- plates, sheets, and the screenplay brief ---------------------------------------


def test_plates_and_sheets_render_in_the_films_medium() -> None:
    package = _package(ANIMATED, "google-flow")
    look = package.master_prompt.look
    plates = artifacts.plan_plate_ops(package.session_id, package)
    assert plates
    for plate in plates:
        assert look.block() in plate.prompt
        assert "Photorealistic" not in plate.prompt
        assert "No text" in plate.prompt
    sheets = artifacts.plan_sheet_ops(package.session_id, package)
    for sheet in sheets:
        assert look.block() in sheet.prompt
        assert "Casting photograph" not in sheet.prompt
        assert "in the film's medium" in sheet.prompt
    photoreal = _package(PHOTOREAL, "google-flow")
    plate = artifacts.plan_plate_ops(photoreal.session_id, photoreal)[0]
    assert "Photorealistic cinema still" in plate.prompt
    assert photoreal.master_prompt.look.block() in plate.prompt


def test_a_plate_references_every_look_anchor_the_film_has(tmp_path) -> None:
    package = _package(ANIMATED, "google-flow")
    sid = package.session_id
    still = session.add_upload(sid, name="mood.png", data=PNG)
    anchor = tmp_path / "look.jpg"
    anchor.write_bytes(b"jpg" * 512)
    package.master_prompt.look.anchor_assets.append(str(anchor))
    plate = artifacts.plan_plate_ops(sid, package)[0]
    refs = artifacts.references_for(package, plate)
    assert refs[0] == still["path"], "the user's stills lead"
    assert str(anchor) in refs


def test_the_screenplay_brain_is_briefed_with_the_look() -> None:
    master = master_prompt.derive(ANIMATED, target_duration_s=60)
    brief = screenwriter.master_brief(master)
    assert "LOOK BIBLE (film-wide" in brief
    assert master.look.block() in brief
    assert "LOOK:" in screenwriter._BRAIN_INSTRUCTIONS
    assert "no 'photorealistic' room in an animated film" in screenwriter._BRAIN_INSTRUCTIONS
    draft = screenwriter.write_script(ANIMATED, runtime_target_s=60, master=master)
    for setting in draft.settings:
        assert master.look.palette in setting.grade_language


# --- one-shots and chains ---------------------------------------------------------------


def test_a_one_shot_carries_the_look_on_both_routes() -> None:
    for provider in ("grok", "google-flow"):
        sid = _sid()
        proposal = session.propose(sid, ANIMATED, provider=provider, duration_s=8)
        master = session.master_for_op(proposal.op.id)
        assert master.look is not None
        assert master.look.block() in proposal.op.prompt
        assert "Shot on a cinema camera" not in proposal.op.prompt


def test_every_chain_link_carries_the_identical_look_block() -> None:
    sid = _sid()
    proposal = session.propose(sid, ANIMATED, provider="google-flow", duration_s=24)
    assert len(proposal.ops) == 3
    block = session.master_for_op(proposal.ops[0].id).look.block()
    for op in proposal.ops:
        assert block in op.prompt
    # a template chain is one continuous action: no cuts, no style frames
    assert all(session.style_frame_op_for(op) is None for op in proposal.ops)


def test_style_frame_prompts_compose_the_opening_in_the_look() -> None:
    look = look_mod.template_look(ANIMATED)
    prompt = look_mod.style_frame_prompt("A skyline of glass towers at night.", look)
    assert prompt.startswith(look_mod.STYLE_FRAME_LEAD)
    assert "a skyline of glass towers at night." in prompt
    assert look.block() in prompt
    assert "same studio's hand" in prompt and "No text" in prompt


def test_scene_changes_plan_priced_style_frames_outside_the_chain() -> None:
    sid = _sid()
    proposal = session.propose(sid, ANIMATED, provider="google-flow", duration_s=24)
    master = session.master_for_op(proposal.ops[0].id)
    master.clips[0]["scene_change"] = True  # ignored: the opener is never a cut
    master.clips[2]["scene_change"] = True
    master.clips[2]["opening"] = "A warm cluster of small businesses on a clean field."
    frames = session._plan_style_frames(sid, proposal.ops, master)
    assert len(frames) == 1
    frame = frames[0]
    assert frame.mode.value == "image"
    assert frame.estimate is not None and frame.estimate.total_usd_micros > 0
    assert frame.artifact_refs["style_frame"] == {"for_op": proposal.ops[2].id, "clip_index": 2}
    assert "small businesses" in frame.prompt and master.look.block() in frame.prompt
    assert session.style_frame_op_for(proposal.ops[2]) is frame
    assert session.style_frame_op_for(proposal.ops[1]) is None
    assert "opens from a style frame" in proposal.ops[2].rationale
    # registered in the session, never inside the chain, never a batch target
    assert frame.id in session.get_session(sid).op_ids
    chain = [op.id for chain in session.oneshot_chain_ops(sid) for op in chain]
    assert frame.id not in chain
    targets = session.set_batch_targets(sid, {op.id for op in proposal.ops} | {frame.id})
    assert frame.id not in targets
    # idempotent: planning again creates nothing new
    assert session._plan_style_frames(sid, proposal.ops, master) == []
    assert "1 scene change open from a generated style frame" in session._style_frames_note(frames)


def _fake_lanes(monkeypatch, tmp_path):
    """Video and image lanes that print instantly and record what they were sent."""
    sent: dict = {"videos": [], "images": []}
    counter = {"n": 0}

    def fake_submit(op):
        counter["n"] += 1
        sent["videos"].append({"op_id": op.id, "refs": dict(op.artifact_refs)})
        return base.CloudJobHandle(
            provider_id="google-flow",
            external_id=f"vid-{counter['n']}",
            model=op.model,
            duration_s=int(op.duration_s),
            mode="t2v",
        )

    def fake_submit_image(prompt, *, model, references=None, aspect_ratio="landscape"):
        counter["n"] += 1
        sent["images"].append({"prompt": prompt, "references": list(references or [])})
        return base.CloudJobHandle(
            provider_id="google-flow",
            external_id=f"img-{counter['n']}",
            model=model,
            duration_s=0,
        )

    def fake_poll(handle):
        return base.CloudJobStatus(
            state="done",
            result_url=f"https://flow/{handle.external_id}",
            metadata={"media_generation_id": f"user:{handle.external_id}"},
        )

    def fake_fetch(handle, status, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        payload = (b"png" if handle.duration_s == 0 else b"mp4") * 2048
        dest.write_bytes(payload)
        return base.FetchedClip(
            path=dest,
            sha256=hashlib.sha256(payload).hexdigest(),
            bytes=len(payload),
            media_url=status.result_url,
        )

    monkeypatch.setattr(flow, "submit", fake_submit)
    monkeypatch.setattr(flow, "submit_image", fake_submit_image)
    monkeypatch.setattr(flow, "poll", fake_poll)
    monkeypatch.setattr(flow, "fetch", fake_fetch)
    return sent


def _chain_brain(monkeypatch):
    """A brain whose split cuts at clip 2 (a new place) and continues at clip 3."""
    monkeypatch.setenv("DIONY_DIRECTOR_BRAIN_URL", "http://127.0.0.1:9999")
    body = master_brain_body(
        visual_style="2.5D animated explainer",
        look={
            "medium": "2.5D animated explainer, flat shaded with soft paper texture",
            "render_language": "clean geometric shapes, thin outlines",
            "palette": "muted navy with one amber accent",
            "lens_grade": "35mm-equivalent framing, soft contrast",
            "lighting_system": "one motivated key per scene",
            "negatives": ["no photoreal footage", "no 3D gloss"],
        },
        clips=[
            {
                "prompt": "a man sits in a sedan at a red light",
                "duration_s": 8,
                "scene_change": False,
                "opening": "a man in a sedan at a red light",
            },
            {
                "prompt": "a night skyline of glass towers",
                "duration_s": 8,
                "scene_change": True,
                "opening": "A night skyline of glass towers seen through glass.",
            },
            {
                "prompt": "graphics rise above the same skyline",
                "duration_s": 8,
                "scene_change": False,
                "opening": "the same skyline",
            },
        ],
    )

    class _Response:
        status_code = 200

        def json(self) -> dict:
            return {"choices": [{"message": {"content": json.dumps(body)}}]}

    monkeypatch.setattr(screenwriter.httpx, "post", lambda *a, **k: _Response())


def _wait_proposed(ops, timeout_s: float = 10.0) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline and any(op.status == "deriving" for op in ops):
        time.sleep(0.05)
    assert all(op.status == "proposed" for op in ops), [op.status for op in ops]


def test_a_cut_opens_from_its_style_frame_not_the_previous_last_frame(
    monkeypatch, tmp_path
) -> None:
    """End to end through the API: the brain flags clip 2 as a cut; its style
    frame is priced when the split lands, prints on the image lane the moment
    clip 2 rolls (referencing the user's stills), and clip 2 opens FROM that
    still. Clip 3 continues and bridges from clip 2's last frame as before.
    The finished chain is the cut list; the style frame never is."""
    _chain_brain(monkeypatch)
    sent = _fake_lanes(monkeypatch, tmp_path)
    monkeypatch.setattr(
        review_mod,
        "review_chain_link",
        lambda path, **kw: review_mod.ClipReview(
            id="rev-ok",
            clip_id=kw["op_id"],
            op_id=kw["op_id"],
            verdict="pass",
            families={"delivery": "pass"},
            recommended_op="accept",
        ),
    )
    client = TestClient(app_module.app)
    sid = client.post("/api/director/sessions").json()["session_id"]
    still = client.post(
        f"/api/director/sessions/{sid}/uploads",
        json={"name": "mood.png", "data_base64": __import__("base64").b64encode(PNG).decode()},
    ).json()["upload"]
    body = client.post(
        f"/api/director/sessions/{sid}/message",
        json={"text": ANIMATED, "provider": "google-flow", "duration_s": 24},
    ).json()
    assert "Look Bible verbatim" in body["reply"]
    ops = [session.get_op(o["id"]) for o in body["ops"]]
    assert len(ops) == 3
    _wait_proposed(ops)
    first, second, third = ops
    # the unmarked still rides clip 1 as a reference — never as its opening frame
    assert first.artifact_refs.get("references") == [still["path"]]
    assert "start_image" not in first.artifact_refs
    frame_op = session.style_frame_op_for(second)
    assert frame_op is not None and frame_op.status == "proposed"
    assert session.style_frame_op_for(third) is None
    assert "1 scene change open from a generated style frame" in first.rationale
    state = client.get(f"/api/director/ops/{second.id}").json()
    assert state["scene_change"] is True
    assert state["style_frame"]["op_id"] == frame_op.id and state["style_frame"]["ready"] is False
    block = session.master_for_op(first.id).look.block()
    assert all(block in op.prompt for op in ops)

    # clip 1 rolls and prints
    assert client.post(f"/api/director/ops/{first.id}/run").status_code == 200
    assert client.get(f"/api/director/ops/{first.id}").json()["op"]["status"] == "completed"
    # clip 2: the style frame prints FIRST, then the clip opens from it
    assert client.post(f"/api/director/ops/{second.id}/run").status_code == 200
    assert frame_op.status == "completed"
    assert len(sent["images"]) == 1
    assert still["path"] in sent["images"][0]["references"], (
        "the look's anchors reference the frame"
    )
    assert "a night skyline of glass towers seen through glass." in sent["images"][0]["prompt"]
    assert block in sent["images"][0]["prompt"]
    frame_path = session.runtime(second.id)["style_frame"]
    assert frame_path and Path(frame_path).is_file()
    assert sent["videos"][-1]["op_id"] == second.id
    assert sent["videos"][-1]["refs"]["start_image"] == frame_path
    assert "references" not in sent["videos"][-1]["refs"]
    second_state = client.get(f"/api/director/ops/{second.id}").json()
    assert second_state["op"]["status"] == "completed"
    assert second_state["style_frame"]["ready"] is True
    assert client.get(f"/api/director/ops/{second.id}/style_frame").status_code == 200
    # clip 3 continues: it bridges from clip 2's last frame, no style frame
    monkeypatch.setattr(
        app_module, "_last_frame", lambda p, d, at_s=None, sid="": str(tmp_path / "bridge.jpg")
    )
    (tmp_path / "bridge.jpg").write_bytes(b"jpg" * 64)
    assert client.post(f"/api/director/ops/{third.id}/run").status_code == 200
    assert sent["videos"][-1]["refs"]["start_image"] == str(tmp_path / "bridge.jpg")
    assert client.get(f"/api/director/ops/{third.id}").json()["op"]["status"] == "completed"
    # the chain is the cut list; the style frame is not a clip
    cut = [op.id for op in session.completed_clip_ops(sid)]
    assert cut == [first.id, second.id, third.id]
    assert app_module._cut_gaps(sid) == ([], [], [])
    # every link was reviewed
    assert all(session.review_for(op.id) is not None for op in ops)
    # the look, the frame op, and the frame path survive a restart
    session.persist(sid)
    session.reset()
    session.rehydrate_from_vault()
    assert session.master_for_op(first.id).look.block() == block
    restored = session.get_op(second.id)
    assert session.style_frame_op_for(restored) is not None
    assert session.runtime(second.id)["style_frame"] == frame_path


def test_a_failed_look_review_holds_the_chain_until_retake_or_accept(monkeypatch, tmp_path) -> None:
    from diony_contracts.production import ClipReview

    sid = _sid()
    proposal = session.propose(sid, ANIMATED, provider="google-flow", duration_s=16)
    first, second = proposal.ops
    first.status = "completed"
    clip_file = tmp_path / "first.mp4"
    clip_file.write_bytes(b"mp4" * 1024)
    session.runtime(first.id)["clip"] = base.FetchedClip(
        path=clip_file, sha256="x", bytes=clip_file.stat().st_size
    )
    session.register_review(
        first.id,
        ClipReview(
            id="rev-bad",
            clip_id=first.id,
            op_id=first.id,
            verdict="fail",
            families={"continuity": "fail"},
            issues=["the clip ignored its opening frame (drift 0.120)"],
        ),
    )
    # the batch holds the chain at clip 1, loudly, once
    session.set_batch_targets(sid, {second.id})
    submitted, held = app_module._submit_ready(sid)
    assert submitted == [] and held == []
    drained = session.drain_held(sid)
    assert len(drained) == 1 and "failed its look review" in drained[0]["reason"]
    assert "ignored its opening frame" in drained[0]["reason"]
    assert second.id not in session.batch_targets(sid)
    # a manual run of clip 2 names the failed predecessor
    with pytest.raises(app_module.HTTPException) as err:
        app_module._guard_chain_order(second)
    assert err.value.status_code == 409 and "Retake it or Accept it" in err.value.detail
    # accepting clip 1 re-arms the chain…
    session.mark_accepted(first.id)
    app_module._guard_chain_order(second)
    session.set_batch_targets(sid, {second.id})
    assert [op.id for op in session.ready_ops(sid)] == [second.id]
    # …and so does a retake: back to proposed with a clean runtime, the
    # rejected review gone, the link targeted again
    session._ACCEPTED.discard(first.id)
    client = TestClient(app_module.app)
    monkeypatch.setattr(app_module, "_kick_submitter", lambda _sid: None)
    res = client.post(f"/api/director/ops/{first.id}/retry")
    assert res.status_code == 200 and res.json()["op"]["status"] == "proposed"
    assert session.review_for(first.id) is None
    assert "clip" not in session.runtime(first.id)
    assert "retake after a failed look review" in first.rationale
    assert first.id in session.batch_targets(sid)
    # a completed link that PASSED never retakes through retry
    second.status = "completed"
    assert client.post(f"/api/director/ops/{second.id}/retry").status_code == 409


HAVE_FFMPEG = True
try:
    from diony_producer.media_tools import resolve_media_tool

    HAVE_FFMPEG = bool(resolve_media_tool("ffmpeg") and resolve_media_tool("ffprobe"))
except ImportError:
    HAVE_FFMPEG = False


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not installed")
def test_the_chain_review_fails_a_clip_that_ignored_its_opening_frame(tmp_path) -> None:
    import subprocess

    ffmpeg = resolve_media_tool("ffmpeg")
    opener = tmp_path / "style.jpg"
    clip_path = tmp_path / "clip.mp4"
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=navy:s=320x180:d=1",
            "-frames:v",
            "1",
            str(opener),
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=orange:s=320x180:d=2:r=24",
            str(clip_path),
        ],
        check=True,
        capture_output=True,
    )
    review = review_mod.review_chain_link(
        clip_path, op_id="op-x", duration_s=2, start_image=opener, scene_change=True
    )
    assert review.verdict == "fail"
    assert review.measured["open_drift"] > review_mod.OPEN_DRIFT_FAIL
    assert any("ignored its opening frame" in i for i in review.issues)
    assert review.families["continuity"] == "fail"
    # a scene change is never measured against the previous clip's colour
    assert "chain_color_drift" not in review.measured
    # the same clip opening from its own colour passes
    same = tmp_path / "same.jpg"
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=orange:s=320x180:d=1",
            "-frames:v",
            "1",
            str(same),
        ],
        check=True,
        capture_output=True,
    )
    good = review_mod.review_chain_link(
        clip_path,
        op_id="op-y",
        duration_s=2,
        start_image=same,
        prev_clip_path=clip_path,
        scene_change=False,
    )
    assert good.verdict == "pass"
    assert good.measured["chain_color_drift"] < review_mod.CHAIN_DRIFT_FAIL
