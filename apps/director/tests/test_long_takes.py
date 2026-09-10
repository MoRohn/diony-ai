"""Blueprint 33 — long takes: a scene's beats pack into takes as long as the
route allows and as even as the beats permit; one spoken line per take; the
words sit where the beats put them; the trim follows the take, not the words."""

from __future__ import annotations

from diony_contracts.director import CloudProvider
from diony_contracts.production import Scene, Script, ScriptLine
from diony_director import app as app_module
from diony_director import narration, screenwriter, session, splitter


def _draft():
    return screenwriter.write_script(
        "a getaway driver decides to leave town from a neon diner", runtime_target_s=60
    )


def _script(lines: list[ScriptLine], cast: list[str] | None = None) -> Script:
    return Script(
        id="scr-takes",
        runtime_target_s=60,
        scenes=[
            Scene(
                scene_id="sc-1",
                heading="INT. DINER - NIGHT",
                setting_ref="set-primary",
                cast=cast or ["chr-lead", "chr-second"],
                line_ids=[l.line_id for l in lines],
            )
        ],
        lines=lines,
    )


def _action(n: int, text: str = "") -> ScriptLine:
    return ScriptLine(
        line_id=f"ln-{n}",
        kind="action",
        scene_id="sc-1",
        text=text or f"Beat {n}: a body moves through the room.",
    )


def _line(n: int, text: str, speaker: str = "LEAD") -> ScriptLine:
    return ScriptLine(
        line_id=f"ln-{n}", kind="dialogue", scene_id="sc-1", text=text, speaker=speaker
    )


# --- take units per route ---------------------------------------------------


def test_take_units_follow_the_routes_longest_legal_clip() -> None:
    assert splitter.take_units("grok") == {"picture": 15, "spoken": 15}
    assert splitter.take_units("google-flow") == {"picture": 10, "spoken": 10}
    assert splitter.take_units_phrase({"picture": 15, "spoken": 10}) == (
        "up to 15s (10s when a line is spoken in it)"
    )
    assert splitter.take_units_phrase(splitter.take_units("grok")) == "up to 15s"


# --- packing ----------------------------------------------------------------


def test_a_picture_scene_on_grok_packs_into_fifteen_second_takes() -> None:
    draft = _draft()
    # 6 + 4 × 6 = 30s of beats → exactly two 15s takes, never seven clips
    lines = [_action(n) for n in range(1, 8)]
    clips = splitter.split_script(
        _script(lines), draft.characters, draft.settings, provider_policy="grok"
    )
    assert [c.duration_s for c in clips] == [15, 15]
    assert [len(c.script_line_ids) for c in clips] == [3, 4]
    assert [c.natural_s for c in clips] == [15.0, 15.0], "each take is paced to the even share"
    assert all(c.provider is CloudProvider.GROK for c in clips)
    assert clips[0].clip_class == "ambience-led" and clips[1].clip_class == "score-led"


def test_beats_compress_a_quarter_to_fill_one_long_take() -> None:
    draft = _draft()
    # 6 + 4 + 4 + 4 = 18s of beats on Grok: ONE 15s take (the beats pace
    # down 17%), never 15s + 3s and never 9s + 9s
    lines = [_action(n) for n in range(1, 5)]
    clips = splitter.split_script(
        _script(lines), draft.characters, draft.settings, provider_policy="grok"
    )
    assert [c.duration_s for c in clips] == [15]
    assert clips[0].natural_s == 15.0
    assert clips[0].script_line_ids == ["ln-1", "ln-2", "ln-3", "ln-4"]


def test_takes_are_even_never_a_long_take_and_a_stub() -> None:
    draft = _draft()
    # 6 + 4 × 4 = 22s on Grok: past what one take can hold, so two takes of
    # 11 and 11 — not 15 and 7
    lines = [_action(n) for n in range(1, 6)]
    clips = splitter.split_script(
        _script(lines), draft.characters, draft.settings, provider_policy="grok"
    )
    assert [c.duration_s for c in clips] == [11, 11]
    assert [len(c.script_line_ids) for c in clips] == [2, 3]


def test_a_short_scene_is_one_take() -> None:
    draft = _draft()
    lines = [_action(1), _action(2)]
    clips = splitter.split_script(
        _script(lines), draft.characters, draft.settings, provider_policy="grok"
    )
    assert len(clips) == 1 and clips[0].duration_s == 10
    assert clips[0].script_line_ids == ["ln-1", "ln-2"]


def test_on_the_omni_grid_a_take_books_the_grid_step_above_its_beats() -> None:
    draft = _draft()
    lines = [_action(1), _action(2), _action(3)]  # 14s → two takes on the 10s grid
    clips = splitter.split_script(
        _script(lines), draft.characters, draft.settings, provider_policy="google-flow"
    )
    # paced to the 7s share (the 6s opener stretches, the 8s pair compresses,
    # both inside the quarter), then the grid rounds each up to 8
    assert [c.duration_s for c in clips] == [8, 8]
    assert [c.natural_s for c in clips] == [7.0, 7.0]
    assert all(c.model == "omni-flash" for c in clips)


def test_one_spoken_line_per_take_is_law() -> None:
    draft = _draft()
    lines = [
        _line(1, "Keys.", "LEAD"),
        _line(2, "Hook.", "SECOND"),
        _line(3, "Go.", "LEAD"),
    ]
    clips = splitter.split_script(
        _script(lines), draft.characters, draft.settings, provider_policy="grok"
    )
    assert len(clips) == 3, "three lines are three takes even when they would fit one"
    for clip in clips:
        assert len(clip.audio_packet.script_excerpt) == 1
        assert len(clip.script_line_ids) == 1


def test_a_line_and_the_beats_around_it_share_a_take() -> None:
    # this production rolls on Google Flow (the 10 s Omni grid, the Flow finish lane)
    from diony_contracts.cluster import write_engine_choice

    write_engine_choice("google-flow")
    draft = _draft()
    # 6 + 1.7 + 4 = 11.7s of beats around one short line: inside the quarter
    # the omni cap allows, so ONE 10s spoken take — setup, line, reaction
    lines = [_action(1), _line(2, "Go.", "LEAD"), _action(3)]
    clips = splitter.split_script(_script(lines), draft.characters, draft.settings)
    assert len(clips) == 1
    take = clips[0]
    assert take.clip_class == "sync-dialogue" and take.duration_s == 10
    assert take.script_line_ids == ["ln-1", "ln-2", "ln-3"]
    assert take.provider is CloudProvider.GOOGLE_FLOW, "a take with a line rides the voice lane"
    # the opener beat before the line, paced to the booking → the words sit
    # about half-way through the take, and the reaction follows in-frame
    assert 4.0 < take.audio_packet.line_at_s < 6.0
    assert "then reaction close-up on SECOND" in take.camera_idea


def test_a_line_that_opens_its_take_sits_at_the_head() -> None:
    draft = _draft()
    lines = [_line(1, "Keys are on the hook.", "LEAD"), _action(2), _action(3)]
    clips = splitter.split_script(_script(lines), draft.characters, draft.settings)
    assert len(clips) == 1
    take = clips[0]
    assert take.audio_packet.line_at_s == 0.0
    assert take.camera_idea.startswith("Over-the-shoulder shot favoring LEAD")
    assert "; then reaction close-up on SECOND" in take.camera_idea


def test_a_take_never_exceeds_the_lane_cap_even_under_auto() -> None:
    draft = _draft()
    # picture takes may run 15s on Grok, but a take carrying a line is capped
    # by the omni grid at 10s — the beats before it cannot drag it past that
    lines = [_action(1), _action(2), _action(3), _line(4, "Now.", "LEAD"), _action(5)]
    clips = splitter.split_script(_script(lines), draft.characters, draft.settings)
    for clip in clips:
        cap = 10 if clip.audio_packet.script_excerpt else 15
        assert clip.duration_s <= cap
    spoken = next(c for c in clips if c.audio_packet.script_excerpt)
    assert spoken.duration_s <= 10


def test_foley_before_the_first_beat_lands_on_the_first_take() -> None:
    draft = _draft()
    lines = [
        ScriptLine(
            line_id="ln-0", kind="sound", text="a fridge compressor kicks in", scene_id="sc-1"
        ),
        _action(1),
        _action(2),
    ]
    clips = splitter.split_script(
        _script(lines), draft.characters, draft.settings, provider_policy="grok"
    )
    assert clips[0].audio_packet.foley == [{"event": "a fridge compressor kicks in"}]


# --- downstream: the trim and the cue follow the take -----------------------


def test_the_cue_sheet_places_the_words_where_the_take_puts_them() -> None:
    draft = _draft()
    lines = [_action(1), _line(2, "We leave tonight.", "LEAD")]
    clips = splitter.split_script(_script(lines), draft.characters, draft.settings)
    take = clips[0]
    assert take.audio_packet.line_at_s > 0

    class _Op:
        id = "op-take"
        narration = ""
        voicing = "post"

    cues = narration.cue_sheet(
        [{"op": _Op(), "clip": take, "window": (0.0, float(take.duration_s))}]
    )
    assert len(cues) == 1
    assert cues[0].start_s == round(take.audio_packet.line_at_s + narration.CUE_LEAD_IN_S, 3)
    assert cues[0].window_s == round(take.duration_s - take.audio_packet.line_at_s, 3)


def test_the_assembly_trim_follows_the_takes_natural_length_not_the_words() -> None:
    session.reset()
    sid = "s-takes-trim"
    session.start_production(sid, "a getaway driver decides to leave town from a neon diner", 60)
    package, ops, _bible, reply = session.lock_script(sid)
    assert "in takes of up to" in reply
    packed = next(
        (
            op
            for op in ops
            if (clip := session.clip_for_op(op)) is not None
            and clip.audio_packet.script_excerpt
            and len(clip.script_line_ids) > 1
            and clip.audio_packet.voicing != "generated"
        ),
        None,
    )
    assert packed is not None, "the template's scenes pack a line with its beats"
    clip = session.clip_for_op(packed)
    spec = app_module._assembly_spec(packed)
    assert spec["needed_s"] == round(clip.natural_s + 0.25, 3)
    assert spec["needed_s"] > 4.0, "the beats around the line are never trimmed away"
    session.reset()


# --- the screenwriter is told the take length --------------------------------


def test_the_brain_brief_names_the_take_length() -> None:
    brief = screenwriter.take_brief({"picture": 15, "spoken": 10})
    assert "up to 15s (10s when a line is spoken in it)" in brief
    assert "one or two takes, not five clips" in brief
    assert screenwriter.take_brief(None) == ""


def test_the_draft_reply_names_the_take_length() -> None:
    session.reset()
    sid = "s-takes-reply"
    _draft_obj, reply = session.start_production(
        sid, "a getaway driver decides to leave town from a neon diner", 60
    )
    assert "pack it into priced takes of up to" in reply
    session.reset()


# --- edge cases ---------------------------------------------------------------


def test_a_lone_line_longer_than_the_cap_rides_the_cap_uncut_and_untrimmed() -> None:
    # this production rolls on Google Flow (the 10 s Omni grid, the Flow finish lane)
    from diony_contracts.cluster import write_engine_choice

    write_engine_choice("google-flow")
    draft = _draft()
    words = " ".join(["word"] * 40)  # 16s + handles = 17.5s of speech on a 10s omni cap
    clips = splitter.split_script(_script([_line(1, words)]), draft.characters, draft.settings)
    assert len(clips) == 1
    take = clips[0]
    assert take.duration_s == 10, "the grid's top, never a clipped booking"
    assert take.natural_s > take.duration_s, "the words are never compressed on paper"
    assert take.audio_packet.line_at_s == 0.0


def test_a_long_line_never_packs_the_beats_around_it() -> None:
    # this production rolls on Google Flow (the 10 s Omni grid, the Flow finish lane)
    from diony_contracts.cluster import write_engine_choice

    write_engine_choice("google-flow")
    draft = _draft()
    words = " ".join(["word"] * 24)  # 9.6s + handles = 11.1s > the omni cap
    lines = [_action(1), _line(2, words), _action(3)]
    clips = splitter.split_script(_script(lines), draft.characters, draft.settings)
    spoken = next(c for c in clips if c.audio_packet.script_excerpt)
    assert spoken.script_line_ids == ["ln-2"], "a line that fills its cap takes nothing with it"
    assert len(clips) == 3, "no pairing is feasible, so every beat is its own take"
    assert all(c.duration_s <= 15 for c in clips)


def test_an_off_screen_line_packed_with_its_beats_keeps_the_listeners_in_frame() -> None:
    draft = _draft()
    line = ScriptLine(
        line_id="ln-2",
        kind="dialogue",
        scene_id="sc-1",
        text="Keys are on the hook.",
        speaker="LEAD",
        off_screen=True,
    )
    clips = splitter.split_script(
        _script([_action(1), line, _action(3)]), draft.characters, draft.settings
    )
    take = next(c for c in clips if c.audio_packet.script_excerpt)
    excerpt = take.audio_packet.script_excerpt[0]
    assert excerpt["off_screen"] is True and excerpt["listeners"] == ["SECOND"]
    assert [p["char_id"] for p in take.continuity.character_packs] == ["chr-second"], (
        "an off-screen speaker's sheet never rides a take they are not in"
    )


def test_a_scene_with_no_beats_yields_no_take() -> None:
    draft = _draft()
    lines = [
        ScriptLine(line_id="ln-1", kind="heading", text="INT. DINER - NIGHT", scene_id="sc-1"),
        ScriptLine(line_id="ln-2", kind="sound", text="a jukebox two rooms away", scene_id="sc-1"),
    ]
    assert splitter.split_script(_script(lines), draft.characters, draft.settings) == []


def test_a_parenthetical_reaches_the_line_it_precedes_inside_a_packed_take() -> None:
    draft = _draft()
    lines = [
        _action(1),
        ScriptLine(line_id="ln-2", kind="parenthetical", text="quietly", scene_id="sc-1"),
        _line(3, "Go."),
    ]
    clips = splitter.split_script(_script(lines), draft.characters, draft.settings)
    assert len(clips) == 1
    assert clips[0].audio_packet.script_excerpt[0]["parenthetical"] == "quietly"


def test_clip_ids_and_order_stay_dense_across_packed_scenes() -> None:
    draft = _draft()
    clips = splitter.split_script(draft.script, draft.characters, draft.settings)
    assert [c.order for c in clips] == list(range(1, len(clips) + 1))
    per_scene: dict[str, list[str]] = {}
    for c in clips:
        per_scene.setdefault(c.scene_id, []).append(c.clip_id)
    for scene_index, (_scene_id, ids) in enumerate(per_scene.items(), start=1):
        assert ids == [f"cl-{scene_index}-{n}" for n in range(1, len(ids) + 1)]


def test_the_veo_grid_caps_a_take_at_the_plans_top_step(monkeypatch) -> None:
    from diony_director import estimate as estimate_mod

    monkeypatch.setattr(estimate_mod, "veo_grid", lambda: (8,))
    assert splitter.take_unit(CloudProvider.GOOGLE_FLOW, "veo-3.1-fast") == 8
    assert splitter.take_unit(CloudProvider.GOOGLE_FLOW, "omni-flash") == 10
    assert splitter.take_unit(CloudProvider.GROK, "grok-imagine-video-1.5") == 15
