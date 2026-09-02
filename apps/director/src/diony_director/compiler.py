"""Provider-aware prompt compilation with bible injection and lossless locks.

Three dialects (Grok motion+sound, Veo cinema prose, Omni dialogue+performance),
each drawing wardrobe, forbidden drift, and setting grammar from the bibles. A
lock that cannot be expressed on the selected provider fails compilation loudly
— it is never dropped silently.
"""

from __future__ import annotations

from diony_contracts.director import CloudProvider
from diony_contracts.production import (
    CharacterProfile,
    ClipUnit,
    ProductionPackage,
    PromptVersion,
    SettingProfile,
)

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
    the booked clip length. A voice-over clip needs no performance at all."""
    excerpt = clip.audio_packet.script_excerpt
    if not excerpt or not clip.audio_packet.on_camera:
        return ""
    entry = excerpt[0]
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


def _blocking_locks(
    clip: ClipUnit, char_text: str, action: str, *, camera_text: str = ""
) -> str:
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


def _base_prompt(clip: ClipUnit, package: ProductionPackage) -> str:
    setting = package.setting(str(clip.continuity.setting_plate["setting_id"]))
    scene = package.script.scene(clip.scene_id)
    chars = _characters_in(clip, package)
    char_text = " ".join(_character_block(c, scene.story_time) for c in chars)
    action = _action_text(clip, package)
    continuity = CONTINUITY_CLAUSE if _continues_scene(clip, package) else ""
    master = _master_pieces(clip, package)
    look_block = _look_block(package)

    limit = prompt_limit(clip.provider)
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
        return _fit_prompt(
            [
                (f"{camera_text}." if camera_text else "", 0),
                (_blocking_locks(clip, char_text, action, camera_text=camera_text), 0),
                (continuity, 1),
                (master["anchors"], 1),
                (
                    action
                    or "The action continues truthfully; no reset, no new business.",
                    3,
                ),
                (master["physics"], 2),
                (f"{grok_lexicon.sentence(atmosphere)}." if atmosphere else "", 2),
                (_setting_block(setting, scene.heading), 2),
                (look_block, 0),
                (master["lighting"], 2),
                (_delivery_clause(clip), 0),
                (_realism_clause(package), 1),
                (_sound_clause(clip, setting, continues=bool(continuity)), 2),
                (NO_SPEECH, 0),
                (char_text, 3),
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
        return _fit_prompt(
            [
                (f"{camera_text}." if camera_text else "", 0),
                # a "back to camera" / concealment cue arms the same hard
                # lock on every dialect — the s-2e0a74644b regression class
                # doesn't care which lane the clip rides
                (_blocking_locks(clip, char_text, action, camera_text=camera_text), 0),
                (continuity, 1),
                (master["anchors"], 1),
                (_setting_block(setting, scene.heading), 2),
                (look_block, 0),
                (master["lighting"], 2),
                (omni_style, 2),
                (char_text, 3),
                (_delivery_clause(clip), 0),
                (_sound_clause(clip, setting, continues=bool(continuity)), 2),
                (NO_SPEECH, 0),
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
    return _fit_prompt(
        [
            (f"{camera_text}." if camera_text else "", 0),
            (_blocking_locks(clip, char_text, action, camera_text=camera_text), 0),
            (continuity, 1),
            (master["anchors"], 1),
            (f"{action or scene.emotional_job or 'a held cinematic moment'}.", 3),
            (master["physics"], 2),
            (_setting_block(setting, scene.heading), 2),
            (look_block, 0),
            (master["lighting"], 2),
            (_flow_style_slot(package, inline_world), 1),
            (_delivery_clause(clip), 0),
            (_sound_clause(clip, setting, continues=bool(continuity)), 2),
            (NO_SPEECH, 0),
            (char_text, 3),
            (master["ending"], 1),
            (master["constraints"], 1),
            (ANTI_ARTIFACT, 0),
        ],
        limit,
    )


def compile_clip_prompt(clip: ClipUnit, package: ProductionPackage) -> PromptVersion:
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
    return PromptVersion(version="v1", prompt=_base_prompt(clip, package))


def revise_prompt(clip: ClipUnit, package: ProductionPackage, *, note: str) -> PromptVersion:
    directive = f"Revision: {REVISION_DIRECTIVES.get(note, f'Address precisely: {note}.')}"
    limit = prompt_limit(clip.provider)
    base = _base_prompt(clip, package)
    n = len(clip.prompt_versions) + 1
    return PromptVersion(
        version=f"v{n}-{note}",
        prompt=_fit_prompt([(base, 1), (directive, 0)], limit),
        note=note,
    )


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


def provider_artifact_refs(clip: ClipUnit, package: ProductionPackage) -> dict:
    refs: dict = {}
    chars = _characters_in(clip, package)
    if clip.provider is CloudProvider.GROK:
        images = [asset for c in chars for asset in c.grok_ref_pack]
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
        if clip.clip_class == "silent-picture":
            refs["generate_audio"] = False
        return refs

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
    return refs
