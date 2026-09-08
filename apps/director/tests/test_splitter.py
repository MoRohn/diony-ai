"""Script dissection: one clip = one camera idea + a speakable audio load."""

from diony_contracts.director import CloudProvider
from diony_contracts.production import Scene, Script, ScriptLine
from diony_director import screenwriter, splitter


def _draft():
    return screenwriter.write_script(
        "a getaway driver decides to leave town from a neon diner", runtime_target_s=60
    )


def test_every_scene_opens_with_an_establishing_clip() -> None:
    draft = _draft()
    lines = {l.line_id: l for l in draft.script.lines}
    clips = splitter.split_script(draft.script, draft.characters, draft.settings)
    by_scene: dict[str, list] = {}
    for clip in clips:
        by_scene.setdefault(clip.scene_id, []).append(clip)
    for scene_clips in by_scene.values():
        first = min(scene_clips, key=lambda c: c.order)
        opener_line = lines[first.script_line_ids[0]]
        if opener_line.kind != "action":
            continue  # a scene that opens on a line buys no empty establishing clip
        authored = (opener_line.shot or "").rstrip(".")
        if first.audio_packet.script_excerpt:
            # Blueprint 33: the opener beat and the scene's first line share a
            # take — the take rides the voice lane, and the authored opener
            # still leads its camera
            if authored:
                assert first.camera_idea.startswith(authored)
            continue
        assert first.clip_class in {"ambience-led", "silent-picture", "score-led"}
        if authored:
            # the screenwriter's frame IS the opener; and a non-wide authored
            # opener must not force the wide plate as its first frame
            assert first.camera_idea.startswith(authored)
            lowered = authored.lower()
            wide_ish = any(
                w in lowered for w in ("wide", "establish", "master", "aerial", "overhead", "top-")
            )
            assert first.continuity.lock_recipe == ("still" if wide_ish else "ref")
        else:
            assert "establish" in first.camera_idea or "wide" in first.camera_idea
            assert first.continuity.lock_recipe == "still"


def test_a_dialogue_line_stays_inside_one_clip_with_its_words() -> None:
    draft = _draft()
    clips = splitter.split_script(draft.script, draft.characters, draft.settings)
    dialogue_clips = [c for c in clips if c.clip_class == "sync-dialogue"]
    assert dialogue_clips
    for clip in dialogue_clips:
        excerpt = clip.audio_packet.script_excerpt
        assert len(excerpt) == 1
        assert excerpt[0]["line"]
        assert clip.audio_packet.voice_ref


def test_dialogue_routes_to_the_voice_capable_lane() -> None:
    draft = _draft()
    clips = splitter.split_script(draft.script, draft.characters, draft.settings)
    for clip in clips:
        if clip.clip_class in {"sync-dialogue", "vo-over"}:
            assert clip.provider is CloudProvider.GOOGLE_FLOW
            assert clip.model == "omni-flash"
        else:
            assert clip.provider is CloudProvider.GROK


def test_duration_comes_from_the_spoken_words_not_wishful_runtime() -> None:
    lines = [
        ScriptLine(line_id="ln-1", kind="heading", text="INT. DINER - NIGHT", scene_id="sc-1"),
        ScriptLine(line_id="ln-2", kind="action", text="Mara waits.", scene_id="sc-1"),
        ScriptLine(
            line_id="ln-3",
            kind="dialogue",
            text=(
                "Listen to me very carefully because I am only going to explain this "
                "entire plan one single time before we drive out of this town forever tonight."
            ),
            speaker="MARA",
            scene_id="sc-1",
        ),
    ]
    script = Script(
        id="scr-t",
        runtime_target_s=30,
        scenes=[
            Scene(
                scene_id="sc-1",
                heading="INT. DINER - NIGHT",
                setting_ref="set-primary",
                cast=["chr-lead"],
                line_ids=[l.line_id for l in lines],
            )
        ],
        lines=lines,
    )
    draft = _draft()
    clips = splitter.split_script(script, draft.characters, draft.settings)
    long_line = next(c for c in clips if c.clip_class == "sync-dialogue")
    # ~29 words at ~2.5 wps ≈ 11.6s + handles → quantizes up to the Omni max of 10
    # is illegal, so the line must ride the longest legal Omni duration.
    assert long_line.duration_s == 10


def test_same_scene_clips_share_room_tone() -> None:
    draft = _draft()
    clips = splitter.split_script(draft.script, draft.characters, draft.settings)
    for scene in draft.script.scenes:
        tones = {
            c.audio_packet.room_tone_id for c in clips if c.scene_id == scene.scene_id
        }
        assert len(tones) == 1


def test_continuity_locks_attach_character_packs_and_plates() -> None:
    draft = _draft()
    clips = splitter.split_script(draft.script, draft.characters, draft.settings)
    for clip in clips:
        assert clip.continuity.setting_plate.get("setting_id")
        if clip.clip_class == "sync-dialogue":
            assert clip.continuity.lock_recipe == "character_asset"
            assert clip.continuity.character_packs
