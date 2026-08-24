from diony_contracts.enums import AssetKind, CustomerKind, Engine, VoiceRenderState
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
    assert [by_id[turn.character_id] for turn in turns] == [
        "Marcus",
        "Lila",
        "Marcus",
        "Hugo",
    ]
    assert all("Final hold" not in turn.text for turn in turns)
