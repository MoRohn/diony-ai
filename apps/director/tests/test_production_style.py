"""Blueprint 31 — the Production Style: Narrated (default), Dialogue, Mix.

The style resolves into a per-clip / per-op `voicing` on the contracts; the
compiler, review, cue sheet, speech guard, and mix read the clip's own
voicing. Narrated must stay byte-identical to the pre-31 platform."""

import importlib
import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_production_flow import _drive as drive
from test_production_flow import _fake_adapters as fake_adapters
from test_production_flow import _start as start

from diony_contracts.director import ProductionStyle
from diony_director import (
    app as app_module,
)
from diony_director import (
    assembly,
    compiler,
    narration,
    packagebuilder,
    review,
    revisions,
    screenwriter,
    session,
    styles,
)

HAVE_FFMPEG = shutil.which("ffmpeg") is not None
IDEA = "a getaway driver decides to leave town from a neon diner"


def _package(style: str = "narrated", policy: str = "grok"):
    draft = screenwriter.write_script(IDEA, runtime_target_s=60)
    return packagebuilder.build_package(
        "s-style", "a getaway", draft, provider_policy=policy, production_style=style
    )


def _spoken(package):
    return [c for c in package.clips if c.audio_packet.script_excerpt]


# --- the style catalog and the voicing doctrine --------------------------------


def test_the_catalog_offers_three_styles_and_narrated_is_the_default() -> None:
    keys = [entry["key"] for entry in styles.catalog()]
    assert keys == ["narrated", "dialogue", "mix"]
    assert styles.DEFAULT_STYLE is ProductionStyle.NARRATED
    assert styles.normalize_style("") is ProductionStyle.NARRATED
    assert styles.normalize_style(" Mix ") is ProductionStyle.MIX
    with pytest.raises(ValueError, match="unknown production style"):
        styles.normalize_style("karaoke")


def test_voicing_follows_the_style_doctrine() -> None:
    # Narrated: everything on the post narrator (Blueprint 28, unchanged)
    assert styles.voicing_for("narrated", on_camera=True) == "post"
    assert styles.voicing_for("narrated", on_camera=False) == "post"
    # Dialogue: every line voiced in the clip
    assert styles.voicing_for("dialogue", on_camera=True) == "generated"
    assert styles.voicing_for("dialogue", on_camera=False) == "generated"
    # Mix: characters speak on camera; voice-over stays with the narrator
    assert styles.voicing_for("mix", on_camera=True) == "generated"
    assert styles.voicing_for("mix", on_camera=False) == "post"


def test_the_split_lands_the_voicing_on_every_spoken_clip() -> None:
    narrated = _package("narrated")
    assert narrated.production_style is ProductionStyle.NARRATED
    assert {c.audio_packet.voicing for c in narrated.clips} == {"post"}

    dialogue = _package("dialogue")
    assert dialogue.production_style is ProductionStyle.DIALOGUE
    assert {c.audio_packet.voicing for c in _spoken(dialogue)} == {"generated"}
    # picture clips carry no line — their voicing is the untouched default
    assert all(
        c.audio_packet.voicing == "post"
        for c in dialogue.clips
        if not c.audio_packet.script_excerpt
    )

    mix = _package("mix")
    on_camera = [c for c in _spoken(mix) if c.clip_class == "sync-dialogue"]
    voice_over = [c for c in _spoken(mix) if c.clip_class == "vo-over"]
    assert on_camera and voice_over, "the fixture script must carry both kinds"
    assert {c.audio_packet.voicing for c in on_camera} == {"generated"}
    assert {c.audio_packet.voicing for c in voice_over} == {"post"}


# --- the compiler ---------------------------------------------------------------


@pytest.mark.parametrize("policy", ["grok", "google-flow"])
def test_dialogue_prompts_carry_the_line_and_the_instruction_to_voice_it(policy) -> None:
    package = _package("dialogue", policy)
    for clip in _spoken(package):
        prompt = compiler.compile_clip_prompt(clip, package).prompt
        line = clip.audio_packet.script_excerpt[0]["line"]
        assert f'"{line}"' in prompt, f"{clip.clip_id}: the line must ride verbatim"
        assert compiler.ONLY_THE_LINE in prompt
        assert compiler.NO_SPEECH not in prompt
        assert "recorded separately" not in prompt
        if clip.audio_packet.on_camera:
            assert "speaks aloud, on camera, lips in sync" in prompt
        else:
            assert "voice-over is heard" in prompt
    # picture clips are still speechless by law — only the LINES moved
    for clip in package.clips:
        if clip.audio_packet.script_excerpt:
            continue
        assert compiler.NO_SPEECH in compiler.compile_clip_prompt(clip, package).prompt


def test_narrated_compiles_exactly_as_before_the_style_existed() -> None:
    # the default style is the pre-31 platform: no line ever rides a prompt
    package = _package("narrated")
    for clip in _spoken(package):
        prompt = compiler.compile_clip_prompt(clip, package).prompt
        assert f'"{clip.audio_packet.script_excerpt[0]["line"]}"' not in prompt
        assert compiler.NO_SPEECH in prompt
        assert compiler.ONLY_THE_LINE not in prompt


def test_mix_voices_dialogue_in_the_clip_and_keeps_voice_over_for_the_narrator() -> None:
    package = _package("mix")
    for clip in _spoken(package):
        prompt = compiler.compile_clip_prompt(clip, package).prompt
        line = clip.audio_packet.script_excerpt[0]["line"]
        if clip.clip_class == "sync-dialogue":
            assert f'"{line}"' in prompt and compiler.NO_SPEECH not in prompt
        else:
            assert f'"{line}"' not in prompt and compiler.NO_SPEECH in prompt


def test_a_long_spoken_line_survives_length_pressure_verbatim(monkeypatch) -> None:
    package = _package("dialogue", "grok")
    clip = next(c for c in _spoken(package) if c.provider.value == "grok")
    long_line = " ".join(["we leave before the neon dies and nobody follows"] * 6)
    clip.audio_packet.script_excerpt[0]["line"] = long_line
    monkeypatch.setenv("DIONY_PROMPT_LIMIT_GROK", "900")
    prompt = compiler.compile_clip_prompt(clip, package).prompt
    assert len(prompt) <= 900
    assert f'"{long_line}"' in prompt, "the quoted words shrink last of all"


def test_a_spoken_clip_gets_the_line_fix_never_the_mute_track_fix() -> None:
    package = _package("dialogue")
    clip = _spoken(package)[0]
    clip.add_prompt(compiler.compile_clip_prompt(clip, package))
    revised = compiler.revise_prompt(clip, package, note="fix-audio")
    assert compiler.REVISION_DIRECTIVES["fix-line"] in revised.prompt
    assert compiler.REVISION_DIRECTIVES["fix-audio"] not in revised.prompt
    # a Narrated clip keeps the Blueprint 28 fix
    narrated = _package("narrated")
    post = _spoken(narrated)[0]
    post.add_prompt(compiler.compile_clip_prompt(post, narrated))
    assert (
        compiler.REVISION_DIRECTIVES["fix-audio"]
        in compiler.revise_prompt(post, narrated, note="fix-audio").prompt
    )


def test_a_failed_line_family_plans_the_line_fix() -> None:
    from diony_contracts.production import ClipReview

    failed = ClipReview(
        id="r-line",
        clip_id="cl-2-2",
        verdict="fail",
        families={"line": "fail", "speech": "pass", "identity": "fail"},
        issues=["the spoken words are not the scripted line"],
    )
    assert revisions._note_for(failed) == "fix-line"


# --- review: the spoken line is measured, not forbidden ------------------------


class _Speech:
    def __init__(self, transcript: str, word_count: int, speech_s: float):
        self.transcript = transcript
        self.word_count = word_count
        self.speech_s = speech_s


def _stub(tmp_path):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"\x00" * 4096)
    return path


def test_a_spoken_clip_that_speaks_its_line_passes(tmp_path, monkeypatch) -> None:
    package = _package("dialogue")
    clip = _spoken(package)[0]
    line = clip.audio_packet.script_excerpt[0]["line"]
    monkeypatch.setattr(review, "location_speech", lambda p: _Speech(line, len(line.split()), 2.4))
    result = review.review_clip(_stub(tmp_path), clip)
    assert result.families["speech"] == "pass", "the heard voice is the authored one"
    assert result.families["line"] == "pass"
    assert result.measured["line_accuracy"] >= review.LINE_ACCURACY_FAIL
    assert result.families["speech"] != "fail" and result.families["line"] != "fail"


def test_a_spoken_clip_that_stays_mute_fails_its_line(tmp_path, monkeypatch) -> None:
    package = _package("dialogue")
    clip = _spoken(package)[0]
    monkeypatch.setattr(review, "location_speech", lambda p: _Speech("", 0, 0.0))
    result = review.review_clip(_stub(tmp_path), clip)
    assert result.families["line"] == "fail"
    assert result.verdict == "fail"
    assert any("never spoke the scripted line" in issue for issue in result.issues)


def test_a_spoken_clip_that_says_other_words_fails_its_line(tmp_path, monkeypatch) -> None:
    package = _package("dialogue")
    clip = _spoken(package)[0]
    monkeypatch.setattr(
        review,
        "location_speech",
        lambda p: _Speech("the weather is lovely in the mountains today", 8, 3.0),
    )
    result = review.review_clip(_stub(tmp_path), clip)
    assert result.families["line"] == "fail"
    assert any("not the scripted line" in issue for issue in result.issues)


def test_a_spoken_clip_is_honestly_unmeasured_without_a_detector(tmp_path, monkeypatch) -> None:
    package = _package("dialogue")
    clip = _spoken(package)[0]
    monkeypatch.setattr(review, "location_speech", lambda p: None)
    result = review.review_clip(_stub(tmp_path), clip)
    assert result.families["speech"] == "unmeasured"
    assert result.families["line"] == "unmeasured"
    assert result.verdict != "fail"


def test_a_narrated_clip_never_grows_a_line_family(tmp_path, monkeypatch) -> None:
    package = _package("narrated")
    clip = _spoken(package)[0]
    monkeypatch.setattr(
        review, "location_speech", lambda p: _Speech("we finish this tonight", 4, 2.0)
    )
    result = review.review_clip(_stub(tmp_path), clip)
    assert "line" not in result.families
    assert result.families["speech"] == "fail", "Blueprint 28 still holds on the Narrated style"


def test_a_chain_link_voicing_its_narration_is_measured_for_it(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(review, "location_speech", lambda p: _Speech("the city sleeps", 3, 1.6))
    spoken = review.review_chain_link(
        _stub(tmp_path), op_id="op-a", duration_s=8, spoken_line="The city sleeps."
    )
    assert spoken.families["line"] == "pass" and spoken.families["speech"] == "pass"
    speechless = review.review_chain_link(_stub(tmp_path), op_id="op-b", duration_s=8)
    assert "line" not in speechless.families
    assert speechless.families["speech"] == "fail"


# --- the cue sheet, the mix balance -------------------------------------------


def _op(op_id: str, *, narr: str = "", voicing: str = "post"):
    from diony_contracts.director import (
        CloudGenerationMode,
        CloudProvider,
        CostEstimate,
        CostEstimateLine,
        DirectorOp,
        DirectorOpType,
    )

    return DirectorOp(
        id=op_id,
        session_id="s-n",
        op=DirectorOpType.GENERATE,
        provider=CloudProvider.GROK,
        model="grok-imagine-video",
        mode=CloudGenerationMode.T2V,
        prompt="x",
        narration=narr,
        voicing=voicing,
        duration_s=6,
        estimate=CostEstimate(
            id="est-n",
            workspace_id="ws",
            lines=[
                CostEstimateLine(
                    label="gen",
                    provider_id="grok",
                    model="grok-imagine-video",
                    unit="clip",
                    usd_micros=1000,
                )
            ],
        ),
    )


def test_the_cue_sheet_skips_clips_that_voice_their_own_line_unless_rescued() -> None:
    package = _package("mix")
    on_camera = next(c for c in _spoken(package) if c.audio_packet.voicing == "generated")
    voice_over = next(c for c in _spoken(package) if c.audio_packet.voicing == "post")
    entries = [
        {"op": _op("op-1"), "clip": on_camera, "window": (0.0, 6.0)},
        {"op": _op("op-2"), "clip": voice_over, "window": (0.0, 6.0)},
        {
            "op": _op("op-3", narr="It will not wake.", voicing="generated"),
            "clip": None,
            "window": (0.0, 4.0),
        },
    ]
    cues = narration.cue_sheet(entries)
    assert [c.clip_id for c in cues] == [voice_over.clip_id]
    # Blueprint 33: the cue lands where the take puts its words (never past
    # the clip's own end), after the clip before it
    at_s = min(voice_over.audio_packet.line_at_s, 6.0 - 0.5)
    assert cues[0].start_s == pytest.approx(6.0 + at_s + narration.CUE_LEAD_IN_S)
    # a rescued spoken clip is narrated after all — placed on its own mark
    entries[0]["narrate_anyway"] = True
    rescued = narration.cue_sheet(entries)
    assert [c.clip_id for c in rescued] == [on_camera.clip_id, voice_over.clip_id]
    on_at_s = min(on_camera.audio_packet.line_at_s, 6.0 - 0.5)
    assert rescued[0].start_s == pytest.approx(on_at_s + narration.CUE_LEAD_IN_S)


def test_assembly_balances_a_spoken_clip_to_the_narrator_level() -> None:
    probes = [{"duration_s": 6.0, "has_audio": True}, {"duration_s": 6.0, "has_audio": True}]
    specs = [{"loudness": narration.NARRATION_LOUDNESS}, {}]
    graph = ";".join(
        assembly.build_filter(probes, {}, look=True, loudness_target="streaming", specs=specs)
    )
    assert (
        f"[0:a]atrim=start=0.000:end=6.000,asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo,loudnorm=I={narration.NARRATION_LOUDNESS}"
        in graph
    )
    assert (
        f"[1:a]atrim=start=0.000:end=6.000,asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo,loudnorm=I={assembly.PER_CLIP_LOUDNESS}"
        in graph
    )


def test_the_speech_guard_keeps_rescues_and_balances_spoken_tracks(monkeypatch, tmp_path) -> None:
    from diony_director import review as review_mod

    importlib.reload(app_module)
    paths = [
        tmp_path / f"{n}.mp4"
        for n in ("spoken-heard", "spoken-mute", "spoken-blind", "post-dirty", "post-blind")
    ]
    for p in paths:
        p.write_bytes(b"x")
    detections = {
        "spoken-heard.mp4": _Speech("we finish this tonight", 4, 2.2),
        "spoken-mute.mp4": _Speech("", 0, 0.0),
        "spoken-blind.mp4": None,
        "post-dirty.mp4": _Speech("hello there my friend", 4, 1.8),
        "post-blind.mp4": None,
    }
    monkeypatch.setattr(review_mod, "location_speech", lambda p: detections[p.name])
    specs = [{} for _ in paths]
    report = app_module._speech_guard(
        paths,
        [p.stem for p in paths],
        [True, True, True, False, True],
        specs,
        ["generated", "generated", "generated", "post", "post"],
        has_post_lines=True,
    )
    assert [k["clip"] for k in report["kept"]] == ["spoken-heard", "spoken-blind"]
    assert [r["clip"] for r in report["rescued"]] == ["spoken-mute"]
    assert [m["clip"] for m in report["muted"]] == ["post-dirty", "post-blind"]
    # spoken tracks are never muted; kept ones are balanced to the narrator
    assert not specs[0].get("mute") and not specs[1].get("mute") and not specs[2].get("mute")
    assert specs[3]["mute"] is True and specs[4]["mute"] is True
    assert report["balanced"] == ["spoken-heard", "spoken-blind"]
    assert specs[0]["loudness"] == narration.NARRATION_LOUDNESS and "loudness" not in specs[1]


def test_no_narrator_rail_means_no_balance(monkeypatch, tmp_path) -> None:
    from diony_director import review as review_mod

    importlib.reload(app_module)
    path = tmp_path / "spoken.mp4"
    path.write_bytes(b"x")
    monkeypatch.setattr(review_mod, "location_speech", lambda p: None)
    specs = [{}]
    report = app_module._speech_guard(
        [path], ["spoken"], [True], specs, ["generated"], has_post_lines=False
    )
    assert report["kept"] and report["balanced"] == [] and "loudness" not in specs[0]


# --- the session and the API ----------------------------------------------------


def _client() -> TestClient:
    importlib.reload(app_module)
    return TestClient(app_module.app, raise_server_exceptions=False)


def test_the_style_persists_and_rehydrates() -> None:
    sid = session.create_session()
    assert session.style_for(sid) == "narrated"
    session.set_style(sid, "mix")
    session.persist(sid)
    session.reset()
    session.rehydrate_from_vault()
    assert session.style_for(sid) == "mix"
    session.set_style(sid, "")
    assert session.style_for(sid) == "narrated"


def test_the_console_chooses_a_style_and_the_lock_freezes_it() -> None:
    client = _client()
    cat = client.get("/api/director/styles").json()
    assert cat["default"] == "narrated"
    assert [s["key"] for s in cat["styles"]] == ["narrated", "dialogue", "mix"]
    sid = client.post("/api/director/sessions").json()["session_id"]
    assert client.get(f"/api/director/sessions/{sid}/settings").json()["style"] == "narrated"

    assert (
        client.post(f"/api/director/sessions/{sid}/style", json={"style": "karaoke"}).status_code
        == 422
    )
    chosen = client.post(f"/api/director/sessions/{sid}/style", json={"style": "dialogue"})
    assert chosen.status_code == 200 and chosen.json()["style"] == "dialogue"
    assert client.get(f"/api/director/sessions/{sid}/settings").json()["style"] == "dialogue"

    # the produce body carries it too (None keeps)
    client.post(f"/api/director/sessions/{sid}/produce", json={"idea": IDEA, "style": "mix"})
    assert session.style_for(sid) == "mix"
    client.post(f"/api/director/sessions/{sid}/produce", json={"idea": IDEA})
    assert session.style_for(sid) == "mix"

    locked = client.post(f"/api/director/sessions/{sid}/lock_script").json()
    package = session.package_for(sid)
    assert package.production_style is ProductionStyle.MIX
    spoken = [c for c in package.clips if c.audio_packet.voicing == "generated"]
    assert spoken and all(c.clip_class == "sync-dialogue" for c in spoken)
    assert any('"' in op["prompt"] and "speaks aloud" in op["prompt"] for op in locked["ops"])

    refused = client.post(f"/api/director/sessions/{sid}/style", json={"style": "narrated"})
    assert refused.status_code == 409
    assert "production style cannot change mid-production" in refused.text
    assert session.style_for(sid) == "mix"
    # the same style again is a no-op, not a refusal
    assert (
        client.post(f"/api/director/sessions/{sid}/style", json={"style": "mix"}).status_code == 200
    )


AUTHORED_IDEA = (
    "Title: The Companies That Never Get Built\n"
    "Scene 1 — Quiet Success\n"
    "Visual: Night. You sit alone in a modest sedan at a red light in a clean business district.\n"
    "Narration: “You were told AI would create more opportunity.”\n"
    "Scene 2 — The Attractive Thesis\n"
    "Visual: Animated graphics appear above the skyline.\n"
    "Narration: “And on the surface, it makes sense.”\n"
)


def test_one_shots_take_the_voicing_of_the_style() -> None:
    # Dialogue: the authored narration rides the clip prompt, spoken in the clip
    sid = session.create_session()
    session.set_style(sid, "dialogue")
    session.propose(sid, AUTHORED_IDEA, provider="grok", duration_s=20)
    links = [o for o in session.ops_for(sid) if o.op.value == "generate"]
    assert links and all(o.voicing == "generated" for o in links)
    spoken_links = [o for o in links if o.narration]
    assert spoken_links
    for op in spoken_links:
        assert f'"{op.narration}"' in op.prompt
        assert compiler.ONLY_THE_LINE in op.prompt
        assert compiler.NO_SPEECH not in op.prompt

    # a SINGLE clip under Dialogue carries its authored words the same way
    sid1 = session.create_session()
    session.set_style(sid1, "dialogue")
    single = session.propose(sid1, AUTHORED_IDEA, provider="grok", duration_s=8).op
    assert single.voicing == "generated" and single.narration
    assert f'"{single.narration}"' in single.prompt and compiler.NO_SPEECH not in single.prompt

    # Mix: a one-shot's narration is voice-over by nature — the narrator's
    sid2 = session.create_session()
    session.set_style(sid2, "mix")
    session.propose(sid2, AUTHORED_IDEA, provider="grok", duration_s=20)
    links2 = [o for o in session.ops_for(sid2) if o.op.value == "generate"]
    assert links2 and all(o.voicing == "post" for o in links2)
    assert all(compiler.NO_SPEECH in o.prompt for o in links2 if o.narration)

    # the voicing survives a restart with the op
    session.persist(sid)
    session.reset()
    session.rehydrate_from_vault()
    assert all(session.get_op(o.id).voicing == "generated" for o in links)


def test_the_brain_contract_tells_the_derivation_where_the_words_go() -> None:
    from diony_director import master_prompt

    post = master_prompt.system_prompt([8, 8], "post")
    generated = master_prompt.system_prompt([8, 8], "generated")
    assert "must NEVER appear inside the prompt" in post
    assert "never write that a clip is silent" in generated
    assert "SYSTEM appends" in generated
    # the default is byte-identical to the Blueprint 28 contract
    assert master_prompt.system_prompt([8, 8]) == post
    assert master_prompt.OUTPUT_CONTRACT == master_prompt.output_contract("post")


# --- the cut, end to end ----------------------------------------------------------


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not installed")
def test_a_dialogue_film_cuts_without_a_narrator(monkeypatch, tmp_path) -> None:
    from diony_contracts.cluster import cluster_root

    client = _client()
    fake_adapters(monkeypatch, tmp_path)
    # no narrator anywhere on this host: Dialogue needs none
    monkeypatch.setenv("DIONY_NARRATOR_BACKEND", "cli")
    monkeypatch.delenv("DIONY_NARRATOR_CLI", raising=False)
    sid = start(client)
    client.post(f"/api/director/sessions/{sid}/produce", json={"idea": IDEA, "style": "dialogue"})
    client.post(f"/api/director/sessions/{sid}/lock_script")
    client.post(f"/api/director/sessions/{sid}/run_batch", json={})
    drive(client, sid)
    res = client.post(f"/api/director/sessions/{sid}/assemble", json={})
    assert res.status_code == 200, res.text
    film = res.json()
    assert film["style"]["key"] == "dialogue"
    assert len(film["style"]["spoken_in_clip"]) == 4 and film["style"]["narrated"] == []
    assert film["narration"] == {"cue_count": 0, "skipped": "lines voiced in the clips"}
    # spoken tracks are kept, never muted — unverified on this host, and said so
    guard = film["speech_guard"]
    assert {k["clip"] for k in guard["kept"]} == set(film["style"]["spoken_in_clip"])
    assert all("unverified" in k["why"] for k in guard["kept"])
    assert guard["muted"] == [] and guard["rescued"] == [] and guard["balanced"] == []
    root = cluster_root() / "director" / sid
    assert (root / "film.mp4").is_file()
    assert not (root / "picture-lock.mp4").exists(), "no narration pass ran"
    # the subtitles still carry every line, timed to the clip windows
    assert "film.srt" in film["deliverables"] and film["cue_count"] == 4
    edl = json.loads((root / "edl.json").read_text())
    assert {
        e["voicing"] for e in edl["events"] if e["clip_class"] in {"sync-dialogue", "vo-over"}
    } == {"generated"}
    assert {
        e["voicing"] for e in edl["events"] if e["clip_class"] not in {"sync-dialogue", "vo-over"}
    } == {"post"}


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not installed")
def test_a_mix_film_narrates_voice_over_balances_dialogue_and_rescues_a_mute_take(
    monkeypatch, tmp_path, fake_narrator
) -> None:
    from diony_contracts.cluster import cluster_root
    from diony_director import review as review_mod

    client = _client()
    fake_adapters(monkeypatch, tmp_path)
    sid = start(client)
    client.post(f"/api/director/sessions/{sid}/produce", json={"idea": IDEA, "style": "mix"})
    client.post(f"/api/director/sessions/{sid}/lock_script").json()
    client.post(f"/api/director/sessions/{sid}/run_batch", json={})
    drive(client, sid)
    package = session.package_for(sid)
    spoken_ids = [c.clip_id for c in package.clips if c.audio_packet.voicing == "generated"]
    vo_ids = [
        c.clip_id
        for c in package.clips
        if c.audio_packet.script_excerpt and c.audio_packet.voicing == "post"
    ]
    assert len(spoken_ids) == 3 and len(vo_ids) == 1

    # the cut can hear: two spoken takes voiced their lines, one stayed mute
    printed = {}
    for op in session.completed_clip_ops(sid):
        printed[Path(session.runtime(op.id)["clip"].path).name] = op.clip_id
    mute_clip = spoken_ids[1]

    def _hear(path):
        clip_id = printed.get(path.name)
        if clip_id == mute_clip:
            return _Speech("", 0, 0.0)
        if clip_id in spoken_ids:
            return _Speech("we finish this tonight then we are gone", 8, 3.0)
        return _Speech("", 0, 0.0)

    monkeypatch.setattr(review_mod, "location_speech", _hear)
    res = client.post(f"/api/director/sessions/{sid}/assemble", json={})
    assert res.status_code == 200, res.text
    film = res.json()
    assert film["style"]["key"] == "mix"
    assert sorted(film["style"]["spoken_in_clip"]) == sorted(spoken_ids)
    assert film["style"]["narrated"] == vo_ids
    assert film["style"]["rescued"] == [mute_clip]
    guard = film["speech_guard"]
    assert [r["clip"] for r in guard["rescued"]] == [mute_clip]
    assert sorted(guard["balanced"]) == sorted(c for c in spoken_ids if c != mute_clip)
    assert guard["muted"] == []
    # the narrator spoke the voice-over AND the rescued line — one voice
    assert film["narration"]["cue_count"] == 2
    assert film["narration"]["voice"]["voice_id"]
    root = cluster_root() / "director" / sid
    assert (root / "picture-lock.mp4").is_file()
    qc = json.loads((root / "qc-packet.json").read_text())
    assert sorted(c["clip_id"] for c in qc["narration"]["cues"]) == sorted([mute_clip] + vo_ids)
