"""Blueprint 32 — Cast Lock: identity cards, the board sheet, measured
sheets, cast keyframes, provider identity markers, and off-screen lines.

Standard must stay byte-identical to the pre-32 platform; Locked is opt-in."""

import importlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from diony_contracts.production import (
    CharacterProfile,
    IdentityCard,
    Scene,
    Script,
    ScriptLine,
    SettingProfile,
)
from diony_director import (
    app as app_module,
    artifacts,
    cast,
    compiler,
    deliverables,
    packagebuilder,
    review,
    screenwriter,
    session,
    styles,
)
from diony_director.providers import base, flow, grok
from test_production_flow import _drive as drive, _fake_adapters as fake_adapters, _start as start

IDEA = "a getaway driver decides to leave town from a neon diner"


def _estimate():
    from diony_contracts.director import CostEstimate, CostEstimateLine

    return CostEstimate(
        id="est-cast", workspace_id="local-workspace",
        lines=[CostEstimateLine(label="video seconds · 720p", provider_id="grok", model="m", unit="second:720p",
                                unit_rate_usd_micros=70_000, quantity_microunits=6_000_000, usd_micros=420_000)],
    )


def _client() -> TestClient:
    importlib.reload(app_module)
    return TestClient(app_module.app, raise_server_exceptions=False)


def _package(cast_lock: str = "standard", style: str = "narrated", policy: str = "auto"):
    draft = screenwriter.write_script(IDEA, runtime_target_s=60)
    return packagebuilder.build_package(
        "s-cast", "a getaway", draft, provider_policy=policy, production_style=style, cast_lock=cast_lock
    )


def _two_hander(*, off_screen: bool, style: str = "narrated", cast_lock: str = "standard", policy: str = "auto"):
    """A scene where MARA speaks and JON listens — MARA in frame, or (O.S.)."""
    mara = CharacterProfile(
        char_id="mara", name="MARA", role="the driver",
        age_body_notes="34, lean, oval face, auburn hair to the shoulder, hazel eyes, a small scar through the left brow",
        wardrobe={"default": "grey wool coat over a black turtleneck"},
    )
    jon = CharacterProfile(
        char_id="jon", name="JON", role="the mechanic",
        age_body_notes="50, heavy shoulders, close-cropped grey hair, deep-set brown eyes",
        wardrobe={"default": "oil-stained navy coveralls"},
    )
    diner = SettingProfile(
        setting_id="diner", name="the neon diner", time_weather="Night 1, neon and rain",
        architecture_notes="a chrome counter, red vinyl booths", grade_language="teal shadow, magenta neon",
        room_tone_id="diner/tone",
    )
    lines = [
        ScriptLine(line_id="ln-1", kind="heading", text="INT. NEON DINER - NIGHT", scene_id="sc-1"),
        ScriptLine(line_id="ln-2", kind="action", text="Jon wipes the counter with a rag that only moves the grease around.", scene_id="sc-1", shot="Wide shot, chest height, the counter running to the door"),
        ScriptLine(line_id="ln-3", kind="cue", text="MARA", speaker="MARA", scene_id="sc-1"),
        ScriptLine(line_id="ln-4", kind="dialogue", text="Keys are on the hook. Don't wait up.", speaker="MARA", scene_id="sc-1", off_screen=off_screen),
        ScriptLine(line_id="ln-5", kind="action", text="Jon sets the rag down and does not turn around.", scene_id="sc-1", shot="Medium close-up on Jon, still"),
    ]
    script = Script(
        id="scr-os", logline="a driver leaves", beats=[], lines=lines,
        scenes=[Scene(scene_id="sc-1", heading="INT. NEON DINER - NIGHT", setting_ref="diner", cast=["mara", "jon"], line_ids=[l.line_id for l in lines])],
    )
    draft = screenwriter.ScreenplayDraft(script=script, characters=[mara, jon], settings=[diner], warnings=[])
    return packagebuilder.build_package(
        "s-os", "a driver leaves", draft, provider_policy=policy, production_style=style, cast_lock=cast_lock
    )


# --- the catalog and the identity card ------------------------------------------


def test_the_catalog_offers_two_modes_and_standard_is_the_default() -> None:
    assert [m["key"] for m in cast.catalog()] == ["standard", "locked"]
    assert cast.normalize_mode("") == "standard"
    assert cast.normalize_mode(" Locked ") == "locked"
    with pytest.raises(ValueError, match="unknown cast lock"):
        cast.normalize_mode("welded")


def test_the_identity_card_derives_deterministically_from_the_notes() -> None:
    char = CharacterProfile(
        char_id="mara", name="MARA",
        age_body_notes="34, lean, oval face; auburn hair to the shoulder, hazel eyes, a scar through the left brow",
        wardrobe={"default": "grey wool coat"},
    )
    first = cast.derive_identity(char)
    second = cast.derive_identity(char)
    assert first == second
    assert first.source == "derived"
    assert first.anchor == "34"
    assert "auburn hair to the shoulder" in first.features
    assert first.wardrobe_lock == "grey wool coat"
    block = first.block("MARA")
    assert block.startswith("IDENTITY LOCK — MARA: 34.")
    assert "Wardrobe, unchanged in every shot: grey wool coat." in block
    # forbidden drift reads back as the noun it forbids
    assert "Never: added jewelry; hair restyle" in block
    assert block.endswith("MARA is the same person in every frame — same face, same build, same hair.")
    # ensure_identity keeps a card once written
    cast.ensure_identity(char)
    char.identity.anchor = "edited by hand"
    assert cast.ensure_identity(char).anchor == "edited by hand"


def test_a_brain_written_card_is_kept_and_a_useless_one_is_ignored() -> None:
    char = CharacterProfile(char_id="jon", name="JON", wardrobe={"default": "coveralls"})
    card = cast.parse_brain_identity(
        {"anchor": "50, heavy shoulders", "features": ["grey crop", "brown eyes"], "negatives": ["no beard"]}, char
    )
    assert card is not None and card.source == "brain"
    assert card.features == ["grey crop", "brown eyes"] and card.negatives == ["no beard"]
    assert cast.parse_brain_identity({}, char) is None
    assert cast.parse_brain_identity("nope", char) is None


def test_the_screenwriter_brain_contract_asks_for_the_card_and_off_screen() -> None:
    text = screenwriter._BRAIN_INSTRUCTIONS
    assert "identity: {anchor, features, negatives}" in text
    assert "off_screen?" in text
    assert "OFF-SCREEN LINES" in text


# --- the prompts ------------------------------------------------------------------


@pytest.mark.parametrize("policy", ["auto", "grok", "google-flow"])
def test_locked_carries_the_identity_block_byte_identical_and_first(policy: str) -> None:
    package = _package("locked", policy=policy)
    assert package.cast_lock == "locked"
    seen: dict[str, set[str]] = {}
    for clip in package.clips:
        chars = compiler._characters_in(clip, package)
        if not chars:
            continue
        prompt = compiler.compile_clip_prompt(clip, package).prompt
        for char in chars:
            block = cast.identity_block(char)
            assert block in prompt, (clip.clip_id, char.name)
            seen.setdefault(char.char_id, set()).add(block)
            # early in the prompt — before the anti-artifact tail and the sound
            # direction: identity outranks atmosphere
            assert prompt.index(block) < prompt.index(compiler.ANTI_ARTIFACT)
            assert prompt.index(block) < len(prompt) / 2
    assert seen and all(len(blocks) == 1 for blocks in seen.values())


def test_standard_prompts_carry_no_identity_lock() -> None:
    package = _package("standard")
    assert package.cast_lock == "standard"
    for clip in package.clips:
        assert "IDENTITY LOCK" not in compiler.compile_clip_prompt(clip, package).prompt
    assert all(c.identity is None for c in package.characters)


def test_under_length_pressure_the_card_survives_where_prose_gave_way(monkeypatch) -> None:
    locked = _package("locked", policy="grok")
    standard = _package("standard", policy="grok")
    clip = next(c for c in locked.clips if compiler._characters_in(c, locked))
    twin = next(c for c in standard.clips if c.clip_id == clip.clip_id)
    limit = len(compiler.compile_clip_prompt(twin, standard).prompt) - 120
    monkeypatch.setenv("DIONY_PROMPT_LIMIT_GROK", str(limit))
    prose = compiler.compile_clip_prompt(twin, standard).prompt
    chars = compiler._characters_in(twin, standard)
    # Standard: the character prose is the first thing squeezed
    assert len(prose) <= limit
    assert not all(compiler._character_block(c, "") in prose for c in chars)
    # Locked: the cards are untouchable — every one rides whole
    pressed = compiler.compile_clip_prompt(clip, locked).prompt
    assert len(pressed) <= limit + len(" ".join(cast.identity_block(c) for c in chars))
    for char in compiler._characters_in(clip, locked):
        assert cast.identity_block(char) in pressed


# --- the board sheet ----------------------------------------------------------------


def test_a_locked_package_sheets_with_an_anchor_and_one_board() -> None:
    package = _package("locked")
    ops = artifacts.plan_sheet_ops("s-cast", package)
    by_char: dict[str, list[str]] = {}
    for op in ops:
        by_char.setdefault(op.artifact_refs["bible"]["char_id"], []).append(op.artifact_refs["bible"]["slot"])
    assert by_char and all(slots == ["front", "board"] for slots in by_char.values())
    board = next(op for op in ops if op.artifact_refs["bible"]["slot"] == "board")
    assert "2 by 2 grid" in board.prompt and "Nothing in the hands" in board.prompt
    assert "IDENTITY LOCK" in board.prompt
    front = next(op for op in ops if op.artifact_refs["bible"]["slot"] == "front")
    assert "Anchor" in front.prompt and "IDENTITY LOCK" in front.prompt
    # the board references the anchor it is drawn from
    char = package.character(board.artifact_refs["bible"]["char_id"])
    char.hero_stills["front"] = "/vault/front.png"
    board.provider = board.provider.__class__("grok")
    assert artifacts.references_for(package, board) == ["/vault/front.png"]


def test_standard_sheets_keep_five_angles() -> None:
    package = _package("standard")
    slots = {op.artifact_refs["bible"]["slot"] for op in artifacts.plan_sheet_ops("s-cast", package)}
    assert slots == set(artifacts.SHEET_SLOTS)


def test_identity_refs_lead_with_the_board_and_flow_media_is_capped_at_two() -> None:
    char = CharacterProfile(
        char_id="m", name="M",
        hero_stills={"front": "/f.png", "board": "/b.png", "profile": "/p.png"},
        grok_ref_pack=["/f.png", "/p.png", "/b.png"],
        flow_media_ids={"profile": "user:p", "front": "user:f", "board": "user:b"},
    )
    assert cast.identity_refs(char) == ["/b.png", "/f.png", "/p.png"]
    assert cast.flow_character_media(char) == ["user:f", "user:b"]


def test_grok_reference_pack_under_locked_leads_with_each_boards_then_the_plate() -> None:
    package = _two_hander(off_screen=False, cast_lock="locked", policy="grok")
    for char in package.characters:
        char.hero_stills = {"front": f"/{char.char_id}-front.png", "board": f"/{char.char_id}-board.png"}
        char.grok_ref_pack = list(char.hero_stills.values())
    package.settings[0].plate_asset_id = "/plate.png"
    pictures = [c for c in package.clips if not c.audio_packet.script_excerpt]
    # a scene opener with people composes INTO the plate (the plate leads —
    # the s-1b80548c4d doctrine); every later shot leads with the boards
    opener, later = pictures[0], pictures[-1]
    assert compiler.provider_artifact_refs(opener, package)["references"][0] == "/plate.png"
    refs = compiler.provider_artifact_refs(later, package)
    assert refs["references"][:2] == ["/mara-board.png", "/jon-board.png"]
    assert "/plate.png" in refs["references"] and len(refs["references"]) <= 3
    roles = [r["kind"] for r in refs["reference_roles"]]
    assert roles[:2] == ["character", "character"] and "plate" in roles
    assert refs["reference_roles"][0]["label"] == "MARA"


# --- measured sheets and the desk's face models --------------------------------------


def test_an_unstaged_desk_reports_an_honest_unmeasured_sheet(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DIONY_FACE_ID_DETECTOR_MODEL", raising=False)
    monkeypatch.delenv("DIONY_FACE_ID_RECOGNIZER_MODEL", raising=False)
    monkeypatch.setenv("DIONY_FACE_ID_DIR", str(tmp_path / "nothing"))
    char = CharacterProfile(char_id="m", name="M")
    assert cast.measure_sheet(char)["notes"] == ["no anchor still yet"]
    (tmp_path / "front.png").write_bytes(b"png" * 100)
    (tmp_path / "board.png").write_bytes(b"png" * 100)
    char.hero_stills = {"front": str(tmp_path / "front.png"), "board": str(tmp_path / "board.png")}
    receipt = cast.measure_sheet(char)
    assert receipt["measured"] is False
    assert any("stage-face-models" in note for note in receipt["notes"])
    char.sheet_receipt = receipt
    assert cast.sheet_summary(char).startswith("sheet unmeasured")
    assert cast.face_models_staged() is False


def test_stage_face_models_lands_both_files_where_the_producer_looks(tmp_path, monkeypatch) -> None:
    from diony_producer import face_identity

    monkeypatch.delenv("DIONY_FACE_ID_DETECTOR_MODEL", raising=False)
    monkeypatch.delenv("DIONY_FACE_ID_RECOGNIZER_MODEL", raising=False)
    monkeypatch.setenv("DIONY_FACE_ID_DIR", str(tmp_path / "zoo"))
    import hashlib

    fetched: list[str] = []
    payload = b"onnx-bytes"
    monkeypatch.setattr(cast, "FACE_MODEL_SHA256", {name: hashlib.sha256(payload).hexdigest() for name in cast.FACE_MODEL_SOURCES})

    def fetch(url: str) -> bytes:
        fetched.append(url)
        return payload

    detector, recognizer = cast.stage_face_models(fetch=fetch)
    assert detector.name == face_identity.DETECTOR_FILENAME and recognizer.name == face_identity.RECOGNIZER_FILENAME
    # the zoo keeps its weights in Git LFS — only the media host serves the model itself
    assert len(fetched) == 2 and all(u.startswith("https://media.githubusercontent.com/media/opencv/opencv_zoo/") for u in fetched)
    assert face_identity.staged_face_models() == (detector, recognizer)
    assert cast.face_models_staged() is True
    # a second staging keeps the files and fetches nothing
    cast.stage_face_models(fetch=fetch)
    assert len(fetched) == 2


def test_staging_refuses_an_lfs_pointer_or_a_wrong_hash_and_replaces_a_bad_file(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DIONY_FACE_ID_DETECTOR_MODEL", raising=False)
    monkeypatch.delenv("DIONY_FACE_ID_RECOGNIZER_MODEL", raising=False)
    monkeypatch.setenv("DIONY_FACE_ID_DIR", str(tmp_path / "zoo"))
    pointer = b"version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 232589\n"
    with pytest.raises(RuntimeError, match="Git LFS pointer"):
        cast.stage_face_models(fetch=lambda url: pointer)
    with pytest.raises(RuntimeError, match="SHA-256 does not match"):
        cast.stage_face_models(fetch=lambda url: b"not the model")
    assert cast.face_models_staged() is False
    # a pointer left behind by an earlier attempt is replaced, never trusted
    import hashlib

    real = b"onnx-bytes"
    monkeypatch.setattr(cast, "FACE_MODEL_SHA256", {name: hashlib.sha256(real).hexdigest() for name in cast.FACE_MODEL_SOURCES})
    (tmp_path / "zoo").mkdir(parents=True, exist_ok=True)
    (tmp_path / "zoo" / "face_detection_yunet_2023mar.onnx").write_bytes(pointer)
    cast.stage_face_models(fetch=lambda url: real)
    assert (tmp_path / "zoo" / "face_detection_yunet_2023mar.onnx").read_bytes() == real
    assert cast.face_models_staged() is True


def test_the_sheet_verdict_names_the_slot_that_reads_as_another_person(monkeypatch, tmp_path) -> None:
    from diony_producer.face_identity import FaceIdentityReceipt

    scores = {"board": 0.71, "full": 0.21}

    def fake(reference: Path, candidate: Path):
        slot = candidate.stem
        return FaceIdentityReceipt(True, reference_faces=1, frames_sampled=1, frames_with_face=1,
                                   similarities=(scores[slot],), similarity_min=scores[slot],
                                   similarity_mean=scores[slot], verified=scores[slot] >= 0.363, threshold=0.363)

    monkeypatch.setattr(cast, "_still_identity", fake)
    for name in ("front", "board", "full"):
        (tmp_path / f"{name}.png").write_bytes(b"png" * 10)
    char = CharacterProfile(char_id="m", name="MARA", hero_stills={n: str(tmp_path / f"{n}.png") for n in ("front", "board", "full")})
    receipt = cast.measure_sheet(char)
    assert receipt["measured"] and receipt["verified"] is False
    assert receipt["failed"] == ["full"] and receipt["min"] == 0.21
    char.sheet_receipt = receipt
    assert "full read as another person" in cast.sheet_summary(char)


def test_the_identity_family_measures_every_packed_character(monkeypatch, tmp_path) -> None:
    from diony_producer.face_identity import FaceIdentityReceipt

    package = _two_hander(off_screen=False, cast_lock="locked", policy="grok")
    picture = next(c for c in package.clips if len(c.continuity.character_packs) == 2)
    for char in package.characters:
        still = tmp_path / f"{char.char_id}.png"
        still.write_bytes(b"png" * 200)
        char.hero_stills["front"] = str(still)
    clip_path = tmp_path / "clip.mp4"
    clip_path.write_bytes(b"clip" * 1024)

    def receipt_for(clip, still, frame_count):
        good = "mara" in str(still)
        score = 0.62 if good else 0.2
        return FaceIdentityReceipt(True, reference_faces=1, frames_sampled=4, frames_with_face=4,
                                   similarities=(score,), similarity_min=score, similarity_mean=score,
                                   verified=good, threshold=0.363)

    monkeypatch.setattr(review, "_face_receipt", receipt_for)
    result = review.review_clip(clip_path, picture, package=package)
    assert result.families["identity"] == "fail"
    assert result.measured["identity_min:mara"] == 0.62 and result.measured["identity_min:jon"] == 0.2
    assert any("JON reads 0.2" in issue for issue in result.issues)


# --- provider identity markers ----------------------------------------------------------


def test_the_grok_legend_tags_images_and_the_flow_legend_tags_markers() -> None:
    roles = [cast.role("character", "MARA"), cast.role("plate"), cast.role("bridge")]
    grok_text = cast.legend(roles, "grok")
    assert grok_text.startswith("<IMAGE_1> is MARA's identity sheet")
    assert "<IMAGE_2> is the setting plate" in grok_text and "<IMAGE_3> is the previous frame" in grok_text
    flow_text = cast.legend(roles[:1], "google-flow", characters=["JON"])
    assert flow_text.startswith("@character_1 is JON")
    assert "@referenceImage_1 is MARA's identity sheet" in flow_text


def test_grok_submit_appends_the_legend_only_when_references_ship(monkeypatch, tmp_path) -> None:
    from diony_contracts.director import CloudGenerationMode, CloudProvider, DirectorOp, DirectorOpType

    sent: list[dict] = []

    class _Resp:
        status_code = 200

        def json(self):
            return {"request_id": "req-1"}

        text = "{}"

    monkeypatch.setattr(base, "post_with_retries", lambda url, **kw: sent.append(kw["json"]) or _Resp())
    still = tmp_path / "board.png"
    still.write_bytes(b"png" * 10)
    op = DirectorOp(
        id="op-1", session_id="s", op=DirectorOpType.GENERATE, provider=CloudProvider.GROK,
        model="grok-imagine-video-1.5", mode=CloudGenerationMode.R2V, prompt="A shot.", duration_s=6,
        artifact_refs={"references": [str(still)], "reference_roles": [cast.role("character", "MARA")]},
        estimate=_estimate(),
    )
    grok.submit(op)
    assert sent[-1]["prompt"] == "A shot. <IMAGE_1> is MARA's identity sheet — match this exact face, build, hair, and wardrobe."
    op.artifact_refs = {"start_image": str(still), "reference_roles": [cast.role("character", "MARA")]}
    grok.submit(op)
    assert sent[-1]["prompt"] == "A shot."  # a frame-locked clip sends no images to tag


def test_flow_submit_names_only_the_slots_it_carries(monkeypatch) -> None:
    from diony_contracts.director import CloudGenerationMode, CloudProvider, DirectorOp, DirectorOpType

    sent: list[dict] = []

    class _Resp:
        status_code = 200

        def json(self):
            return {"jobId": "j1"}

        text = "{}"

    monkeypatch.setattr(flow, "_post", lambda url, **kw: sent.append(kw["json"]) or _Resp())
    monkeypatch.setattr(flow, "_media_ref", lambda value, what="": value)
    op = DirectorOp(
        id="op-2", session_id="s", op=DirectorOpType.GENERATE, provider=CloudProvider.GOOGLE_FLOW,
        model="omni-flash", mode=CloudGenerationMode.R2V, prompt="A shot.", duration_s=8,
        artifact_refs={
            "characters": ["char-1"], "character_names": ["MARA"],
            "references": ["user:plate"], "reference_roles": [cast.role("plate"), cast.role("bridge")],
        },
        estimate=_estimate(),
    )
    flow.submit(op)
    prompt = sent[-1]["prompt"]
    assert "@character_1 is MARA" in prompt and "@referenceImage_1 is the setting plate" in prompt
    assert "@referenceImage_2" not in prompt  # no second slot in the body → no dangling marker
    assert sent[-1]["character_1"] == "char-1" and sent[-1]["referenceImage_1"] == "user:plate"


def test_grok_image_edit_sends_one_image_or_a_list_never_both(monkeypatch, tmp_path) -> None:
    sent: list[dict] = []

    class _Resp:
        status_code = 200

        def json(self):
            return {"data": [{"url": "https://x/img.png"}]}

        text = "{}"

    monkeypatch.setattr(base, "post_with_retries", lambda url, **kw: sent.append({"url": url, **kw["json"]}) or _Resp())
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    a.write_bytes(b"a" * 10)
    b.write_bytes(b"b" * 10)
    handle = grok.submit_image_edit("Draw the board.", [str(a)], aspect_ratio="1:1")
    assert sent[-1]["url"].endswith("/v1/images/edits")
    assert sent[-1]["image"]["type"] == "image_url" and "image_urls" not in sent[-1]
    assert sent[-1]["aspect_ratio"] == "1:1"
    assert handle.metadata["sync_result_url"] == "https://x/img.png"
    grok.submit_image_edit("Compose the frame.", [str(a), str(b)] * 4)
    assert "image" not in sent[-1] and len(sent[-1]["image_urls"]) == grok.MAX_EDIT_IMAGES


def test_flow_character_registration_sends_two_images_a_voice_and_notes(monkeypatch) -> None:
    sent: list[dict] = []

    class _Resp:
        status_code = 200

        def json(self):
            return {"entityId": "char-9"}

        text = "{}"

    monkeypatch.setattr(flow, "_post", lambda url, **kw: sent.append(kw["json"]) or _Resp())
    monkeypatch.setattr(flow, "_media_ref", lambda value, what="": value)
    ref = flow.register_character("MARA", ["user:f", "user:b", "user:p"], voice="Leda", notes="34, lean")
    assert ref == "char-9"
    assert sent[-1]["imageReference_1"] == "user:f" and sent[-1]["imageReference_2"] == "user:b"
    assert "imageReference_3" not in sent[-1]
    assert sent[-1]["voice"] == "Leda" and sent[-1]["personalityNotes"] == "34, lean"


# --- off-screen lines ---------------------------------------------------------------------


def test_parse_speaker_reads_screenplay_tags() -> None:
    assert cast.parse_speaker("MARA (O.S.)") == ("MARA", "os")
    assert cast.parse_speaker("MARA (OS)") == ("MARA", "os")
    assert cast.parse_speaker("MARA (off-screen)") == ("MARA", "os")
    assert cast.parse_speaker("JON (V.O.)") == ("JON", "vo")
    assert cast.parse_speaker("JON (CONT'D)") == ("JON", "")
    assert cast.parse_speaker("MARA (O.S.) (CONT'D)") == ("MARA", "os")
    assert cast.parse_speaker("MARA") == ("MARA", "")


def test_an_off_screen_line_holds_on_the_listener_and_packs_the_visible_cast() -> None:
    package = _two_hander(off_screen=True)
    spoken = next(c for c in package.clips if c.audio_packet.script_excerpt)
    assert spoken.clip_class == "sync-dialogue"
    assert spoken.audio_packet.on_camera is False
    assert spoken.audio_packet.script_excerpt[0]["off_screen"] is True
    assert spoken.audio_packet.script_excerpt[0]["listeners"] == ["JON"]
    assert [p["char_id"] for p in spoken.continuity.character_packs] == ["jon"]
    assert "Medium close-up on JON, listening" in spoken.camera_idea
    assert "MARA stays out of frame" in spoken.camera_idea
    on_camera = next(c for c in _two_hander(off_screen=False).clips if c.audio_packet.script_excerpt)
    assert on_camera.audio_packet.on_camera is True
    assert [p["char_id"] for p in on_camera.continuity.character_packs] == ["mara"]


def test_an_off_screen_line_compiles_as_heard_not_seen_under_every_style() -> None:
    narrated = _two_hander(off_screen=True, style="narrated")
    clip = next(c for c in narrated.clips if c.audio_packet.script_excerpt)
    prompt = compiler.compile_clip_prompt(clip, narrated).prompt
    assert clip.audio_packet.voicing == "post"
    assert "MARA speaks from off-screen" in prompt and "no one on screen forms words" in prompt
    assert compiler.NO_SPEECH in prompt and "Keys are on the hook" not in prompt
    assert "the face is never visible" not in prompt  # the listener's face is the shot

    for style in ("dialogue", "mix"):
        package = _two_hander(off_screen=True, style=style)
        clip = next(c for c in package.clips if c.audio_packet.script_excerpt)
        assert clip.audio_packet.voicing == "generated", style
        prompt = compiler.compile_clip_prompt(clip, package).prompt
        assert 'exactly these words and nothing else: "Keys are on the hook. Don\'t wait up."' in prompt
        assert "OFF-SCREEN" in prompt and "no one on screen mouths the words" in prompt
        assert compiler.ONLY_THE_LINE in prompt


def test_mix_voices_an_off_screen_line_in_the_clip_but_keeps_voice_over_in_post() -> None:
    assert styles.voicing_for("mix", on_camera=False, off_screen=True) == "generated"
    assert styles.voicing_for("mix", on_camera=False) == "post"
    assert styles.voicing_for("narrated", on_camera=False, off_screen=True) == "post"


def test_subtitles_and_cues_carry_the_off_screen_speaker() -> None:
    from diony_director import narration

    package = _two_hander(off_screen=True)
    clip = next(c for c in package.clips if c.audio_packet.script_excerpt)
    entries = [{"op": type("Op", (), {"id": "op-x", "narration": ""})(), "clip": clip, "window": (0.0, float(clip.duration_s))}]
    cues = narration.cue_sheet(entries)
    assert cues and cues[0].speaker == "MARA" and cues[0].line.startswith("Keys are on the hook")
    cue_list = deliverables.subtitle_cues(entries) if hasattr(deliverables, "subtitle_cues") else None
    if cue_list is not None:
        assert cue_list[0].speaker == "MARA (O.S.)"


def test_the_screenwriter_parses_an_os_cue_and_the_card_from_the_brain(monkeypatch) -> None:
    monkeypatch.setenv("DIONY_DIRECTOR_BRAIN_URL", "http://127.0.0.1:9999")
    monkeypatch.setenv("DIONY_DIRECTOR_BRAIN_REFINE", "0")
    monkeypatch.setenv("DIONY_LIKENESS_AUDIT", "0")
    body = {
        "logline": "A driver leaves.", "beats": [{"beat": "leave", "story_time": "night"}], "real_people": [],
        "settings": [{"setting_id": "d", "name": "the diner", "kind": "INT", "room_tone": "hum", "time_weather": "night", "grade_language": "teal", "camera_kit": "50mm"}],
        "characters": [
            {"char_id": "mara", "name": "MARA", "age_body_notes": "34, lean", "wardrobe": "coat", "identity": {"anchor": "34, lean, oval face", "features": ["auburn hair"]}},
            {"char_id": "jon", "name": "JON", "age_body_notes": "50, heavy", "wardrobe": "coveralls"},
        ],
        "scenes": [{
            "heading": "INT. DINER - NIGHT", "setting_ref": "d", "story_time": "night", "cast": ["mara", "jon"], "emotional_job": "leave",
            "lines": [
                {"kind": "action", "text": "Jon wipes the counter with a grey rag.", "shot": "wide"},
                {"kind": "dialogue", "text": "Keys are on the hook.", "speaker": "MARA (O.S.)", "shot": "close on Jon"},
                {"kind": "dialogue", "text": "Some towns you leave.", "speaker": "JON (V.O.)", "shot": "the road"},
                {"kind": "dialogue", "text": "Don't wait up.", "speaker": "MARA", "off_screen": True, "shot": "close on Jon"},
                {"kind": "dialogue", "text": "Drive safe.", "speaker": "JON", "shot": "close on Jon"},
            ],
        }],
    }

    class _Response:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"choices": [{"message": {"content": json.dumps(body)}}]}

    monkeypatch.setattr(screenwriter.httpx, "post", lambda *a, **k: _Response())
    draft = screenwriter.write_script("a driver leaves", runtime_target_s=30)
    lines = {l.text: l for l in draft.script.lines}
    assert lines["Keys are on the hook."].off_screen is True and lines["Keys are on the hook."].speaker == "MARA"
    assert lines["Some towns you leave."].kind == "vo" and lines["Some towns you leave."].speaker == "JON"
    assert lines["Don't wait up."].off_screen is True
    assert lines["Drive safe."].off_screen is False
    # the cue line carries the clean name; the tag lives on the line
    assert lines["MARA"].kind == "cue"
    mara = next(c for c in draft.characters if c.char_id == "mara")
    assert mara.identity is not None and mara.identity.source == "brain" and mara.identity.anchor == "34, lean, oval face"
    jon = next(c for c in draft.characters if c.char_id == "jon")
    assert jon.identity is None  # derived at lock under Locked, never invented here


def test_a_dialogue_line_can_be_made_off_screen_until_the_lock() -> None:
    client = _client()
    sid = start(client)
    client.post(f"/api/director/sessions/{sid}/produce", json={"idea": IDEA})
    board = client.get(f"/api/director/sessions/{sid}/board").json()
    dialogue = next(l for l in board["script"]["lines"] if l["kind"] == "dialogue")
    action = next(l for l in board["script"]["lines"] if l["kind"] == "action")
    res = client.post(f"/api/director/sessions/{sid}/draft/line", json={"line_id": dialogue["line_id"], "off_screen": True})
    assert res.status_code == 200
    line = next(l for l in res.json()["script"]["lines"] if l["line_id"] == dialogue["line_id"])
    assert line["off_screen"] is True
    assert client.post(f"/api/director/sessions/{sid}/draft/line", json={"line_id": action["line_id"], "off_screen": True}).status_code == 422
    client.post(f"/api/director/sessions/{sid}/lock_script")
    assert client.post(f"/api/director/sessions/{sid}/draft/line", json={"line_id": dialogue["line_id"], "off_screen": False}).status_code == 409
    package = session.package_for(sid)
    spoken = next(c for c in package.clips if c.script_line_ids == [dialogue["line_id"]])
    assert spoken.audio_packet.script_excerpt[0]["off_screen"] is True


# --- the session setting ----------------------------------------------------------------


def test_the_cast_lock_is_a_session_setting_fixed_at_the_lock() -> None:
    client = _client()
    sid = start(client)
    catalog = client.get("/api/director/cast/modes").json()
    assert catalog["default"] == "standard" and [m["key"] for m in catalog["modes"]] == ["standard", "locked"]
    assert "face_models_staged" in catalog
    assert client.get(f"/api/director/sessions/{sid}/settings").json()["cast_lock"] == "standard"
    assert client.post(f"/api/director/sessions/{sid}/cast", json={"cast": "welded"}).status_code == 422
    assert client.post(f"/api/director/sessions/{sid}/cast", json={"cast": "locked"}).json()["cast_lock"] == "locked"
    assert client.get(f"/api/director/sessions/{sid}/settings").json()["cast_lock"] == "locked"
    # persisted and rehydrated
    state = json.loads((session._state_path(sid)).read_text())
    assert state["cast_lock"] == "locked"
    session.reset()
    session.rehydrate_from_vault()
    assert session.cast_mode_for(sid) == "locked"
    client.post(f"/api/director/sessions/{sid}/produce", json={"idea": IDEA})
    client.post(f"/api/director/sessions/{sid}/lock_script")
    res = client.post(f"/api/director/sessions/{sid}/cast", json={"cast": "standard"})
    assert res.status_code == 409 and "cannot change mid-production" in res.json()["detail"]
    # a message that omits the field keeps it; the same value is a no-op
    assert client.post(f"/api/director/sessions/{sid}/message", json={"text": "a lone car on a wet road", "cast": "locked"}).status_code == 200


def test_a_locked_lock_plans_a_keyframe_for_every_clip_that_shows_the_cast() -> None:
    client = _client()
    sid = start(client)
    client.post(f"/api/director/sessions/{sid}/cast", json={"cast": "locked"})
    client.post(f"/api/director/sessions/{sid}/produce", json={"idea": IDEA})
    body = client.post(f"/api/director/sessions/{sid}/lock_script").json()
    package = session.package_for(sid)
    assert package.cast_lock == "locked"
    assert all(c.identity is not None for c in package.characters)
    with_cast = [c for c in package.clips if compiler._characters_in(c, package)]
    keyframes = [
        op for op in session.ops_for(sid)
        if (op.artifact_refs.get("style_frame") or {}).get("keyframe")
    ]
    assert len(keyframes) == len(with_cast) > 0
    for op in session.ops_for(sid):
        if op.clip_id and op.op.value == "generate":
            frame = session.style_frame_op_for(op)
            clip = session.clip_for_op(op)
            assert (frame is not None) == bool(compiler._characters_in(clip, package)), clip.clip_id
            if frame is not None:
                assert "IDENTITY LOCK" in frame.prompt and "Opening frame of a shot" in frame.prompt
                assert "opens from a cast keyframe" in op.rationale
    labels = [line.label for line in package.estimate.lines]
    assert any(label.startswith("cast keyframes ×") for label in labels)
    assert "cast keyframe" in body["reply"]
    # keyframes are never batch targets of their own — they roll inside the clip's submit
    assert not any(op.id in session.batch_targets(sid) for op in keyframes)
    board = client.get(f"/api/director/sessions/{sid}/board").json()
    assert board["cast_lock"] == "locked"
    assert all(c["identity_block"].startswith("IDENTITY LOCK") for c in board["characters"])


def test_a_standard_lock_plans_no_keyframes() -> None:
    client = _client()
    sid = start(client)
    client.post(f"/api/director/sessions/{sid}/produce", json={"idea": IDEA})
    client.post(f"/api/director/sessions/{sid}/lock_script")
    assert not any((op.artifact_refs.get("style_frame") or {}).get("keyframe") for op in session.ops_for(sid))
    assert not any(line.label.startswith("cast keyframes") for line in session.package_for(sid).estimate.lines)


# --- the whole production under Cast Lock -----------------------------------------------


def test_a_locked_production_opens_every_cast_clip_from_its_keyframe(monkeypatch, tmp_path) -> None:
    client = _client()
    fake_adapters(monkeypatch, tmp_path)
    edits: list[dict] = []
    registered: list[dict] = []

    def fake_edit(prompt, images, *, model=grok.IMAGE_MODEL, aspect_ratio="", resolution=""):
        edits.append({"prompt": prompt, "images": list(images), "aspect": aspect_ratio})
        return base.CloudJobHandle(provider_id="grok", external_id=f"grok-edit-{len(edits)}", model=model, duration_s=0, metadata={"kind": "image"})

    def fake_register(name, media, *, voice="", notes=""):
        registered.append({"name": name, "media": list(media), "voice": voice, "notes": notes})
        return f"flowchar-{len(registered)}"

    monkeypatch.setattr(grok, "submit_image_edit", fake_edit)
    monkeypatch.setattr(flow, "register_character", fake_register)
    monkeypatch.delenv("USEAPI_TOKEN", raising=False)  # one lane: sheets, keyframes, and clips on Grok
    from diony_contracts.cluster import write_engine_choice

    write_engine_choice("grok")
    sid = start(client)
    client.post(f"/api/director/sessions/{sid}/cast", json={"cast": "locked"})
    client.post(f"/api/director/sessions/{sid}/produce", json={"idea": IDEA})
    client.post(f"/api/director/sessions/{sid}/lock_script")
    client.post(f"/api/director/sessions/{sid}/run_batch", json={"confirm": True})
    last = drive(client, sid)
    failures = {
        op.id: (op.clip_id, session.runtime(op.id).get("submit_error"), session.runtime(op.id).get("failure"))
        for op in session.ops_for(sid) if op.status == "failed"
    }
    assert not failures, failures
    assert last["pending"] == 0
    package = session.package_for(sid)
    for char in package.characters:
        assert set(char.hero_stills) == {"front", "board"}, char.name
        assert char.sheet_receipt.get("measured") is False  # no face models on this desk — said so
        assert "unmeasured" in char.sheet_receipt.get("notes", [""])[0] or char.sheet_receipt.get("notes")
    # every board was drawn FROM its anchor on the edit lane, square
    boards = [e for e in edits if e["aspect"] == "1:1"]
    assert len(boards) == len(package.characters)
    assert all(len(e["images"]) == 1 for e in boards)
    # every cast clip opened from its keyframe, and the keyframe composed from the board + plate
    for op in session.ops_for(sid):
        if not op.clip_id or op.op.value != "generate":
            continue
        clip = session.clip_for_op(op)
        chars = compiler._characters_in(clip, package)
        if not chars:
            continue
        frame = session.style_frame_op_for(op)
        assert frame is not None and frame.status == "completed", clip.clip_id
        keyframe = session.runtime(op.id).get("style_frame")
        assert keyframe and op.artifact_refs.get("start_image") == keyframe, clip.clip_id
        assert not op.artifact_refs.get("references")
    keyframe_edits = [e for e in edits if e["aspect"] != "1:1"]
    assert keyframe_edits and all(len(e["images"]) >= 2 for e in keyframe_edits)
    assert all("IDENTITY LOCK" in e["prompt"] for e in keyframe_edits)
    assert not registered  # no Flow lane configured → no Flow character
    assert Path(session.runtime(next(op.id for op in session.ops_for(sid) if op.clip_id)).get("clip").path).is_file()


def test_on_the_mixed_lane_flow_characters_carry_dialogue_and_keyframes_carry_pictures(monkeypatch, tmp_path) -> None:
    client = _client()
    fake_adapters(monkeypatch, tmp_path)
    images: list[dict] = []
    real_submit_image = flow.submit_image

    def recording_submit_image(prompt, *, model, references=None, aspect_ratio="landscape", resolution=""):
        images.append({"prompt": prompt, "references": list(references or [])})
        return real_submit_image(prompt, model=model, references=references, aspect_ratio=aspect_ratio, resolution=resolution)

    monkeypatch.setattr(flow, "submit_image", recording_submit_image)
    monkeypatch.setattr(
        flow, "register_character",
        lambda name, media, *, voice="", notes="": f"flowchar-{name.lower()}",
    )
    sid = start(client)
    client.post(f"/api/director/sessions/{sid}/cast", json={"cast": "locked"})
    client.post(f"/api/director/sessions/{sid}/produce", json={"idea": IDEA})
    client.post(f"/api/director/sessions/{sid}/lock_script")
    client.post(f"/api/director/sessions/{sid}/run_batch", json={"confirm": True})
    drive(client, sid)
    failures = {op.id: session.runtime(op.id).get("submit_error") for op in session.ops_for(sid) if op.status == "failed"}
    assert not failures, failures
    package = session.package_for(sid)
    for char in package.characters:
        assert set(char.hero_stills) == {"front", "board"}
        assert char.flow_character_ref == f"flowchar-{char.name.lower()}"
    # every board was drawn with its anchor as the reference
    boards = [i for i in images if "2 by 2 grid" in i["prompt"]]
    assert len(boards) == len(package.characters) and all(len(b["references"]) == 1 for b in boards)
    for op in session.ops_for(sid):
        if not op.clip_id or op.op.value != "generate":
            continue
        clip = session.clip_for_op(op)
        if not compiler._characters_in(clip, package):
            continue
        rt = session.runtime(op.id)
        if op.provider.value == "grok":
            # a picture clip on Grok opens from its keyframe
            assert rt.get("style_frame") and op.artifact_refs.get("start_image") == rt["style_frame"], clip.clip_id
        else:
            # a dialogue clip on Flow carries the character object; frames yield to it
            assert op.artifact_refs.get("characters"), clip.clip_id
            assert op.artifact_refs.get("character_names"), clip.clip_id
            assert not op.artifact_refs.get("start_image"), clip.clip_id
            assert rt.get("style_frame_skipped"), clip.clip_id
    keyframes = [i for i in images if "Opening frame of a shot" in i["prompt"]]
    assert keyframes and all(len(k["references"]) >= 2 for k in keyframes)


def test_a_locked_flow_sheet_registers_its_character_and_a_bad_board_is_redrawn(monkeypatch, tmp_path) -> None:
    from diony_producer.face_identity import FaceIdentityReceipt

    client = _client()
    fake_adapters(monkeypatch, tmp_path)
    registered: list[dict] = []
    monkeypatch.setattr(
        flow, "register_character",
        lambda name, media, *, voice="", notes="": registered.append({"name": name, "media": list(media), "voice": voice, "notes": notes}) or f"flowchar-{len(registered)}",
    )
    verdicts = {"n": 0}

    def fake_still(reference: Path, candidate: Path):
        verdicts["n"] += 1
        score = 0.2 if verdicts["n"] == 1 else 0.7  # the first board of the film reads wrong once
        return FaceIdentityReceipt(True, reference_faces=1, frames_sampled=1, frames_with_face=1,
                                   similarities=(score,), similarity_min=score, similarity_mean=score,
                                   verified=score >= 0.363, threshold=0.363)

    monkeypatch.setattr(cast, "_still_identity", fake_still)
    sid = start(client)
    client.post(f"/api/director/sessions/{sid}/cast", json={"cast": "locked"})
    client.post(f"/api/director/sessions/{sid}/produce", json={"idea": IDEA})
    client.post(f"/api/director/sessions/{sid}/lock_script")
    client.post(f"/api/director/sessions/{sid}/run_batch", json={"confirm": True})
    drive(client, sid)
    package = session.package_for(sid)
    redrawn = [c for c in package.characters if c.sheet_retries.get("board")]
    assert len(redrawn) == 1 and redrawn[0].sheet_retries["board"] == 1
    for char in package.characters:
        assert set(char.hero_stills) == {"front", "board"}, char.name
        assert char.sheet_receipt["verified"] is True and char.sheet_receipt["scores"]["board"] == 0.7
        assert char.flow_character_ref.startswith("flowchar-")
    assert len(registered) == len(package.characters)
    assert all(len(r["media"]) == 2 and r["notes"] for r in registered)
    board = client.get(f"/api/director/sessions/{sid}/board").json()
    assert all(c["flow_registered"] and c["sheet"].startswith("sheet coheres") for c in board["characters"])
    # the human redraw throws a still away and the new op joins the bible gate
    char = package.characters[0]
    res = client.post(f"/api/director/sessions/{sid}/bible/redraw/{char.char_id}/board")
    assert res.status_code == 200 and res.json()["ops"]
    assert "board" not in char.hero_stills and char.face_approved is False
    new_id = res.json()["ops"][0]["id"]
    seq = session._SEQUENCES[sid]
    assert new_id in seq["bible"] and any(new_id in chain for chain in seq["bible_chains"])
    assert client.post(f"/api/director/sessions/{sid}/bible/redraw/{char.char_id}/front").status_code == 409
