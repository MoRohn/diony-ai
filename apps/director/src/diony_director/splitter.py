"""Deterministic script dissection into provider-legal ClipUnits.

Blueprint 26 §2.2: split on camera/subject change or a dialogue breath, never
mid-word; a dialogue line stays in one clip; duration comes from the spoken
words plus handles, quantized UP to the provider grid; room tone carries
across every join in a scene; dialogue rides the voice-capable lane.
"""

from __future__ import annotations

import math

from diony_contracts.director import CloudGenerationMode, CloudProvider
from diony_contracts.production import (
    AudioPacket,
    CharacterProfile,
    ClipUnit,
    ContinuityLock,
    Script,
    ScriptLine,
    SettingProfile,
)
from diony_director.estimate import quantize_duration

WORDS_PER_SECOND = 2.5
DIALOGUE_MODEL = "omni-flash"
PICTURE_MODEL = "grok-imagine-video-1.5"
PRE_HANDLE_S = 0.5
TAIL_HANDLE_S = 1.0

# A sound line that describes MUSIC is a score cue for the whole scene, not a
# one-clip foley event: every clip must describe the same piece, or thirteen
# generations invent thirteen unrelated jingles.
MUSIC_HINTS = (
    "music", "groove", "tune", "theme", "melody", "song", "riff", "jingle",
    "anthem", "instrumental", "soundtrack", "score", "bassline", "beat drops",
)


def _is_music_cue(text: str) -> bool:
    lowered = text.lower()
    return any(hint in lowered for hint in MUSIC_HINTS)


def _speech_duration(text: str) -> float:
    words = max(1, len(text.split()))
    return words / WORDS_PER_SECOND + PRE_HANDLE_S + TAIL_HANDLE_S


def _character_for(speaker: str, characters: list[CharacterProfile]) -> CharacterProfile:
    for char in characters:
        if char.name.upper() == speaker.upper():
            return char
    return characters[0]


def _plate(setting: SettingProfile) -> dict:
    plate = {"setting_id": setting.setting_id, "plate_version": setting.plate_version}
    if setting.plate_asset_id:
        plate["asset_id"] = setting.plate_asset_id
    return plate


def _packs(cast: list[str], characters: list[CharacterProfile], pack_kind: str) -> list[dict]:
    known = {c.char_id: c for c in characters}
    return [
        {
            "char_id": char_id,
            "sheet_version": known[char_id].sheet_version,
            "pack_kind": pack_kind,
        }
        for char_id in cast
        if char_id in known
    ]


def split_script(
    script: Script,
    characters: list[CharacterProfile],
    settings: list[SettingProfile],
    *,
    provider_policy: str = "auto",
) -> list[ClipUnit]:
    """Provider policy comes from Studio's engine choice.

    "auto": doctrine routing — picture on Grok, dialogue on the voice-capable
    Omni lane. "grok": everything on Grok; dialogue speaks through native audio
    with the voice honestly unlocked (no voice_ref). "google-flow": picture on
    Veo, dialogue on Omni as usual.
    """
    if provider_policy == "grok":
        picture_provider, picture_model = CloudProvider.GROK, PICTURE_MODEL
        dialogue_provider, dialogue_model = CloudProvider.GROK, PICTURE_MODEL
    elif provider_policy == "google-flow":
        picture_provider, picture_model = CloudProvider.GOOGLE_FLOW, "veo-3.1-fast"
        dialogue_provider, dialogue_model = CloudProvider.GOOGLE_FLOW, DIALOGUE_MODEL
    else:
        picture_provider, picture_model = CloudProvider.GROK, PICTURE_MODEL
        dialogue_provider, dialogue_model = CloudProvider.GOOGLE_FLOW, DIALOGUE_MODEL
    dialogue_voice_locked = dialogue_provider is CloudProvider.GOOGLE_FLOW

    settings_by_id = {s.setting_id: s for s in settings}
    known_chars = {c.char_id: c for c in characters}
    clips: list[ClipUnit] = []
    order = 0

    coverage_sizes = ("Medium shot", "Wide shot", "Medium close shot")
    coverage_moves = ("slow push-in", "held frame", "slow lateral drift")

    for scene_index, scene in enumerate(script.scenes, start=1):
        setting = settings_by_id[scene.setting_ref]
        tone = setting.room_tone_id
        scene_lines = [script.line(i) for i in scene.line_ids]
        cast_chars = [known_chars[c] for c in scene.cast if c in known_chars]
        # The scene's scripted music, if any — carried verbatim on every clip
        # so each generation scores the same piece.
        scene_score = next(
            (l.text for l in scene_lines if l.kind == "sound" and _is_music_cue(l.text)),
            "",
        )
        scene_clips: list[ClipUnit] = []
        clip_no = 0
        coverage_no = 0
        pending_parenthetical = ""
        prev_was_dialogue = False
        last_speaker: CharacterProfile | None = None
        last_picture: ClipUnit | None = None

        def picture_clip(line: ScriptLine) -> ClipUnit:
            """ONE action beat = one clip, its camera a complete sentence —
            never a truncated prose fragment, never a synthetic empty opener.
            Only the scene's FIRST clip is its establishing shot; an action
            beat later in the scene is coverage, never a plate reset."""
            nonlocal order, clip_no, coverage_no
            clip_no += 1
            order += 1
            if not scene_clips:
                # An opener breathes with the room's own ambience — a forced
                # silent-picture cut a dead 6s hole into the middle of the film.
                clip_class = "ambience-led"
                camera = (
                    f"Wide establishing shot of {setting.name}, static frame at eye level"
                )
                duration = quantize_duration(picture_provider, picture_model, 6)
                recipe = "still"
            elif prev_was_dialogue:
                clip_class = "score-led"
                listener = next(
                    (
                        c
                        for c in cast_chars
                        if last_speaker is None or c.char_id != last_speaker.char_id
                    ),
                    None,
                )
                camera = (
                    f"Reaction close-up on {listener.name}, holding on their face"
                    if listener is not None
                    else f"Close reaction shot inside {setting.name}"
                )
                duration = quantize_duration(picture_provider, picture_model, 4)
                recipe = "ref"
            else:
                clip_class = "score-led"
                size = coverage_sizes[coverage_no % len(coverage_sizes)]
                move = coverage_moves[coverage_no % len(coverage_moves)]
                coverage_no += 1
                camera = f"{size}, {move}, eye level"
                duration = quantize_duration(picture_provider, picture_model, 4)
                recipe = "ref"
            clip = ClipUnit(
                clip_id=f"cl-{scene_index}-{clip_no}",
                scene_id=scene.scene_id,
                order=order,
                script_line_ids=[line.line_id],
                clip_class=clip_class,
                duration_s=duration,
                camera_idea=camera,
                provider=picture_provider,
                model=picture_model,
                mode=CloudGenerationMode.T2V,
                audio_packet=AudioPacket(
                    clip_class=clip_class,
                    room_tone_id=tone,
                    score="in" if (clip_class == "score-led" or scene_score) else "none",
                    score_desc=scene_score,
                ),
                continuity=ContinuityLock(
                    lock_recipe=recipe,
                    character_packs=_packs(scene.cast, characters, "grok_refs"),
                    setting_plate=_plate(setting),
                ),
            )
            return clip

        for line in scene_lines:
            if line.kind == "heading":
                continue
            if line.kind == "sound":
                if _is_music_cue(line.text):
                    continue  # already the scene-wide score, never per-clip foley
                # A sound line enriches the pictures around it: foley on the
                # nearest picture clip, and a silent opener becomes ambience-led.
                if last_picture is not None:
                    last_picture.audio_packet.foley.append({"event": line.text})
                    if last_picture.clip_class == "silent-picture":
                        last_picture.clip_class = "ambience-led"
                        last_picture.audio_packet.clip_class = "ambience-led"
                continue
            if line.kind == "cue":
                continue
            if line.kind == "parenthetical":
                pending_parenthetical = line.text
                continue
            if line.kind == "action":
                clip = picture_clip(line)
                clips.append(clip)
                scene_clips.append(clip)
                last_picture = clip
                prev_was_dialogue = False
                continue
            if line.kind in {"dialogue", "vo"}:
                clip_no += 1
                order += 1
                char = _character_for(line.speaker, characters)
                raw = _speech_duration(line.text)
                duration = quantize_duration(
                    dialogue_provider, dialogue_model, math.ceil(raw)
                )
                on_camera = line.kind == "dialogue"
                clip_class = "sync-dialogue" if on_camera else "vo-over"
                listener = next(
                    (c for c in cast_chars if c.char_id != char.char_id), None
                )
                if not on_camera:
                    camera = f"Picture under voice-over, drifting through {setting.name}"
                elif listener is not None:
                    camera = (
                        f"Over-the-shoulder shot favoring {char.name}, "
                        f"{listener.name}'s shoulder soft in the foreground"
                    )
                else:
                    camera = f"Close-up on {char.name}, eye level"
                clip = ClipUnit(
                    clip_id=f"cl-{scene_index}-{clip_no}",
                    scene_id=scene.scene_id,
                    order=order,
                    script_line_ids=[line.line_id],
                    clip_class=clip_class,
                    duration_s=duration,
                    camera_idea=camera,
                    provider=dialogue_provider,
                    model=dialogue_model,
                    mode=CloudGenerationMode.T2V,
                    audio_packet=AudioPacket(
                        clip_class=clip_class,
                        script_excerpt=[
                            {
                                "speaker": line.speaker or char.name,
                                "line": line.text,
                                "parenthetical": pending_parenthetical,
                            }
                        ],
                        voice_ref=(
                            f"vox-{char.char_id}@v1" if dialogue_voice_locked else ""
                        ),
                        delivery={"pace": "natural", "distance": "close, dry"}
                        if on_camera
                        else {"pace": "measured", "distance": "close, dry"},
                        on_camera=on_camera,
                        room_tone_id=tone,
                        score="in" if scene_score else "none",
                        score_desc=scene_score,
                    ),
                    continuity=ContinuityLock(
                        lock_recipe="character_asset" if dialogue_voice_locked else "ref",
                        character_packs=[
                            {
                                "char_id": char.char_id,
                                "sheet_version": char.sheet_version,
                                "pack_kind": "flow_character"
                                if dialogue_voice_locked
                                else "grok_refs",
                            }
                        ],
                        setting_plate=_plate(setting),
                    ),
                )
                clips.append(clip)
                scene_clips.append(clip)
                pending_parenthetical = ""
                prev_was_dialogue = True
                last_speaker = char
                continue

        # Authored joins: how each clip meets the next. Inside a scene the
        # picture bridges; a spoken line cuts to its reaction; the last clip of
        # a scene is a hard cut into the next scene.
        for position, clip in enumerate(scene_clips):
            if position == len(scene_clips) - 1:
                clip.join_next = "cut"
            elif (
                clip.clip_class in {"sync-dialogue", "vo-over"}
                and scene_clips[position + 1].camera_idea.startswith("Reaction")
            ):
                clip.join_next = "reaction_cut"
            else:
                clip.join_next = "bridge"

    return clips
