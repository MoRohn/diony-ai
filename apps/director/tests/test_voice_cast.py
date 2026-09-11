"""Blueprint 31 §10 — the voice cast: one castable voice per character.

Under Dialogue and Mix the provider voices the character in the clip, so the
prompt must say WHOSE voice — gender, age, timbre — and say it the same way
on every clip; two characters must never share a read. A character's line
that falls to the narrator rail under Mix (a kept voice-over, a rescued mute
take) is read in a roster voice cast from that character's card, never the
film's narrator by default. Narrated stays byte-identical to Blueprint 28."""

from __future__ import annotations

import json

from test_production_style import IDEA, _package, _spoken

from diony_contracts.production import CharacterProfile, IdentityCard, VoiceProfile
from diony_director import compiler, narration, packagebuilder, screenwriter, session, voices

# --- reading the casting notes -----------------------------------------------


def test_the_derivation_reads_gender_age_and_timbre_from_the_notes() -> None:
    weathered = CharacterProfile(
        char_id="chr-elias",
        name="ELIAS",
        age_body_notes="a weathered man in his fifties, gravel in the voice",
    )
    card = voices.derive_voice(weathered)
    assert (card.gender, card.age_band) == ("male", "fifties")
    assert "deep" in card.timbre and card.source == "derived"

    mara = CharacterProfile(
        char_id="chr-mara", name="MARA", age_body_notes="34, lean, quick to smile; she runs"
    )
    card = voices.derive_voice(mara)
    assert (card.gender, card.age_band) == ("female", "thirties")
    assert "warm" in card.timbre

    # the identity card's anchor reads too — it is the casting read
    locked = CharacterProfile(
        char_id="chr-x",
        name="X",
        identity=IdentityCard(anchor="a girl of 9 years, gap-toothed", features=["red hair"]),
    )
    card = voices.derive_voice(locked)
    assert (card.gender, card.age_band) == ("female", "a child")


def test_an_ambiguous_or_technical_read_is_never_a_guess() -> None:
    # both tells present: no gender read; "35mm" is a lens, not an age
    both = CharacterProfile(
        char_id="c", name="C", age_body_notes="a woman beside her brother, 35mm primes"
    )
    card = voices.derive_voice(both)
    assert card.gender == "" and card.age_band == ""
    # a card with no read at all is still a castable timbre from the palette
    assert card.timbre and card.cast
    # wardrobe never reads as a person
    dressed = CharacterProfile(
        char_id="d", name="D", wardrobe={"default": "a boyfriend jacket, her scarf"}
    )
    assert voices.derive_voice(dressed).gender == ""


def test_the_cast_is_distinct_and_the_brains_card_is_authored_law() -> None:
    women = [
        CharacterProfile(char_id=f"chr-{i}", name=f"W{i}", age_body_notes="a woman, thirties")
        for i in range(6)
    ]
    voices.cast_voices(women)
    reads = [(c.voice.gender, c.voice.timbre) for c in women]
    assert len(set(reads)) == len(reads), "no two characters share a gender-and-timbre pair"
    assert all(g == "female" for g, _ in reads)
    # idempotent: casting again changes nothing
    before = [c.voice.model_dump() for c in women]
    voices.cast_voices(women)
    assert [c.voice.model_dump() for c in women] == before

    # a brain card stands as written
    lead = CharacterProfile(char_id="chr-lead", name="LEAD", age_body_notes="a man")
    brain = voices.parse_brain_voice(
        {"gender": "woman", "age": "40", "timbre": "husky, low, unhurried", "accent": "Irish"},
        lead,
    )
    assert brain is not None and brain.source == "brain"
    assert (brain.gender, brain.age_band) == ("female", "forties")
    lead.voice = brain
    voices.cast_voices([lead])
    assert lead.voice.timbre == "husky, low, unhurried" and lead.voice.accent == "Irish"
    assert "a female voice, forties, husky, low, unhurried, Irish accent" in voices.voice_card(lead)
    assert voices.parse_brain_voice({"gender": "robot"}, lead) is None
    assert voices.parse_brain_voice("a voice", lead) is None


def test_the_card_is_one_clause_with_the_one_voice_per_character_law() -> None:
    char = CharacterProfile(
        char_id="chr-mara",
        name="MARA",
        voice=VoiceProfile(
            voice_id="vox-mara", gender="female", age_band="thirties", timbre="warm and bright"
        ),
    )
    card = voices.voice_card(char)
    assert card.startswith("VOICE — MARA: a female voice, thirties, warm and bright;")
    assert "MARA has this same voice in every clip" in card
    assert "no other character in this film shares it" in card
    assert voices.voice_line(char) == "female · thirties · warm and bright"
    assert voices.roster_notes(char) == "female thirties warm and bright"
    # a pre-card profile carries no clause — the prompt reads as before
    bare = CharacterProfile(char_id="b", name="B", voice=VoiceProfile(voice_id="vox-b"))
    assert voices.voice_card(bare) == "" and voices.voice_line(bare) == ""


# --- the screenwriter and the lock ---------------------------------------------


def test_every_drafted_character_carries_a_distinct_voice_card() -> None:
    draft = screenwriter.write_script(IDEA, runtime_target_s=60)
    assert draft.characters
    assert all(c.voice is not None and c.voice.cast for c in draft.characters)
    reads = {(c.voice.gender, c.voice.timbre) for c in draft.characters}
    assert len(reads) == len(draft.characters)
    # the brain contract asks for the card
    assert "voice: {gender, age, timbre, accent}" in screenwriter._BRAIN_INSTRUCTIONS
    assert "never give two characters the same gender AND the same timbre" in (
        screenwriter._BRAIN_INSTRUCTIONS
    )


def test_a_package_drafted_before_cards_existed_is_cast_at_lock() -> None:
    draft = screenwriter.write_script(IDEA, runtime_target_s=60)
    for char in draft.characters:
        char.voice = VoiceProfile(voice_id=f"vox-{char.char_id}")  # the pre-§10 shape
    package = packagebuilder.build_package("s-old", "a getaway", draft, provider_policy="grok")
    assert all(c.voice.cast for c in package.characters)


# --- the compiler ---------------------------------------------------------------


def test_dialogue_and_mix_prompts_carry_each_speakers_own_card() -> None:
    for style in ("dialogue", "mix"):
        package = _package(style, "grok")
        by_name = {c.name: c for c in package.characters}
        seen_cards: dict[str, set[str]] = {}
        for clip in _spoken(package):
            if clip.audio_packet.voicing != "generated":
                continue
            prompt = compiler.compile_clip_prompt(clip, package).prompt
            speaker = clip.audio_packet.script_excerpt[0]["speaker"]
            char = by_name[speaker]
            card = voices.voice_card(char)
            assert card in prompt, f"{style} {clip.clip_id}: the speaker's card rides verbatim"
            seen_cards.setdefault(speaker, set()).add(card)
            # only the speaker's card — never another character's
            for other in package.characters:
                if other.name != speaker:
                    assert voices.voice_card(other) not in prompt
        # the same bytes on every clip a character speaks in
        assert all(len(cards) == 1 for cards in seen_cards.values())
        assert len(seen_cards) >= 2, "the fixture script must carry two speakers"


def test_narrated_prompts_never_carry_a_voice_card() -> None:
    package = _package("narrated", "grok")
    for clip in package.clips:
        prompt = compiler.compile_clip_prompt(clip, package).prompt
        assert "VOICE — " not in prompt
        assert compiler.NO_SPEECH in prompt


def test_an_off_screen_line_and_a_long_line_keep_the_card() -> None:
    package = _package("dialogue", "grok")
    clip = next(c for c in _spoken(package) if c.audio_packet.on_camera)
    speaker = clip.audio_packet.script_excerpt[0]["speaker"]
    char = next(c for c in package.characters if c.name == speaker)
    clip.audio_packet.script_excerpt[0]["off_screen"] = True
    clip.audio_packet.script_excerpt[0]["listeners"] = ["THE COOK"]
    prompt = compiler.compile_clip_prompt(clip, package).prompt
    assert "speaks from OFF-SCREEN" in prompt and voices.voice_card(char) in prompt
    # the quoted line still outranks everything under length pressure, and
    # the card rides beside it in the same untouchable piece
    clip.audio_packet.script_excerpt[0].pop("off_screen")
    long_line = " ".join(["we leave before the neon dies and nobody follows"] * 6)
    clip.audio_packet.script_excerpt[0]["line"] = long_line
    import os

    os.environ["DIONY_PROMPT_LIMIT_GROK"] = "1200"
    try:
        prompt = compiler.compile_clip_prompt(clip, package).prompt
    finally:
        os.environ.pop("DIONY_PROMPT_LIMIT_GROK", None)
    assert len(prompt) <= 1200 and f'"{long_line}"' in prompt


def test_a_one_shot_voiced_in_the_clip_names_one_voice_for_the_whole_chain(monkeypatch) -> None:
    monkeypatch.delenv(narration.GROK_VOICE_ROSTER_ENV, raising=False)
    sid = session.create_session()
    session.set_voice(sid, "eve")
    clause = compiler.spoken_narration_clause(
        "The city sleeps.", voice_read=session.spoken_voice_read(sid)
    )
    assert 'exactly these words and nothing else: "The city sleeps."' in clause
    assert "VOICE: a female voice — soothing, calm, intimate" in clause
    assert "the same voice in every clip of this film" in clause
    # Auto: the Director casts one voice for the session, stable across links
    session.set_voice(sid, "")
    first = session.spoken_voice_read(sid)
    assert first.startswith("a ") and first == session.spoken_voice_read(sid)
    assert narration.describe_voice("nobody") == ""
    assert compiler.spoken_narration_clause("x", voice_read="").endswith(
        "voice for the whole line."
    )


# --- the narrator rail ------------------------------------------------------------


def test_a_characters_line_on_the_rail_is_cast_to_a_matching_roster_voice(monkeypatch) -> None:
    monkeypatch.delenv(narration.GROK_VOICE_ENV, raising=False)
    monkeypatch.delenv(narration.GROK_VOICE_ROSTER_ENV, raising=False)
    monkeypatch.setenv("DIONY_NARRATOR_BACKEND", "grok")
    mara = CharacterProfile(
        char_id="chr-mara",
        name="MARA",
        voice=VoiceProfile(
            voice_id="vox-mara",
            gender="female",
            age_band="thirties",
            timbre="warm and bright, quick and friendly",
        ),
    )
    elias = CharacterProfile(
        char_id="chr-elias",
        name="ELIAS",
        voice=VoiceProfile(
            voice_id="vox-elias",
            gender="male",
            age_band="fifties",
            timbre="deep and solemn, slow and grave",
        ),
    )
    cues = [
        narration.NarrationCue(
            order=0, clip_id="cl-1", speaker="MARA", line="a", voicing="generated"
        ),
        narration.NarrationCue(order=1, clip_id="cl-2", speaker="ELIAS", line="b", voicing="post"),
        narration.NarrationCue(order=2, clip_id="cl-3", speaker="", line="c"),
        narration.NarrationCue(
            order=3, clip_id="cl-4", speaker="MARA", line="d", voicing="generated"
        ),
    ]
    narration.cast_cues(
        cues,
        tone_text="tense noir, night streets",
        voice="atlas",  # the console's narrator: a deep male voice
        seed="s-1",
        character_voices={c.name: voices.roster_notes(c) for c in (mara, elias)},
    )
    # MARA's rescued lines: a female roster voice, the same one both times,
    # not the male narrator the pill chose
    assert cues[0].voice_ref == "ara" and cues[3].voice_ref == "ara"
    assert "character voice (MARA)" in cues[0].cast_note and "female read" in cues[0].cast_note
    assert "rescued in the character's voice" in cues[0].cast_note
    assert "rescued" not in cues[1].cast_note
    # ELIAS's voice-over: a male roster voice that is NOT the narrator's? — it
    # is cast from his card; the narrator's own cue keeps the console choice
    assert cues[1].voice_ref == "atlas" and "character voice (ELIAS)" in cues[1].cast_note
    assert cues[2].voice_ref == "atlas" and "chosen in the Director console" in cues[2].cast_note


def test_two_characters_of_one_gender_never_share_a_roster_voice(monkeypatch) -> None:
    monkeypatch.delenv(narration.GROK_VOICE_ENV, raising=False)
    monkeypatch.delenv(narration.GROK_VOICE_ROSTER_ENV, raising=False)
    monkeypatch.setenv("DIONY_NARRATOR_BACKEND", "grok")
    cues = [
        narration.NarrationCue(
            order=0, clip_id="cl-1", speaker="ANA", line="a", voicing="generated"
        ),
        narration.NarrationCue(
            order=1, clip_id="cl-2", speaker="BEA", line="b", voicing="generated"
        ),
    ]
    same_read = "female thirties warm and bright, quick and friendly"
    narration.cast_cues(cues, seed="s-2", character_voices={"ANA": same_read, "BEA": same_read})
    assert cues[0].voice_ref != cues[1].voice_ref
    assert {narration.DEFAULT_ROSTER[c.voice_ref][0] for c in cues} == {"female"}


def test_narrated_keeps_one_narrator_for_the_whole_film(monkeypatch) -> None:
    # no character voices handed in (the Narrated style): Blueprint 28's
    # single-speaker doctrine is untouched
    monkeypatch.delenv(narration.GROK_VOICE_ENV, raising=False)
    monkeypatch.delenv(narration.GROK_VOICE_ROSTER_ENV, raising=False)
    cues = [
        narration.NarrationCue(order=0, clip_id="cl-1", speaker="ELIAS", line="a"),
        narration.NarrationCue(order=1, clip_id="cl-2", speaker="MARA", line="b"),
    ]
    narration.cast_cues(cues, tone_text="tense noir", seed="s-3", character_voices={})
    assert cues[0].voice_ref == cues[1].voice_ref
    assert all("one voice for the whole film" in c.cast_note for c in cues)


def test_the_board_shows_each_characters_voice_card() -> None:
    from fastapi.testclient import TestClient

    from diony_director import app as app_module

    client = TestClient(app_module.app)
    sid = client.post("/api/director/sessions").json()["session_id"]
    client.post(f"/api/director/sessions/{sid}/produce", json={"idea": IDEA, "style": "mix"})
    client.post(f"/api/director/sessions/{sid}/lock_script")
    board = client.get(f"/api/director/sessions/{sid}/board").json()
    rows = board["characters"]
    assert rows and all(row["voice"] for row in rows)
    assert len({row["voice"] for row in rows}) == len(rows)
    assert all(row["voice_source"] in {"brain", "derived", "user"} for row in rows)
    # the console paints it
    from pathlib import Path

    static = Path(app_module.__file__).resolve().parent / "static"
    js = (static / "app.js").read_text()
    css = (static / "app.css").read_text()
    assert 'class="bvoice"' in js and ".bvoice" in css
    # and the card survives a persisted, rehydrated session
    session.persist(sid)
    package = session.package_for(sid)
    doc = json.loads(json.dumps(package.model_dump(mode="json")))
    assert all(c["voice"]["timbre"] for c in doc["characters"])
