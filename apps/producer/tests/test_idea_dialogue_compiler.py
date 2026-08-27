from diony_contracts.enums import (
    AssetKind,
    CustomerKind,
    Engine,
    ProductionKind,
    VoiceRenderState,
)
from diony_contracts.models import Asset, Brief, Take
from diony_contracts.studio import expand_idea
from diony_producer.voice import DeterministicVoiceTestBackend, VoicePerformancePipeline


def _named_screenplay_project():
    still = Asset(
        id="ast-ensemble",
        kind=AssetKind.IMAGE,
        filename="ensemble.png",
        mime="image/png",
        bytes=48,
        path="assets/ast-ensemble/ensemble.png",
    )
    project = expand_idea(
        Brief(
            idea=(
                "Use the exact attached ensemble character still for this screenplay scene.\n\n"
                'Marcus: "The window closes at midnight."\n'
                'Lila: "Then we move before the bell."\n'
                'Hugo: "I already opened the gate."'
            ),
            customer_kind=CustomerKind.SERIES,
            target_minutes=0.5,
            attachment_ids=[still.id],
        ),
        assets=[still],
    )
    return project, still


def test_idea_compiler_preserves_named_screenplay_cast_dialogue_and_voice_locks() -> None:
    project, _ = _named_screenplay_project()

    characters_by_id = {character.id: character for character in project.bible.characters}
    assert {character.name for character in characters_by_id.values()} == {
        "Marcus",
        "Lila",
        "Hugo",
    }

    turns = [turn for shot in project.shots for turn in shot.dialogue_turns]
    assert [
        (characters_by_id[turn.character_id].name, turn.text)
        for turn in turns
    ] == [
        ("Marcus", "The window closes at midnight."),
        ("Lila", "Then we move before the bell."),
        ("Hugo", "I already opened the gate."),
    ]

    profiles_by_character = {
        profile.character_id: profile for profile in project.voice_profiles
    }
    assert set(profiles_by_character) == set(characters_by_id)
    for character_id, character in characters_by_id.items():
        profile = profiles_by_character[character_id]
        assert character.voice_profile_id == profile.id
        assert profile.project_id == project.id
        assert profile.character_id == character_id
        assert profile.locked is True
        assert len(profile.content_sha256) == 64


def test_compiled_spoken_still_shots_lock_audio_before_routing_to_s2v(tmp_path) -> None:
    project, still = _named_screenplay_project()
    spoken_shots = [shot for shot in project.shots if shot.dialogue_turns]

    assert len(spoken_shots) == 3
    backend = DeterministicVoiceTestBackend()
    pipeline = VoicePerformancePipeline(
        vault=tmp_path,
        synthesizer=backend,
        embedder=backend,
        aligner=backend,
        audio_processor=backend,
        audiovisual_qc=backend,
    )

    for shot in spoken_shots:
        assert shot.keyframe_asset_ids == [still.id]
        assert shot.engine == Engine.I2V_A14B
        take = Take(
            id=f"{shot.id}-dialogue-t1",
            shot_id=shot.id,
            attempt=1,
            engine=shot.engine,
            seed=shot.seed,
        )
        project.takes.append(take)

        package = pipeline.prepare_take(project, take)

        assert package.state == VoiceRenderState.AUDIO_LOCKED
        assert shot.audio_asset_ids == [package.clean_dialogue_asset_id]
        assert shot.engine == Engine.S2V_14B
        assert take.engine == Engine.S2V_14B
        assert shot.prompt_plan is not None
        assert shot.prompt_plan.generation_method == Engine.S2V_14B.value
        assert shot.prompt_plan.audio_strategy == "locked_voice_s2v"
        # The locked line dispatches with the S2V performance direction.
        from diony_contracts.wan_prompt import is_spoken_plan, validate_wan_prompt_dispatch

        assert is_spoken_plan(shot.prompt_plan)
        assert "speaks the line straight toward camera" in shot.prompt
        assert "static camera with no movement" in shot.prompt
        assert validate_wan_prompt_dispatch(shot) == []


def test_title_case_cues_and_curly_quotes_compile_without_stage_directions() -> None:
    still = Asset(
        id="ast-kitchen",
        kind=AssetKind.IMAGE,
        filename="kitchen.png",
        mime="image/png",
        bytes=48,
        path="assets/ast-kitchen/kitchen.png",
    )
    project = expand_idea(
        Brief(
            idea=(
                "Three clay characters share a kitchen.\n"
                "Marcus: “We are starting a daily gratitude circle.”\n"
                "Lila: “I’m grateful this will be over in under a minute.”\n"
                "Marcus: “Hugo. Your turn. Be vulnerable.”\n"
                "Hugo: “Neither of you can force me to continue this.”\n\n"
                "Final hold:\nMarcus keeps smiling while Hugo remains still."
            ),
            customer_kind=CustomerKind.SERIES,
            target_minutes=0.5,
            attachment_ids=[still.id],
        ),
        assets=[still],
    )

    by_id = {character.id: character.name for character in project.bible.characters}
    turns = [turn for shot in project.shots for turn in shot.dialogue_turns]

    assert set(by_id.values()) == {"Marcus", "Lila", "Hugo"}
    # Long turns may split across lane-true shot windows; the speaking order holds.
    speaker_runs: list[str] = []
    for turn in turns:
        name = by_id[turn.character_id]
        if not speaker_runs or speaker_runs[-1] != name:
            speaker_runs.append(name)
    assert speaker_runs == ["Marcus", "Lila", "Marcus", "Hugo"]
    assert all("Final hold" not in turn.text for turn in turns)
    picture_prompts = " ".join(shot.prompt for shot in project.shots).casefold()
    assert "we are starting a daily gratitude circle" not in picture_prompts
    assert "i’m grateful this will be over" not in picture_prompts
    assert "hugo. your turn. be vulnerable" not in picture_prompts
    assert "neither of you can force me" not in picture_prompts


def test_prose_speech_direction_with_curly_quote_compiles_as_authored_dialogue() -> None:
    still = Asset(
        id="ast-cyber-guardian",
        kind=AssetKind.IMAGE,
        filename="CyberGuardian.JPG",
        mime="image/jpeg",
        bytes=48,
        path="assets/ast-cyber-guardian/CyberGuardian.JPG",
    )
    project = expand_idea(
        Brief(
            idea=(
                "Character sways and glistens, she says in a synthetic and mysterious "
                "tone, “Welcome to the future, it’s our time now.”"
            ),
            customer_kind=CustomerKind.SERIES,
            target_minutes=0.5,
            production_kind=ProductionKind.SAMPLE,
            sample_seconds=10,
            attachment_ids=[still.id],
        ),
        assets=[still],
    )

    turns = [turn for shot in project.shots for turn in shot.dialogue_turns]

    # The authored line survives verbatim even when it spans lane-true windows.
    assert " ".join(turn.text for turn in turns) == "Welcome to the future, it’s our time now."
    assert len({turn.character_id for turn in turns}) == 1
    assert len(project.voice_profiles) == 1
    assert project.voice_profiles[0].character_id == turns[0].character_id
    assert any(shot.keyframe_asset_ids == [still.id] for shot in project.shots if shot.dialogue_turns)
    picture_prompts = " ".join(shot.prompt for shot in project.shots).casefold()
    assert "welcome to the future" not in picture_prompts


def test_long_authored_dialogue_is_distributed_without_truncation_across_lane_shots() -> None:
    still = Asset(
        id="ast-kitchen-long-line",
        kind=AssetKind.IMAGE,
        filename="kitchen.png",
        mime="image/png",
        bytes=48,
        path="assets/ast-kitchen-long-line/kitchen.png",
    )
    authored = (
        "I thought gratitude would make this room feel calmer but somehow "
        "the coffee is judging us and Hugo has declared war on the toaster."
    )
    project = expand_idea(
        Brief(
            idea=f'In a shared kitchen.\nMarcus: "{authored}"',
            customer_kind=CustomerKind.SERIES,
            target_minutes=0.5,
            attachment_ids=[still.id],
        ),
        assets=[still],
    )

    turns = [turn for shot in project.shots for turn in shot.dialogue_turns]

    # 24 authored words at the natural rate over ~3.4s lane shots → 6-word windows.
    assert len(turns) == 4
    assert all(len(turn.text.split()) <= 6 for turn in turns)
    assert " ".join(turn.text for turn in turns) == authored
    assert len({turn.character_id for turn in turns}) == 1


def test_thirty_second_program_packs_turns_by_total_natural_speaking_time() -> None:
    authored_turns = [
        (
            "Marcus",
            "We are starting a daily gratitude circle. I’ll go first. I’m grateful "
            "for hustle, vision, and the fact that I haven’t given up on you two.",
        ),
        ("Lila", "I’m grateful this will be over in under a minute."),
        ("Marcus", "Hugo. Your turn. Be vulnerable."),
        (
            "Hugo",
            "I am grateful that neither of you can force me to continue this.",
        ),
    ]
    idea = "A clay ensemble performs in a shared kitchen.\n" + "\n".join(
        f'{speaker}: “{text}”' for speaker, text in authored_turns
    )

    project = expand_idea(
        Brief(
            idea=idea,
            customer_kind=CustomerKind.SERIES,
            target_minutes=0.5,
        )
    )

    characters_by_id = {
        character.id: character.name for character in project.bible.characters
    }
    actual_word_stream = [
        (characters_by_id[turn.character_id], word)
        for shot in project.shots
        for turn in shot.dialogue_turns
        for word in turn.text.split()
    ]
    expected_word_stream = [
        (speaker, word)
        for speaker, text in authored_turns
        for word in text.split()
    ]

    assert len(project.shots) == 10
    # Dialogue rides only on bookings long enough for a natural-rate window:
    # the 62-frame lane tail stays picture-only.
    assert len([shot for shot in project.shots if shot.dialogue_turns]) == 9
    assert not project.shots[-1].dialogue_turns
    assert all(
        sum(len(turn.text.split()) for turn in shot.dialogue_turns) <= 6
        for shot in project.shots
    )
    assert actual_word_stream == expected_word_stream


def test_dialogue_only_idea_never_falls_back_to_voice_text_for_picture() -> None:
    project = expand_idea(
        Brief(
            idea='Marcus: "We move before midnight."',
            customer_kind=CustomerKind.SERIES,
            target_minutes=0.5,
        )
    )

    assert project.shots[0].dialogue_turns[0].text == "We move before midnight."
    prompts = " ".join(shot.prompt for shot in project.shots).casefold()
    assert "we move before midnight" not in prompts
    assert "bible-locked performance" in prompts


def test_program_rundown_grows_instead_of_refusing_long_authored_dialogue() -> None:
    """A 30s program has 9 dialogue-eligible shots; ~10 windows of speech used to raise."""
    authored_turns = [
        ("Marcus", "We are starting a daily gratitude circle and nobody is excused from it today."),
        ("Lila", "I am grateful this will be over in under a minute if we all cooperate."),
        ("Hugo", "I am grateful that neither of you can force me to continue this exercise."),
        ("Marcus", "That is not the spirit Hugo and you know it so try again with feeling."),
        ("Lila", "He tried and it was honest which is more than the toaster ever managed."),
    ]
    idea = "A clay ensemble performs in a shared kitchen.\n" + "\n".join(
        f'{speaker}: “{text}”' for speaker, text in authored_turns
    )

    project = expand_idea(
        Brief(idea=idea, customer_kind=CustomerKind.SERIES, target_minutes=0.5)
    )

    characters_by_id = {c.id: c.name for c in project.bible.characters}
    actual_word_stream = [
        (characters_by_id[turn.character_id], word)
        for shot in project.shots
        for turn in shot.dialogue_turns
        for word in turn.text.split()
    ]
    expected_word_stream = [
        (speaker, word) for speaker, text in authored_turns for word in text.split()
    ]
    assert actual_word_stream == expected_word_stream
    assert len(project.shots) > 10  # grown past the 0.5-minute plan
    # Natural rate held: no shot carries more than a ~3.4s window's worth of words.
    assert all(
        sum(len(turn.text.split()) for turn in shot.dialogue_turns) <= 6
        for shot in project.shots
    )
    note = next(note for note in project.notes if note.id == "n-dialogue-fit")
    assert "grew from 10 to" in note.body
    assert "Every line is kept" in note.body


def test_sample_keeps_every_authored_word_with_brisk_delivery() -> None:
    """A fixed 5s sample cannot grow, so it packs the words instead of refusing."""
    authored = (
        "I thought gratitude would make this room feel calmer but somehow the coffee "
        "is judging us and Hugo has declared war on the toaster again this morning."
    )
    project = expand_idea(
        Brief(
            idea=f'In a shared kitchen.\nMarcus: "{authored}"',
            customer_kind=CustomerKind.SERIES,
            production_kind=ProductionKind.SAMPLE,
            sample_seconds=5,
        )
    )
    turns = [turn for shot in project.shots for turn in shot.dialogue_turns]
    assert " ".join(turn.text for turn in turns) == authored
    note = next(note for note in project.notes if note.id == "n-dialogue-fit")
    assert "sample" in note.body
    assert "words per shot" in note.body
