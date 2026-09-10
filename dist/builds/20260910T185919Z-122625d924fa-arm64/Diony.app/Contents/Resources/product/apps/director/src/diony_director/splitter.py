"""Deterministic script dissection into provider-legal ClipUnits.

Doctrine (Blueprint 26 §4, revised by Blueprint 33): split on a camera or
subject change or a dialogue breath, never mid-word; a dialogue line stays in
one clip; duration comes from the beats plus the spoken words and handles,
quantized UP to the provider grid; room tone carries across every join in a
scene; dialogue rides the voice-capable lane.

Blueprint 33 — long takes. A scene's beats are PACKED into takes as long as
the route's longest legal clip (Grok 15s, omni 10s, Veo 8s) and as even as
the beats allow: a 17-second scene is two takes of eight and nine seconds,
never four clips of four. Fewer generations means fewer identity resets,
fewer chain joins, and motion that plays out instead of restarting every
four seconds. Two rules never bend inside a take: at most ONE spoken line
(every downstream consumer — the compiler, the cue sheet, the review, the
subtitles — reads one excerpt per clip), and no take longer than the cap.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

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
from diony_director import estimate as estimate_mod
from diony_director.estimate import quantize_duration

WORDS_PER_SECOND = 2.5
DIALOGUE_MODEL = "omni-flash"
PICTURE_MODEL = "grok-imagine-video-1.5"
PRE_HANDLE_S = 0.5
TAIL_HANDLE_S = 1.0

# What one authored beat naturally plays for: an action line is one complete
# physical sentence (~4s of motion); a scene's opening beat breathes with the
# room (~6s). These are planning weights, not trims — the take books their sum.
ACTION_BEAT_S = 4.0
OPENER_BEAT_S = 6.0

# A sound line that describes MUSIC is a score cue for the whole scene, not a
# one-clip foley event: every clip must describe the same piece, or thirteen
# generations invent thirteen unrelated jingles.
MUSIC_HINTS = (
    "music",
    "groove",
    "tune",
    "theme",
    "melody",
    "song",
    "riff",
    "jingle",
    "anthem",
    "instrumental",
    "soundtrack",
    "score",
    "bassline",
    "beat drops",
)

_WIDE_WORDS = ("wide", "establish", "master", "aerial", "overhead", "top-", "drone")


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


# --- lanes and take units ---------------------------------------------------


def lanes_for(
    provider_policy: str,
) -> tuple[tuple[CloudProvider, str], tuple[CloudProvider, str]]:
    """((picture provider, model), (dialogue provider, model)) for the camera
    Studio chose. One camera shoots the whole film — one model, one look,
    picture and dialogue alike.

    "grok": everything on Grok Imagine; dialogue speaks through native audio
    with the voice honestly unlocked (no voice_ref). "google-flow": the whole
    package rides Omni 1.1 Flash — audio-native, characters and refs,
    start/end frames, v2v editing, the 4/6/8/10s grid on any plan. The Veo
    tiers stay reachable by explicit model choice (and as the extension lane),
    never as a default.
    """
    if provider_policy == "grok":
        return (CloudProvider.GROK, PICTURE_MODEL), (CloudProvider.GROK, PICTURE_MODEL)
    if provider_policy == "google-flow":
        return (CloudProvider.GOOGLE_FLOW, DIALOGUE_MODEL), (
            CloudProvider.GOOGLE_FLOW,
            DIALOGUE_MODEL,
        )
    raise ValueError(f"unknown camera {provider_policy!r}; choose grok or google-flow")


def _studio_policy() -> str:
    """The camera Studio chose; a missing choice is refused, never guessed."""
    from diony_director import session

    return session.engine_policy()


def take_unit(provider: CloudProvider, model: str) -> int:
    """The longest clip this lane books in one generation — the take length
    every scene is packed toward (Blueprint 33)."""
    if provider is CloudProvider.GROK:
        return estimate_mod.GROK_MAX_S
    if model in estimate_mod.OMNI_MODELS:
        return estimate_mod.OMNI_DURATIONS[-1]
    return estimate_mod.veo_grid()[-1]


def take_units(provider_policy: str | None = None) -> dict[str, int]:
    """{"picture": s, "spoken": s} — the take lengths a policy packs toward."""
    picture, spoken = lanes_for(provider_policy or _studio_policy())
    return {"picture": take_unit(*picture), "spoken": take_unit(*spoken)}


def take_units_phrase(units: dict[str, int]) -> str:
    """'up to 15s (10s when a line is spoken in it)' — for replies and briefs."""
    picture, spoken = int(units.get("picture", 0)), int(units.get("spoken", 0))
    if picture and spoken and picture != spoken:
        return f"up to {picture}s ({spoken}s when a line is spoken in it)"
    return f"up to {picture or spoken}s"


# --- beats and packing ------------------------------------------------------


@dataclass
class _Beat:
    line: ScriptLine
    natural_s: float
    spoken: bool
    parenthetical: str = ""
    foley: list[str] = field(default_factory=list)
    after_spoken: bool = False  # a picture beat that directly follows a line


def _beats_for(scene_lines: list[ScriptLine]) -> tuple[list[_Beat], str]:
    """The scene's beats in order, and its scripted music (if any)."""
    scene_score = next(
        (l.text for l in scene_lines if l.kind == "sound" and _is_music_cue(l.text)),
        "",
    )
    beats: list[_Beat] = []
    pending_parenthetical = ""
    pending_foley: list[str] = []
    prev_spoken = False
    for line in scene_lines:
        if line.kind in {"heading", "cue"}:
            continue
        if line.kind == "sound":
            if _is_music_cue(line.text):
                continue  # already the scene-wide score, never per-clip foley
            # A sound line enriches the picture around it: foley on the
            # nearest beat (the one before it, else the first to come).
            if beats:
                beats[-1].foley.append(line.text)
            else:
                pending_foley.append(line.text)
            continue
        if line.kind == "parenthetical":
            pending_parenthetical = line.text
            continue
        if line.kind == "action":
            beat = _Beat(
                line=line,
                natural_s=OPENER_BEAT_S if not beats else ACTION_BEAT_S,
                spoken=False,
                after_spoken=prev_spoken,
            )
            prev_spoken = False
        elif line.kind in {"dialogue", "vo"}:
            beat = _Beat(
                line=line,
                natural_s=_speech_duration(line.text),
                spoken=True,
                parenthetical=pending_parenthetical,
            )
            pending_parenthetical = ""
            prev_spoken = True
        else:  # pragma: no cover - ScriptLine.kind is closed
            continue
        if pending_foley:
            beat.foley.extend(pending_foley)
            pending_foley = []
        beats.append(beat)
    return beats, scene_score


# How far a take's beats may stretch or compress to meet the scene's even
# share: an action beat is a planning weight (~4s), not a stopwatch, so a
# 16s run of beats plays comfortably as one 15s take. Speech never compresses.
PACE_TOLERANCE = 0.25


@dataclass
class Take:
    beats: list[_Beat]
    paced_s: float  # the length the take is designed to play, before the grid

    @property
    def natural_s(self) -> float:
        return sum(b.natural_s for b in self.beats)

    @property
    def hard_s(self) -> float:
        """The seconds that can never compress: the spoken line and handles."""
        return sum(b.natural_s for b in self.beats if b.spoken)

    @property
    def spoken(self) -> bool:
        return any(b.spoken for b in self.beats)


def _group_cap(beats: list[_Beat], *, picture_unit: int, spoken_unit: int) -> int:
    return spoken_unit if any(b.spoken for b in beats) else picture_unit


def _group_feasible(beats: list[_Beat], *, picture_unit: int, spoken_unit: int) -> bool:
    """One spoken line at most; the words fit the cap; the beats can pace
    down to the cap. A lone beat is always feasible (it has nowhere to go)."""
    if len(beats) == 1:
        return True
    if sum(1 for b in beats if b.spoken) > 1:
        return False
    cap = _group_cap(beats, picture_unit=picture_unit, spoken_unit=spoken_unit)
    hard = sum(b.natural_s for b in beats if b.spoken)
    natural = sum(b.natural_s for b in beats)
    return hard <= cap + 1e-9 and natural <= cap * (1 + PACE_TOLERANCE) + 1e-9


def _partition(
    beats: list[_Beat], count: int, share: float, *, picture_unit: int, spoken_unit: int
) -> list[list[_Beat]] | None:
    """The contiguous split of `beats` into exactly `count` feasible groups
    whose lengths sit closest to `share` (least squared deviation), or None
    when no such split exists."""
    total = len(beats)
    if count > total:
        return None
    inf = float("inf")
    # best[k][i]: least cost splitting beats[:i] into k groups; cut[k][i]: where the last group starts
    best = [[inf] * (total + 1) for _ in range(count + 1)]
    cut = [[-1] * (total + 1) for _ in range(count + 1)]
    best[0][0] = 0.0
    prefix = [0.0]
    for beat in beats:
        prefix.append(prefix[-1] + beat.natural_s)
    for k in range(1, count + 1):
        for i in range(k, total + 1):
            for j in range(k - 1, i):
                if best[k - 1][j] == inf:
                    continue
                group = beats[j:i]
                if not _group_feasible(group, picture_unit=picture_unit, spoken_unit=spoken_unit):
                    continue
                cost = best[k - 1][j] + (prefix[i] - prefix[j] - share) ** 2
                if cost < best[k][i]:
                    best[k][i] = cost
                    cut[k][i] = j
    if best[count][total] == inf:
        return None
    groups: list[list[_Beat]] = []
    i = total
    for k in range(count, 0, -1):
        j = cut[k][i]
        groups.append(beats[j:i])
        i = j
    groups.reverse()
    return groups


def pack_takes(beats: list[_Beat], *, picture_unit: int, spoken_unit: int) -> list[Take]:
    """Group a scene's beats into takes: the fewest the cap allows, as even
    as the beats permit, never two spoken lines in one take, each paced to
    the scene's even share.

    The scene's natural length is divided into the fewest takes the unit
    allows; the beats are partitioned so every take sits closest to that
    even share (a 30s run of seven beats is two 15s takes; an 18s run is
    9s + 9s, never 15s + 3s); when the one-line rule or the cap makes that
    count impossible, one more take is tried. A take that holds a spoken
    line is capped by the dialogue lane's unit (10s on omni), a picture-only
    take by the picture lane's (15s on Grok). Each take is then PACED to the
    share — its beats stretch or compress up to a quarter to meet it, the
    words never compress, and nothing exceeds its cap."""
    if not beats:
        return []
    has_spoken = any(b.spoken for b in beats)
    unit = spoken_unit if has_spoken else picture_unit
    total_s = sum(b.natural_s for b in beats)
    groups: list[list[_Beat]] | None = None
    share = total_s
    # the fewest takes the cap allows once the beats may compress a quarter
    # to meet it — 18s of beats is ONE 15s take on Grok, not 9s + 9s
    fewest = max(1, math.ceil(total_s / (unit * (1 + PACE_TOLERANCE)) - 1e-9))
    for count in range(fewest, len(beats) + 1):
        share = total_s / count
        groups = _partition(beats, count, share, picture_unit=picture_unit, spoken_unit=spoken_unit)
        if groups is not None:
            break
    if groups is None:  # pragma: no cover - singleton groups are always feasible
        groups = [[b] for b in beats]
        share = total_s / len(groups)
    takes: list[Take] = []
    for group in groups:
        natural = sum(b.natural_s for b in group)
        hard = sum(b.natural_s for b in group if b.spoken)
        cap = _group_cap(group, picture_unit=picture_unit, spoken_unit=spoken_unit)
        paced = min(max(share, natural * (1 - PACE_TOLERANCE)), natural * (1 + PACE_TOLERANCE))
        # the words never compress; nothing books past its cap — except a
        # lone line longer than the cap, which rides the cap uncut and untrimmed
        paced = max(min(paced, cap), hard)
        takes.append(Take(beats=group, paced_s=round(paced, 2)))
    return takes


def _join_cameras(pieces: list[str]) -> str:
    """One take's camera as a progression: 'A; then b; then c'."""
    cleaned = [p.strip().rstrip(".") for p in pieces if p and p.strip()]
    if not cleaned:
        return ""
    out = cleaned[0]
    for piece in cleaned[1:]:
        # lowercase a leading capital that is plain prose, never an acronym
        # or a NAME the script shouts
        head = piece[:1].lower() + piece[1:] if len(piece) > 1 and piece[1].islower() else piece
        out += f"; then {head}"
    return out


def split_script(
    script: Script,
    characters: list[CharacterProfile],
    settings: list[SettingProfile],
    *,
    provider_policy: str | None = None,
    production_style: str = "narrated",
    cast_lock: str = "standard",
) -> list[ClipUnit]:
    """Provider policy comes from Studio's engine choice; the production
    style (Blueprint 31) decides each spoken clip's voicing — post narrator
    or the character in the clip — and lands it on the clip's audio packet.
    Cast lock (Blueprint 32) decides who rides a spoken take's identity refs.
    """
    provider_policy = provider_policy or _studio_policy()
    (picture_provider, picture_model), (dialogue_provider, dialogue_model) = lanes_for(
        provider_policy
    )
    dialogue_voice_locked = dialogue_provider is CloudProvider.GOOGLE_FLOW
    picture_unit = take_unit(picture_provider, picture_model)
    spoken_unit = take_unit(dialogue_provider, dialogue_model)
    from diony_director import styles

    style = styles.normalize_style(production_style)

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
        beats, scene_score = _beats_for(scene_lines)
        takes = pack_takes(beats, picture_unit=picture_unit, spoken_unit=spoken_unit)
        scene_clips: list[ClipUnit] = []
        coverage_no = 0
        last_speaker: CharacterProfile | None = None

        def listener_for(speaker: CharacterProfile | None) -> CharacterProfile | None:
            return next(
                (c for c in cast_chars if speaker is None or c.char_id != speaker.char_id),
                None,
            )

        for take_no, packed in enumerate(takes, start=1):
            take = packed.beats
            first_take = take_no == 1
            spoken = next((b for b in take if b.spoken), None)
            natural_s = packed.paced_s
            line_ids = [b.line.line_id for b in take]
            foley = [{"event": event} for b in take for event in b.foley]

            # --- the spoken line's cast and camera, when the take carries one
            char: CharacterProfile | None = None
            visible_cast: list[CharacterProfile] = []
            off_screen = False
            on_camera = False
            if spoken is not None:
                char = _character_for(spoken.line.speaker, characters)
                # Blueprint 32: an (O.S.) dialogue line is the character's
                # line spoken from outside the frame — the picture holds on
                # who hears it, and the speaker is never in the shot.
                off_screen = bool(
                    spoken.line.kind == "dialogue" and getattr(spoken.line, "off_screen", False)
                )
                on_camera = spoken.line.kind == "dialogue" and not off_screen
                visible_cast = [c for c in cast_chars if c.char_id != char.char_id]

            def spoken_camera() -> str:
                assert spoken is not None and char is not None
                listener = next((c for c in cast_chars if c.char_id != char.char_id), None)
                if off_screen:
                    from diony_director import cast as cast_mod

                    return cast_mod.offscreen_camera(
                        char.name, [c.name for c in visible_cast], setting.name
                    )
                if not on_camera:
                    return f"Picture under voice-over, drifting through {setting.name}"
                if listener is not None:
                    return (
                        f"Over-the-shoulder shot favoring {char.name}, "
                        f"{listener.name}'s shoulder soft in the foreground"
                    )
                return f"Close-up on {char.name}, eye level"

            # --- the take's camera: every authored shot in order, fallbacks
            # only where a take opens or a reaction follows a line — never a
            # coverage rotation inside one continuous take
            pieces: list[str] = []
            first_authored = ""
            for position, beat in enumerate(take):
                authored = (beat.line.shot or "").strip().rstrip(".")
                if authored:
                    if not pieces:
                        first_authored = authored
                    pieces.append(authored)
                    continue
                if beat.spoken:
                    pieces.append(spoken_camera())
                    continue
                if first_take and position == 0:
                    pieces.append(
                        f"Wide establishing shot of {setting.name}, static frame at eye level"
                    )
                    continue
                if beat.after_spoken:
                    # the beat that answers a line: the listener's face
                    listener = listener_for(last_speaker if position == 0 else char)
                    pieces.append(
                        f"Reaction close-up on {listener.name}, holding on their face"
                        if listener is not None
                        else f"Close reaction shot inside {setting.name}"
                    )
                    continue
                if not pieces:
                    size = coverage_sizes[coverage_no % len(coverage_sizes)]
                    move = coverage_moves[coverage_no % len(coverage_moves)]
                    coverage_no += 1
                    pieces.append(f"{size}, {move}, eye level")
            camera = _join_cameras(pieces)

            order += 1
            if spoken is not None:
                assert char is not None
                clip_class = "sync-dialogue" if spoken.line.kind == "dialogue" else "vo-over"
                provider, model = dialogue_provider, dialogue_model
                duration = quantize_duration(provider, model, math.ceil(natural_s))
                # where the words sit inside the take, paced to the booked length
                spoken_at = next(i for i, b in enumerate(take) if b is spoken)
                before_s = sum(b.natural_s for b in take[:spoken_at])
                line_at_s = round(before_s * (duration / packed.natural_s), 2) if before_s else 0.0
                listener = next((c for c in cast_chars if c.char_id != char.char_id), None)
                # Who the identity references are for: the people IN the
                # frame. An off-screen speaker's sheet must never ride a shot
                # they are not in (the model puts them in it); under Locked a
                # named listener in the frame carries their own card too.
                if off_screen:
                    packed_chars = visible_cast
                elif cast_lock == "locked" and on_camera and listener is not None:
                    packed_chars = [char, listener]
                else:
                    packed_chars = [char]
                clip = ClipUnit(
                    clip_id=f"cl-{scene_index}-{take_no}",
                    scene_id=scene.scene_id,
                    order=order,
                    script_line_ids=line_ids,
                    clip_class=clip_class,
                    duration_s=duration,
                    natural_s=round(natural_s, 2),
                    camera_idea=camera,
                    provider=provider,
                    model=model,
                    mode=CloudGenerationMode.T2V,
                    audio_packet=AudioPacket(
                        clip_class=clip_class,
                        script_excerpt=[
                            {
                                "speaker": spoken.line.speaker or char.name,
                                "line": spoken.line.text,
                                "parenthetical": spoken.parenthetical,
                                **(
                                    {
                                        "off_screen": True,
                                        "listeners": [c.name for c in visible_cast],
                                    }
                                    if off_screen
                                    else {}
                                ),
                            }
                        ],
                        voice_ref=(f"vox-{char.char_id}@v1" if dialogue_voice_locked else ""),
                        delivery={"pace": "natural", "distance": "close, dry"}
                        if on_camera
                        else {"pace": "measured", "distance": "close, dry"},
                        on_camera=on_camera,
                        voicing=styles.voicing_for(
                            style, on_camera=on_camera, off_screen=off_screen
                        ),
                        line_at_s=line_at_s,
                        room_tone_id=tone,
                        foley=foley,
                        score="in" if scene_score else "none",
                        score_desc=scene_score,
                    ),
                    continuity=ContinuityLock(
                        lock_recipe="character_asset" if dialogue_voice_locked else "ref",
                        character_packs=[
                            {
                                "char_id": packed.char_id,
                                "sheet_version": packed.sheet_version,
                                "pack_kind": "flow_character"
                                if dialogue_voice_locked
                                else "grok_refs",
                            }
                            for packed in packed_chars
                        ],
                        setting_plate=_plate(setting),
                    ),
                )
                last_speaker = char
            else:
                # Only the scene's FIRST take is its establishing shot; a later
                # picture take is coverage, never a plate reset. An opener
                # breathes with the room's own ambience — a forced silent-
                # picture cut a dead hole into the middle of the film.
                clip_class = "ambience-led" if first_take else "score-led"
                provider, model = picture_provider, picture_model
                duration = quantize_duration(provider, model, math.ceil(natural_s))
                if first_take:
                    # The plate can only open the scene when the authored
                    # frame IS the room — a close-up generated from a wide
                    # plate first-frame fights its own opening image.
                    wide_ish = not first_authored or any(
                        w in first_authored.lower() for w in _WIDE_WORDS
                    )
                    recipe = "still" if wide_ish else "ref"
                else:
                    recipe = "ref"
                clip = ClipUnit(
                    clip_id=f"cl-{scene_index}-{take_no}",
                    scene_id=scene.scene_id,
                    order=order,
                    script_line_ids=line_ids,
                    clip_class=clip_class,
                    duration_s=duration,
                    natural_s=round(natural_s, 2),
                    camera_idea=camera,
                    provider=provider,
                    model=model,
                    mode=CloudGenerationMode.T2V,
                    audio_packet=AudioPacket(
                        clip_class=clip_class,
                        room_tone_id=tone,
                        foley=foley,
                        score="in" if (clip_class == "score-led" or scene_score) else "none",
                        score_desc=scene_score,
                    ),
                    continuity=ContinuityLock(
                        lock_recipe=recipe,
                        character_packs=_packs(scene.cast, characters, "grok_refs"),
                        setting_plate=_plate(setting),
                    ),
                )
            clips.append(clip)
            scene_clips.append(clip)

        # Authored joins: how each clip meets the next. Inside a scene the
        # picture bridges; a spoken line cuts to its reaction; the last clip of
        # a scene is a hard cut into the next scene.
        for position, clip in enumerate(scene_clips):
            if position == len(scene_clips) - 1:
                clip.join_next = "cut"
            elif clip.clip_class in {"sync-dialogue", "vo-over"} and scene_clips[
                position + 1
            ].camera_idea.startswith("Reaction"):
                clip.join_next = "reaction_cut"
            else:
                clip.join_next = "bridge"

    return clips
