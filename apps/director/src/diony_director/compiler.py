"""Provider-aware prompt compilation with bible injection and lossless locks.

Three dialects (Grok motion+sound, Veo cinema prose, Omni dialogue+performance),
each drawing wardrobe, forbidden drift, and setting grammar from the bibles. A
lock that cannot be expressed on the selected provider fails compilation loudly
— it is never dropped silently.
"""

from __future__ import annotations

import re

from diony_contracts.director import CloudProvider
from diony_contracts.production import (
    CharacterProfile,
    ClipUnit,
    ProductionPackage,
    PromptVersion,
    SettingProfile,
)
from diony_director import likeness

ANTI_ARTIFACT = (
    "No morphing, no floating motion, no warped anatomy, no extra limbs, "
    "no on-screen text, no plastic skin, no uncanny stare, no CGI sheen."
)

# Blueprint 28: the voice track is a POST instrument. Scripted words never
# ride a generator prompt as spoken dialogue — a provider-invented voice has
# the wrong timbre in every clip and stretches the clip to fit its own
# delivery. Every dialect tells the generator the location track is speechless;
# the narrator records the authored words against the assembled cut instead.
NO_SPEECH = (
    "The location sound carries no spoken words: no dialogue, no narration, "
    "no voice-over, no crowd chatter reading as speech — the voice track is "
    "recorded and mixed in post."
)

# Blueprint 31: under the Dialogue and Mix styles a spoken clip's words DO
# ride the prompt — quoted verbatim, with the instruction that the character
# voices them — and the location track is that voice and nothing else. The
# clause below rides beside the quoted line on every dialect.
ONLY_THE_LINE = (
    "The only speech on this track is that line, spoken once by that one "
    "voice — no narration, no voice-over, no other character speaks, no "
    "crowd chatter reading as words."
)

# Every Grok picture carries the same photographic ground truth — without it
# the model drifts toward game-engine gloss and stock-footage lighting.
REALISM = (
    "Filmed on a digital cinema camera: natural skin texture, true blacks, "
    "motivated practical light, physically accurate motion and weight, "
    "filmic contrast, documentary realism."
)

REVISION_DIRECTIVES = {
    "fix-face": (
        "Hold the face exactly to the reference identity: same bone structure, "
        "same hair, same eyes; no jewelry changes."
    ),
    "fix-audio": (
        "The location track must carry no spoken words at all — remove any "
        "dialogue, narration, or voice-like sound; natural ambience only. "
        "The voice track is recorded in post."
    ),
    "fix-light": "Keep the established light direction and color; do not relight the scene.",
    "fix-continuity": (
        "Match the scene's master look exactly — the same room in the same "
        "minute: same walls, screens, floor graphics, furniture, and light; "
        "no relight, no reset, no redesign."
    ),
    "fix-frames": (
        "Render clean, well-exposed, sharp frames for the full duration; "
        "no black frames, no freezes, no dropout."
    ),
    "extend-motion": "Continue the existing motion and sound without resetting the scene.",
    # Blueprint 31: a spoken clip whose character never voiced the line, or
    # voiced other words — the fix is the line, clearly, once, and nothing else
    "fix-line": (
        "The character must speak the quoted line aloud, clearly and audibly, "
        "exactly as written, once, lips in sync — and nothing else is spoken "
        "on this track."
    ),
}


class CompileError(ValueError):
    pass


def prompt_limit(provider: CloudProvider | None) -> int:
    from diony_director.providers.base import prompt_limit as _limit

    return _limit(provider.value if provider else "grok")


def _shrink(text: str, cap: int) -> str:
    if len(text) <= cap:
        return text
    if cap <= 1:
        return ""
    return text[:cap].rsplit(" ", 1)[0].rstrip(",.;:") + "…"


def _fit_prompt(pieces: list[tuple[str, int]], limit: int) -> str:
    """Assemble prompt pieces in display order within the provider limit.

    Priority 0 pieces (camera intent, scripted dialogue, anti-artifact) are
    untouchable; higher numbers shrink first, from gentle caps down to removal.
    The scripted words are never altered — that is the whole point of a script.
    """
    texts = [text for text, _ in pieces]

    def joined() -> str:
        return " ".join(t for t in texts if t).strip()

    if len(joined()) <= limit:
        return joined()
    priorities = sorted({rank for _, rank in pieces if rank > 0}, reverse=True)
    for cap in (400, 200, 96, 0):
        for rank in priorities:
            for index, (_, piece_rank) in enumerate(pieces):
                if piece_rank == rank and texts[index]:
                    texts[index] = _shrink(texts[index], cap)
            if len(joined()) <= limit:
                return joined()
    # Essentials alone still over: shrink the longest essential (never a quoted
    # dialogue piece if any other essential can give way first).
    essential_indexes = sorted(
        (i for i, (_, rank) in enumerate(pieces) if rank == 0 and texts[i]),
        key=lambda i: ('"' in texts[i], -len(texts[i])),
    )
    for index in essential_indexes:
        overflow = len(joined()) - limit
        if overflow <= 0:
            break
        texts[index] = _shrink(texts[index], max(16, len(texts[index]) - overflow - 1))
    return joined()[:limit]


def _characters_in(clip: ClipUnit, package: ProductionPackage) -> list[CharacterProfile]:
    chars = []
    for pack in clip.continuity.character_packs:
        try:
            chars.append(package.character(str(pack.get("char_id", ""))))
        except KeyError:
            continue
    return chars


def _character_block(char: CharacterProfile, story_time: str) -> str:
    parts = [f"{char.name}: {char.age_body_notes or 'one consistent person throughout'}"]
    wardrobe = char.wardrobe.get(story_time) or next(iter(char.wardrobe.values()), "")
    if wardrobe:
        parts.append(f"wardrobe: {wardrobe}")
    parts.append("; ".join(char.forbidden_drift))
    return ". ".join(parts)


def _action_text(clip: ClipUnit, package: ProductionPackage) -> str:
    texts = []
    for line_id in clip.script_line_ids:
        try:
            line = package.script.line(line_id)
        except KeyError:
            continue
        if line.kind == "action":
            texts.append(line.text)
    return " ".join(texts)


def _setting_block(setting: SettingProfile, heading: str) -> str:
    bits = [f"Setting: {setting.name} ({heading})"]
    if setting.time_weather:
        bits.append(setting.time_weather)
    if setting.grade_language:
        bits.append(f"look: {setting.grade_language}")
    if setting.camera_kit:
        bits.append(f"camera language: {setting.camera_kit}")
    return "; ".join(bits) + "."


def _continues_scene(clip: ClipUnit, package: ProductionPackage) -> bool:
    orders = [c.order for c in package.clips if c.scene_id == clip.scene_id]
    return bool(orders) and clip.order != min(orders)


def _delivery_clause(clip: ClipUnit) -> str:
    """The PERFORMANCE of a scripted line, without its words (Blueprint 28).

    An on-camera speaker still acts the delivery — posture, breath, mouth
    moving mid-conversation — so the post-recorded narration lands on a body
    that is visibly speaking. The authored words themselves never enter the
    prompt: a provider voice is the wrong voice, and its own pacing fights
    the booked clip length. A voice-over clip needs no performance at all.
    An off-screen line (Blueprint 32) is performed by the LISTENERS: the
    speaker is out of frame and no mouth on screen forms words."""
    excerpt = clip.audio_packet.script_excerpt
    if not excerpt:
        return ""
    entry = excerpt[0]
    if entry.get("off_screen"):
        from diony_director import cast as cast_mod

        return cast_mod.offscreen_delivery_clause(
            str(entry.get("speaker", "") or "The speaker"), list(entry.get("listeners") or [])
        )
    if not clip.audio_packet.on_camera:
        return ""
    parenthetical = entry.get("parenthetical", "")
    delivery = ", ".join(f"{k}: {v}" for k, v in clip.audio_packet.delivery.items())
    direction = "; ".join(x for x in (parenthetical, delivery) if x)
    who = entry.get("speaker", "") or "The speaker"
    return (
        f"{who} is speaking mid-conversation"
        + (f" ({direction})" if direction else "")
        + ", natural mouth movement and gesture, unhurried — their voice is "
        "recorded separately and is not heard on this track."
    )


def speaks_in_clip(clip: ClipUnit) -> bool:
    """Blueprint 31: does this clip's provider voice its own line? True for a
    line-carrying clip whose audio packet books generated voicing."""
    return bool(
        clip.audio_packet.script_excerpt
        and clip.audio_packet.voicing == "generated"
        and str(clip.audio_packet.script_excerpt[0].get("line", "")).strip()
    )


def speaker_voice_card(clip: ClipUnit, package: ProductionPackage | None) -> str:
    """Blueprint 31 §10: the VOICE card of the character who speaks this
    clip's line — the same bytes on every clip they speak in — or '' when
    the speaker is not a cast member (a package with no cast, an unnamed
    speaker) or the character has no card yet."""
    from diony_director import voices

    excerpt = clip.audio_packet.script_excerpt
    if package is None or not excerpt:
        return ""
    char = voices.character_for(str(excerpt[0].get("speaker", "")), package.characters)
    return voices.voice_card(char) if char is not None else ""


def _spoken_clause(clip: ClipUnit, package: ProductionPackage | None = None) -> str:
    """The line itself, quoted verbatim, with the instruction that the
    character voices it (Blueprint 31, Dialogue and Mix styles). An on-camera
    line is spoken in frame, lips in sync; a voice-over line is heard in the
    speaker's voice over the picture. The direction (parenthetical and the
    packet's delivery notes) shapes the read; the character's VOICE card
    (§10) says whose voice it is — gender, age, timbre — so the provider
    voices the person the script wrote and voices no two characters alike.
    The words are never paraphrased — _fit_prompt shrinks a quoted piece
    last of all."""
    excerpt = clip.audio_packet.script_excerpt
    if not excerpt:
        return ""
    entry = excerpt[0]
    line = str(entry.get("line", "")).strip()
    if not line:
        return ""
    who = str(entry.get("speaker", "")).strip() or "The speaker"
    parenthetical = str(entry.get("parenthetical", "")).strip()
    delivery = ", ".join(f"{k}: {v}" for k, v in clip.audio_packet.delivery.items())
    direction = "; ".join(x for x in (parenthetical, delivery) if x)
    read = f" — delivered {direction}" if direction else ""
    card = speaker_voice_card(clip, package)
    if entry.get("off_screen"):
        # Blueprint 32: the character's own voice, from beyond the frame edge
        from diony_director import cast as cast_mod

        return cast_mod.offscreen_spoken_clause(
            who, line, read, list(entry.get("listeners") or []), voice_card=card
        )
    if clip.audio_packet.on_camera:
        clause = (
            f"{who} speaks aloud, on camera, lips in sync with the words, exactly "
            f'these words and nothing else: "{line}"{read}. A natural, clear, '
            "close-miked voice that stays the same voice for the whole line."
        )
    else:
        clause = (
            f"A voice-over is heard on the track in {who}'s voice, exactly these "
            f'words and nothing else: "{line}"{read}. No one on screen mouths the '
            "words; the voice is clear and close-miked."
        )
    return f"{clause} {card}" if card else clause


def spoken_narration_clause(narration: str, *, voice_read: str = "") -> str:
    """A one-shot's (or chain link's) narration voiced INSIDE the clip
    (Blueprint 31, Dialogue style): the words quoted verbatim, spoken by the
    on-screen speaker when one is present and otherwise as a voice-over.
    `voice_read` (§10) is the film's one described voice — the narrator
    cast for this session — so every link of a chain speaks in the same
    voice instead of a new one per clip."""
    words = " ".join(str(narration or "").split())
    if not words:
        return ""
    clause = (
        "Spoken aloud on this clip's track, exactly these words and nothing "
        f'else: "{words}" — by the on-screen speaker, lips in sync, when one is '
        "present; otherwise as a clear voice-over. One natural, close-miked "
        "voice for the whole line."
    )
    read = " ".join(str(voice_read or "").split()).rstrip(".")
    if read:
        clause += f" VOICE: {read}; the same voice in every clip of this film."
    return clause


def _voice_pieces(
    clip: ClipUnit, package: ProductionPackage | None = None
) -> list[tuple[str, int]]:
    """The two untouchable voice pieces every dialect carries, by voicing:
    post (Blueprint 28) — the delivery performance without the words, plus
    the speechless-track law; generated (Blueprint 31) — the quoted line with
    the instruction to voice it (and the speaker's voice card), plus the
    one-voice law."""
    if speaks_in_clip(clip):
        return [(_spoken_clause(clip, package), 0), (ONLY_THE_LINE, 0)]
    return [(_delivery_clause(clip), 0), (NO_SPEECH, 0)]


def _sound_clause(clip: ClipUnit, setting: SettingProfile, *, continues: bool = True) -> str:
    """Every dialect names its soundtrack: room, foley, score in or out, and
    what must be absent. A model told nothing about sound invents a soundtrack."""
    from diony_director.splitter import _is_music_cue

    packet = clip.audio_packet
    events = [str(event.get("event", "")) for event in packet.foley if event.get("event")]
    # A music-describing foley event (older packages) is a score cue, never an
    # ambience event — it must not sit next to a "no music" directive.
    music_events = [e for e in events if _is_music_cue(e)]
    ambient = [e for e in events if e not in music_events]
    bits = [setting.architecture_notes or "natural ambience that belongs to the scene"]
    bits += ambient
    score_desc = packet.score_desc or (music_events[0] if music_events else "")
    if packet.score == "in" or score_desc:
        piece = score_desc or "a restrained underscore entering low"
        position = (
            "already playing, continuing at the same tempo, key, and instrumentation"
            if continues
            else "it establishes the tempo, key, and instrumentation every later shot keeps"
        )
        bits.append(
            f"score: {piece} — the one piece of music in this scene, {position}; "
            "never a restart, never a new tune, never a ringtone or jingle"
        )
    else:
        bits.append("no music")
    clause = f"Sound: {', '.join(b for b in bits if b)}."
    if packet.forbidden:
        clause += f" Never: {', '.join(packet.forbidden)}."
    return clause


CONTINUITY_CLAUSE = (
    "Continues the previous shot directly in the exact same place: same walls, "
    "same screens and signage, same floor graphics, same furniture in the same "
    "positions, same light rig and color; the room never redesigns itself; "
    "same wardrobe and people; no scene reset."
)

# Blocking cues in the writing become hard camera law in the prompt — the
# model happily rotated a back-to-camera figure to face the lens mid-clip
# (s-2e0a74644b), which also broke a concealed identity.
_BEHIND_TRIGGERS = ("back to camera", "from behind", "from directly behind", "seen from behind")
_CONCEAL_TRIGGERS = ("concealed", "faceless", "face hidden", "never seen", "hood drawn", "hooded")


def _blocking_locks(clip: ClipUnit, char_text: str, action: str, *, camera_text: str = "") -> str:
    # Scan the camera text that will actually ship — a lexicon clause that
    # expands to "seen from behind" must arm the lock like authored prose.
    haystack = f"{camera_text or clip.camera_idea} {action} {char_text}".lower()
    locks: list[str] = []
    if any(t in haystack for t in _BEHIND_TRIGGERS):
        locks.append(
            "the camera stays behind the subject for the entire shot and never "
            "crosses the axis; the subject never turns to face the camera"
        )
    if any(t in haystack for t in _CONCEAL_TRIGGERS):
        locks.append("the face is never visible in any frame")
    return f"Blocking lock: {'; '.join(locks)}." if locks else ""


def _master_pieces(clip: ClipUnit, package: ProductionPackage) -> dict[str, str]:
    """What every split clip inherits from the pre-split finalized artifact
    (Blueprint 27): the one lighting system, the physics grounding, the
    continuity anchors, the scene-specific constraints — and, on the last
    clip of the package only, the exact ending. Empty strings when the
    package was locked without a master prompt."""
    master = package.master_prompt
    if master is None:
        return {"lighting": "", "physics": "", "anchors": "", "constraints": "", "ending": ""}
    light = master.lighting
    lighting = ""
    # One lighting system is the template's promise for ONE world. A film that
    # moves between settings lights each scene from its own setting block —
    # the artifact's system rides only when every scene shares a setting.
    one_world = len({s.setting_ref for s in package.script.scenes}) <= 1
    if light.get("source") and one_world:
        quality = ", ".join(x for x in (light.get("quality"), light.get("shadows")) if x)
        lighting = f"Lighting comes from {light['source']}"
        if quality:
            lighting += f", producing {quality}"
        lighting += "."
    physics = ""
    if master.physics or master.body_mechanics:
        mech = "; ".join(x for x in (master.body_mechanics, master.physics) if x)
        physics = f"Movement follows believable body mechanics with {mech[:1].lower()}{mech[1:].rstrip('.')}."
    anchors = ""
    if master.continuity_anchors:
        anchors = (
            "Maintain strict continuity throughout: "
            + ", ".join(master.continuity_anchors)
            + " remain unchanged."
        )
    constraints = ""
    if master.constraints:
        constraints = "Constraints: " + "; ".join(master.constraints) + "."
    ending = ""
    if master.ending and package.clips and clip.clip_id == package.clips[-1].clip_id:
        ending = f"The clip ends with {master.ending[:1].lower()}{master.ending[1:].rstrip('.')}."
    return {
        "lighting": lighting,
        "physics": physics,
        "anchors": anchors,
        "constraints": constraints,
        "ending": ending,
    }


def look_for(package: ProductionPackage):
    """The film's Look Bible — on the master the package was written from."""
    master = package.master_prompt
    return master.look if master is not None else None


def _look_block(package: ProductionPackage) -> str:
    """The verbatim LOOK BIBLE clause every clip of the film carries (Blueprint
    29). Empty for a package locked before the look existed — those prompts
    compile byte-identical to before."""
    look = look_for(package)
    return look.block() if look is not None else ""


def _realism_clause(package: ProductionPackage) -> str:
    """Grok's photographic ground truth rides a photoreal look only. Under an
    animated or illustrated look it is the contradiction that pulled every
    shot back toward live-action gloss (s-e790706066)."""
    look = look_for(package)
    return REALISM if look is None or look.photoreal else ""


def _flow_style_slot(package: ProductionPackage, inline_world: str) -> str:
    """The Veo/Omni style slot beside the Look Bible: an authored world (inline
    or a chosen session world) always rides; the BASELINE cinema clause yields
    to a non-photoreal look — 'shot on a cinema camera, 35mm lens' over an
    animated film is the same contradiction as REALISM."""
    from diony_director import flow_lexicon

    look = look_for(package)
    clause = flow_lexicon.style_clause(package.session_id, override_world=inline_world)
    if look is None or look.photoreal:
        return clause
    chosen = inline_world or flow_lexicon.template_for(package.session_id)["world"]
    if chosen and chosen != flow_lexicon.BASELINE_WORLD:
        return clause
    return ""


def _base_prompt(
    clip: ClipUnit,
    package: ProductionPackage,
    *,
    pictured_people: list[dict] | None = None,
    limit: int | None = None,
) -> str:
    """The clip's prompt, fitted to `limit` (the provider's, unless a caller
    reserves room for a rider). The likeness guard runs on the PIECES, before
    the fit: a name that becomes a forty-character description after the
    fit is a prompt the provider refuses (s-8a4239727b: fifteen clips held at
    4100–4258 chars against Flow's 4096)."""
    setting = package.setting(str(clip.continuity.setting_plate["setting_id"]))
    scene = package.script.scene(clip.scene_id)
    chars = _characters_in(clip, package)
    locked = getattr(package, "cast_lock", "standard") == "locked"
    if locked:
        # Blueprint 32: the identity card, byte-identical on every prompt that
        # shows the character, untouchable under length pressure, and placed
        # BEFORE scene and style so atmosphere never dilutes the face.
        from diony_director import cast as cast_mod

        char_text = " ".join(cast_mod.identity_block(c) for c in chars)
    else:
        char_text = " ".join(_character_block(c, scene.story_time) for c in chars)
    identity_early = (char_text, 0) if locked else ("", 0)
    identity_late = ("", 3) if locked else (char_text, 3)
    action = _action_text(clip, package)
    continuity = CONTINUITY_CLAUSE if _continues_scene(clip, package) else ""
    master = _master_pieces(clip, package)
    look_block = _look_block(package)
    # Blueprint 28 or 31, by the clip's own voicing: the mute-track delivery
    # performance, or the quoted line the character voices in the clip.
    voice = _voice_pieces(clip, package)

    if limit is None:
        limit = prompt_limit(clip.provider)

    def _fit(pieces: list[tuple[str, int]], cap: int) -> str:
        return _fit_prompt(_described(pieces, package, pictured_people), cap)

    if clip.provider is CloudProvider.GROK:
        # The Grok dialect alone speaks the Grok lexicon: /shorthand in the
        # authored shot expands with the guide's reliable phrasings, unreliable
        # camera-lock wording is hardened ("steady shot" drifts; "camera not
        # moving" holds), and the session's atmosphere — an environmental
        # signature, like scene-wide weather — rides every picture unless the
        # shot already carries its own. Flow tokens stay literal here.
        from diony_director import grok_lexicon

        camera_text, _, atmo_seen = grok_lexicon.expand(clip.camera_idea)
        camera_text = grok_lexicon.harden_camera_locks(camera_text).strip().rstrip(".")
        if not camera_text:
            camera_text = clip.camera_idea.strip().rstrip(".")
        # The session atmosphere rides only when the whole package rolls on
        # Grok: under a mixed package (auto policy — dialogue on omni), an
        # atmosphere the Flow lane can never carry would make the spoken
        # shots mismatch the pictures around them — the same one-look
        # doctrine that keeps the Flow world off mixed-package dialogue.
        # An inline /atmosphere token stays authored law for its own clip.
        # …and never against the scripted weather: "Rain falls" over a
        # setting that reads "Night 1, clear" makes the picture fight the
        # script — the setting is law, the atmosphere yields.
        mixed = any(c.provider is not CloudProvider.GROK for c in package.clips)
        atmosphere = (
            ""
            if (atmo_seen or mixed)
            else grok_lexicon.session_atmosphere_clause(
                package.session_id,
                weather=str(getattr(setting, "time_weather", "") or ""),
            )
        )
        return _fit(
            [
                (f"{camera_text}." if camera_text else "", 0),
                (_blocking_locks(clip, char_text, action, camera_text=camera_text), 0),
                identity_early,
                (continuity, 1),
                (master["anchors"], 1),
                (
                    action or "The action continues truthfully; no reset, no new business.",
                    3,
                ),
                (master["physics"], 2),
                (f"{grok_lexicon.sentence(atmosphere)}." if atmosphere else "", 2),
                (_setting_block(setting, scene.heading), 2),
                (look_block, 0),
                (master["lighting"], 2),
                voice[0],
                (_realism_clause(package), 1),
                (_sound_clause(clip, setting, continues=bool(continuity)), 2),
                voice[1],
                identity_late,
                (master["ending"], 1),
                (master["constraints"], 1),
                (ANTI_ARTIFACT, 0),
            ],
            limit,
        )

    # Both Google lanes speak the Flow lexicon — /shorthand in the authored
    # shot expands, and a chosen world rides dialogue clips too, so spoken
    # shots hold the same look as the pictures around them. The baseline
    # world adds nothing here: the omni dialect stays exactly itself.
    from diony_director import flow_lexicon

    camera_text, inline_world, _ = flow_lexicon.expand(clip.camera_idea)
    camera_text = camera_text.strip().rstrip(".")
    if not camera_text and not inline_world:
        # nothing was shorthand — the authored prose stands as written
        camera_text = clip.camera_idea.strip().rstrip(".")
    # a shot that was ONLY a /world token leaves the camera slot empty; the
    # world moved to the style slot — never resurrect the literal token

    if clip.model == "omni-flash":
        # The session world rides dialogue only when the whole package rolls
        # on Flow: under a mixed package (auto policy — pictures on Grok),
        # styling the spoken shots away from the Grok pictures would break
        # the scene's one-look doctrine. An inline /world is authored law
        # for its own clip either way.
        mixed = any(c.provider is CloudProvider.GROK for c in package.clips)
        world = inline_world or (
            "" if mixed else flow_lexicon.template_for(package.session_id)["world"]
        )
        omni_style = ""
        if world and world != flow_lexicon.BASELINE_WORLD:
            omni_style = flow_lexicon.worlds().get(world, {}).get("clause", "")
        return _fit(
            [
                (f"{camera_text}." if camera_text else "", 0),
                # a "back to camera" / concealment cue arms the same hard
                # lock on every dialect — the s-2e0a74644b regression class
                # doesn't care which lane the clip rides
                (_blocking_locks(clip, char_text, action, camera_text=camera_text), 0),
                identity_early,
                (continuity, 1),
                (master["anchors"], 1),
                # the whole Flow package rides omni now, picture clips
                # included — the authored action must reach the prompt, not
                # just performance and sound
                (
                    action
                    or (
                        ""
                        if clip.audio_packet.script_excerpt
                        else scene.emotional_job or "a held cinematic moment"
                    ),
                    3,
                ),
                (_setting_block(setting, scene.heading), 2),
                (look_block, 0),
                (master["lighting"], 2),
                (omni_style, 2),
                identity_late,
                voice[0],
                (_sound_clause(clip, setting, continues=bool(continuity)), 2),
                voice[1],
                ("Performance is grounded and unhurried.", 1),
                (master["physics"], 2),
                (master["ending"], 1),
                (master["constraints"], 1),
                (ANTI_ARTIFACT, 0),
            ],
            limit,
        )

    # Veo generates native audio: it gets the same explicit sound direction as
    # every other dialect — an undirected soundtrack is an invented soundtrack.
    # The session's world template fills the style slot; an inline /world on
    # the shot beats the template — authorship wins.
    return _fit(
        [
            (f"{camera_text}." if camera_text else "", 0),
            (_blocking_locks(clip, char_text, action, camera_text=camera_text), 0),
            identity_early,
            (continuity, 1),
            (master["anchors"], 1),
            (f"{action or scene.emotional_job or 'a held cinematic moment'}.", 3),
            (master["physics"], 2),
            (_setting_block(setting, scene.heading), 2),
            (look_block, 0),
            (master["lighting"], 2),
            (_flow_style_slot(package, inline_world), 1),
            voice[0],
            (_sound_clause(clip, setting, continues=bool(continuity)), 2),
            voice[1],
            identity_late,
            (master["ending"], 1),
            (master["constraints"], 1),
            (ANTI_ARTIFACT, 0),
        ],
        limit,
    )


def compile_clip_prompt(
    clip: ClipUnit, package: ProductionPackage, *, pictured_people: list[dict] | None = None
) -> PromptVersion:
    """`pictured_people` (the submit gate's list — real people, named
    characters, the cast) are described, never named, outside quoted speech,
    BEFORE the prompt is fitted, so the description costs the shrinkable
    pieces their length, never the provider limit."""
    from diony_director import capabilities

    if clip.provider is None or not clip.model:
        raise CompileError(f"clip {clip.clip_id} has no provider/model routing")
    caps = capabilities.caps_for(clip.provider.value, clip.model)
    if caps is None:
        raise CompileError(
            f"clip {clip.clip_id} routes to unknown model {clip.model!r} on {clip.provider.value}"
        )
    # A voice lock no longer constrains routing: the narrator honors it in
    # post (Blueprint 28), so it is expressible on every route.
    if not capabilities.duration_legal(caps, clip.duration_s):
        raise CompileError(
            f"clip {clip.clip_id} books {clip.duration_s}s, which is not on the "
            f"{clip.model} duration grid"
        )
    return PromptVersion(
        version="v1",
        prompt=_base_prompt(clip, package, pictured_people=pictured_people),
    )


def _described(
    pieces: list[tuple[str, int]],
    package: ProductionPackage,
    pictured_people: list[dict] | None = None,
) -> list[tuple[str, int]]:
    """The likeness guard, piece by piece and before the fit: whoever the
    master derivation recognized stays described, never named, whatever a
    later note, authored shot, or character block carried in (Blueprint 27
    §3a); beside a picture, every named character too, outside quoted
    speech (the submit gate's rule, applied where the length still has
    somewhere to go)."""
    master = package.master_prompt
    real = list(master.real_people or []) if master is not None else []
    pictured = list(pictured_people or [])
    if not real and not pictured:
        return pieces
    out: list[tuple[str, int]] = []
    for text, rank in pieces:
        if text and real:
            text, _ = likeness.scrub(text, real)
        if text and pictured:
            text, _ = likeness.scrub_pictured(text, pictured)
        out.append((text, rank))
    return out


def _guarded(
    prompt: str, package: ProductionPackage, pictured_people: list[dict] | None = None
) -> str:
    return _described([(prompt, 0)], package, pictured_people)[0][0]


def revise_prompt(
    clip: ClipUnit,
    package: ProductionPackage,
    *,
    note: str,
    pictured_people: list[dict] | None = None,
) -> PromptVersion:
    # A spoken clip (Blueprint 31) never gets the "remove all speech" audio
    # fix — its track IS the voice; the fix is the line, clearly, once.
    key = note
    if speaks_in_clip(clip) and (note == "fix-audio" or note.startswith("fix-audio-")):
        key = "fix-line"
    directive = _guarded(
        f"Revision: {REVISION_DIRECTIVES.get(key, f'Address precisely: {note}.')}",
        package,
        pictured_people,
    )
    limit = prompt_limit(clip.provider)
    # The base is fitted against what the directive leaves: priorities hold
    # (camera, identity, look, the guard) — never the whole base capped to
    # 400 chars because a 90-char rider pushed it over.
    base = _base_prompt(
        clip,
        package,
        pictured_people=pictured_people,
        limit=max(limit // 2, limit - len(directive) - 1),
    )
    n = len(clip.prompt_versions) + 1
    return PromptVersion(
        version=f"v{n}-{note}",
        prompt=_fit_prompt([(base, 1), (directive, 0)], limit),
        note=note,
    )


_SENTENCE_BREAK = re.compile(r'(?<=[.!?])\s+(?=[A-Z"“(@])')


def refit_text(text: str, limit: int) -> str:
    """The net for a finished prompt with no pieces left to weigh (a prompt
    persisted by an earlier build, a still's prompt): the opening sentence
    (the camera) and the closing one (the guard) hold; the sentences between
    give way from the end, one at a time, gently first."""
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    parts = [s for s in _SENTENCE_BREAK.split(text) if s]
    if len(parts) < 3:
        return _shrink(text, limit)
    pieces = [(parts[0], 0)]
    pieces += [(s, 1 + index) for index, s in enumerate(parts[1:-1])]
    pieces.append((parts[-1], 0))
    return _fit_prompt(pieces, limit)


def _live_plate_asset(clip: ClipUnit, package: ProductionPackage) -> str:
    """The setting's plate as it exists NOW — locks late-bind at submit, so a
    plate registered after the split still reaches every clip that needs it."""
    plate_asset = str(clip.continuity.setting_plate.get("asset_id") or "")
    if plate_asset:
        return plate_asset
    setting_id = str(clip.continuity.setting_plate.get("setting_id") or "")
    if setting_id:
        try:
            return package.setting(setting_id).plate_asset_id
        except KeyError:
            pass
    return ""


def _locked_identity_images(chars: list[CharacterProfile], budget: int) -> list[str]:
    """Blueprint 32: one best still per character first (the board packs
    four views into one slot), then second stills while the budget allows."""
    from diony_director import cast as cast_mod

    packs = [cast_mod.identity_refs(c) for c in chars]
    images: list[str] = []
    for depth in range(max((len(pack) for pack in packs), default=0)):
        for pack in packs:
            if depth < len(pack) and pack[depth] not in images and len(images) < budget:
                images.append(pack[depth])
    return images


def reference_roles(
    refs: dict, clip: ClipUnit, package: ProductionPackage, *, frames: list[str] | None = None
) -> dict:
    """Blueprint 32: label every reference the op will send with what it IS,
    so the adapters can tag them the way the providers document. Classified
    by path, so late binding can reorder or add references freely. Only a
    Locked package earns roles — Standard payloads stay byte-identical."""
    if getattr(package, "cast_lock", "standard") != "locked":
        refs.pop("reference_roles", None)
        refs.pop("character_names", None)
        return refs
    from diony_director import cast as cast_mod

    chars = _characters_in(clip, package)
    by_still: dict[str, str] = {}
    for char in chars:
        for still in cast_mod.identity_refs(char):
            by_still.setdefault(still, char.name)
    plate = _live_plate_asset(clip, package)
    look = look_for(package)
    anchors = set(look.anchor_assets) if look is not None else set()
    from diony_director import session as _session

    uploads = set(_session.all_upload_paths(package.session_id))
    frame_set = set(frames or [])
    roles: list[dict[str, str]] = []
    for ref in refs.get("references") or []:
        if ref in by_still:
            roles.append(cast_mod.role("character", by_still[ref]))
        elif plate and ref == plate:
            roles.append(cast_mod.role("plate"))
        elif ref in frame_set or "/frames/" in str(ref):
            roles.append(cast_mod.role("bridge"))
        elif ref in anchors:
            roles.append(cast_mod.role("look"))
        elif ref in uploads:
            roles.append(cast_mod.role("upload"))
        else:
            roles.append(cast_mod.role("look"))
    if roles:
        refs["reference_roles"] = roles
    else:
        refs.pop("reference_roles", None)
    if refs.get("characters"):
        names = [c.name for c in chars if c.flow_character_ref]
        refs["character_names"] = names[: len(refs["characters"])]
    else:
        refs.pop("character_names", None)
    return refs


def provider_artifact_refs(clip: ClipUnit, package: ProductionPackage) -> dict:
    refs: dict = {}
    chars = _characters_in(clip, package)
    locked = getattr(package, "cast_lock", "standard") == "locked"
    if clip.provider is CloudProvider.GROK:
        images = (
            _locked_identity_images(chars, 3)
            if locked
            else [asset for c in chars for asset in c.grok_ref_pack]
        )
        plate_asset = _live_plate_asset(clip, package)
        if clip.continuity.lock_recipe == "still" and plate_asset:
            if chars and images:
                # A scene that opens WITH people cannot open FROM the empty
                # plate — the cast would pop into existence mid-shot. The
                # plate leads the reference pack instead: the model composes
                # the figures INTO the master room from frame one.
                refs["references"] = ([plate_asset] + images)[:3]
            else:
                refs["start_image"] = plate_asset
        elif images or plate_asset:
            # docs.x.ai: ≤3 reference images. The setting plate always rides —
            # identity sheets alone let the model invent a brand-new room
            # (the s-1b80548c4d re-anchor reset).
            pack = images[:2] if plate_asset else images[:3]
            if plate_asset and plate_asset not in pack:
                pack = pack + [plate_asset]
            refs["references"] = pack[:3]
        if clip.clip_class == "silent-picture" and not speaks_in_clip(clip):
            refs["generate_audio"] = False
        return reference_roles(refs, clip, package)

    from diony_director import capabilities

    caps = capabilities.caps_for(clip.provider.value, clip.model)
    max_refs = caps.max_reference_images if caps else 7
    max_chars = caps.max_characters if caps else 7
    character_refs = [c.flow_character_ref for c in chars if c.flow_character_ref]
    if character_refs and max_chars:
        refs["characters"] = character_refs[:max_chars]
    # Provider voices are never registered: the clip carries no spoken words
    # (Blueprint 28) — a voice lock binds the post narrator instead.
    plate_asset = _live_plate_asset(clip, package)
    if plate_asset:
        # Frames are exclusive with refs/characters/voices on Flow: a
        # still-recipe opener may only lead FROM the plate frame when the
        # clip carries no identity refs. With cast present the plate rides
        # as a reference instead — the model composes the figures into the
        # master room, the same doctrine as the Grok still-recipe branch —
        # so the compiler never hands the capability gate an illegal combo.
        has_identity = bool(refs.get("characters"))
        if clip.continuity.lock_recipe == "still" and not has_identity:
            refs["start_image"] = plate_asset
        elif max_refs:
            refs.setdefault("references", []).append(plate_asset)
        # A model with no reference lane (veo-3.1-quality) carries the look
        # through i2v chaining and the prompt — never an illegal payload.
    if locked and max_refs and not refs.get("characters"):
        # Blueprint 32: no Flow character object yet (the sheet is still
        # landing, or the lane has no character budget) — the identity
        # stills ride as references beside the plate, never nothing.
        budget = max(0, max_refs - len(refs.get("references") or []))
        for still in _locked_identity_images(chars, budget):
            if still not in (refs.get("references") or []):
                refs.setdefault("references", []).append(still)
    return reference_roles(refs, clip, package)
