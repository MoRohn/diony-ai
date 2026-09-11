"""Blueprint 31 §10 — the voice cast: one castable voice per character.

Under the Dialogue and Mix styles the provider voices the character inside
the clip. A prompt that says only "a natural, clear voice" leaves the
provider to invent one — and it invents a different one per clip, and the
same one for everyone: a woman read by a man, two people in one voice, a
character whose timbre changes at every cut. The fix is the same one the
identity card made for faces (Blueprint 32): a VOICE CARD written once at
draft time and carried verbatim on every spoken prompt the character has.

The card is `VoiceProfile.gender / age_band / timbre / accent` on the
character's profile. The screenwriter brain writes it when it can
(`voice: {gender, age, timbre, accent}` per character); the deterministic
derivation below reads the casting notes otherwise; and the cast as a whole
is made DISTINCT — no two characters share a gender-and-timbre pair — so a
two-hander never collapses into one voice.

The same card casts the post narrator when a character's line falls to the
narrator rail under Mix (a voice-over, or a mute take the speech guard
rescues): `roster_notes()` turns the card into the read `narration.cast_voice`
scores, so the rescue is a woman's line in a woman's voice from the roster,
never the film's narrator whoever they are.
"""

from __future__ import annotations

import re
from typing import Any

from diony_contracts.production import CharacterProfile, VoiceProfile

# --- reading the casting notes ---------------------------------------------

FEMALE_TELLS = re.compile(
    r"\b(she|her|hers|herself|woman|women|female|girl|mother|mom|daughter|sister|aunt|"
    r"grandmother|queen|princess|actress|lady|widow|waitress|nun|bride|wife|"
    r"mistress)\b",
    re.I,
)
MALE_TELLS = re.compile(
    r"\b(he|him|his|himself|man|men|male|boy|father|dad|son|brother|uncle|grandfather|"
    r"king|prince|actor|gentleman|widower|waiter|monk|groom|husband|guy|lad)\b",
    re.I,
)

_AGE_WORDS = {
    "child": "a child",
    "kid": "a child",
    "toddler": "a small child",
    "teen": "teens",
    "teenage": "teens",
    "teenager": "teens",
    "adolescent": "teens",
    "young": "twenties",
    "youthful": "twenties",
    "middle-aged": "forties",
    "middle aged": "forties",
    "elderly": "elderly",
    "aged": "elderly",
    "ancient": "elderly",
    "grandmother": "elderly",
    "grandfather": "elderly",
    "retired": "sixties",
    "veteran": "fifties",
    "weathered": "fifties",
    "grizzled": "fifties",
    "greying": "fifties",
    "graying": "fifties",
    "silver-haired": "sixties",
    "white-haired": "seventies",
}
_DECADES = ("twenties", "thirties", "forties", "fifties", "sixties", "seventies", "eighties")
_DECADE_RE = re.compile(
    r"\b(?:early|mid|late)?[- ]?(twenties|thirties|forties|fifties|sixties|seventies|eighties)\b",
    re.I,
)
# "34, lean" / "34 years old" / "a 9-year-old" — never "35mm", "2×2", or
# "5 o'clock shadow": a bare number is an age only in the adult band and only
# when nothing letter-like follows it; a child's age must say "year"
_EXPLICIT_AGE_RE = re.compile(r"(?<![\w.])(\d{1,3})\s*-?\s*(?:years?|yrs?|yo)\b", re.I)
_NUMBER_RE = re.compile(r"(?<![\w.])(\d{2})(?![\w%])")

# The timbre palette. Every phrase is built from the narrator rosters' own
# casting tags (narration.DEFAULT_ROSTER / GEMINI_DEFAULT_ROSTER) so a card
# cast here also casts a matching roster voice when the line falls to the
# post narrator — the same read on both instruments.
_PALETTE: dict[str, tuple[str, ...]] = {
    "female": (
        "warm and bright, quick and friendly",
        "soft and soothing, calm and intimate",
        "poised and refined, measured and even",
        "light and youthful, gentle and quick",
        "mature and grave, solemn and slow",
    ),
    "male": (
        "calm and grounded, steady mid-range",
        "deep and solemn, slow and grave",
        "smooth and measured, low and even",
        "dramatic and intense, energetic and firm",
        "light and youthful, quick and bright",
    ),
    "": (
        "clear and even, natural mid-range",
        "warm and low, unhurried and steady",
        "light and bright, quick and friendly",
        "dry and measured, calm and precise",
        "deep and slow, grave and solemn",
    ),
}

# What the writing says about how a person sounds → the palette read that
# fits it. Matched against the role, the casting notes, and the identity
# anchor; the first hint wins.
_HINTS: tuple[tuple[str, dict[str, str]], ...] = (
    (
        r"\b(commander|captain|general|colonel|sergeant|boss|chief|leader|officer|judge|"
        r"warden|foreman|patriarch|matriarch|stern|gruff|gravel|gravelly|hard-bitten|"
        r"grizzled|weathered|authority|authoritative|imposing)\b",
        {
            "female": "poised and firm, measured and steady",
            "male": "deep and authoritative, grave and slow",
            "": "deep and firm, measured and slow",
        },
    ),
    (
        r"\b(detective|analyst|doctor|surgeon|scientist|professor|lawyer|accountant|"
        r"clinical|precise|calm|measured|poised|refined|composed|cool|deliberate)\b",
        {
            "female": "poised and refined, measured and even",
            "male": "smooth and measured, low and even",
            "": "dry and measured, calm and precise",
        },
    ),
    (
        r"\b(warm|kind|kindly|friendly|cheerful|cheery|playful|upbeat|bubbly|sunny|"
        r"charming|smile|smiling|laughs?|jovial|generous)\b",
        {
            "female": "warm and bright, quick and friendly",
            "male": "calm and grounded, easy and confident",
            "": "warm and low, unhurried and steady",
        },
    ),
    (
        r"\b(soft|softly|gentle|quiet|shy|timid|tender|dreamy|nervous|frail|wistful|"
        r"whisper|hushed|meek)\b",
        {
            "female": "soft and soothing, calm and intimate",
            "male": "smooth and soft, measured and low",
            "": "soft and low, gentle and calm",
        },
    ),
    (
        r"\b(dramatic|intense|fiery|angry|furious|loud|wild|volatile|manic|frantic|"
        r"explosive|hot-headed|brash)\b",
        {
            "female": "bright and energetic, upbeat and intense",
            "male": "dramatic and intense, energetic and firm",
            "": "bright and intense, energetic and quick",
        },
    ),
    (
        r"\b(teen|teenage|teenager|kid|child|boy|girl|young|youthful|eager|wiry|quick|"
        r"student|rookie|apprentice|intern)\b",
        {
            "female": "light and youthful, gentle and quick",
            "male": "light and youthful, quick and bright",
            "": "light and bright, quick and friendly",
        },
    ),
    (
        r"\b(old|elderly|aged|ancient|grandmother|grandfather|retired|veteran|dying|"
        r"tired|weary|worn)\b",
        {
            "female": "mature and grave, solemn and slow",
            "male": "deep and solemn, slow and grave",
            "": "deep and slow, grave and solemn",
        },
    ),
)

_PITCH_SPREAD = ("pitched a little higher", "pitched a little lower", "brighter", "darker")


def _clean(text: Any) -> str:
    return " ".join(str(text or "").split()).strip()


def character_text(char: CharacterProfile) -> str:
    """Everything the writing says about the person, for the reads below:
    the role, the casting notes, and the identity card's anchor and
    features. Never the wardrobe — a 'boyfriend jacket' is not a man."""
    bits = [_clean(char.role), _clean(char.age_body_notes)]
    identity = getattr(char, "identity", None)
    if identity is not None:
        bits.append(_clean(identity.anchor))
        bits.extend(_clean(f) for f in identity.features)
    return " ".join(b for b in bits if b)


def read_gender(text: str) -> str:
    """'female' | 'male' | '' — a read the writing makes plainly, or nothing.
    Both tells present (a woman described against her brother) is no read:
    guessing wrong misgenders the character in every clip."""
    female = bool(FEMALE_TELLS.search(text or ""))
    male = bool(MALE_TELLS.search(text or ""))
    if female and not male:
        return "female"
    if male and not female:
        return "male"
    return ""


def read_age_band(text: str) -> str:
    """The age band the writing names — a number ('34, lean'), a decade
    ('in her fifties'), or a word ('elderly', 'teenage') — else ''."""
    lowered = (text or "").lower()
    decade = _DECADE_RE.search(lowered)
    if decade:
        return decade.group(1).lower()

    def _band(n: int) -> str:
        if n <= 12:
            return "a child"
        if n <= 19:
            return "teens"
        if n <= 89:
            return _DECADES[min((n - 20) // 10, len(_DECADES) - 1)]
        return "very old"

    explicit = _EXPLICIT_AGE_RE.search(lowered)
    if explicit and 1 <= int(explicit.group(1)) <= 110:
        return _band(int(explicit.group(1)))
    for match in _NUMBER_RE.finditer(lowered):
        n = int(match.group(1))
        if 18 <= n <= 99:
            return _band(n)
    for word, band in _AGE_WORDS.items():
        if re.search(rf"\b{re.escape(word)}\b", lowered):
            return band
    return ""


def read_timbre(text: str, gender: str) -> str:
    """The palette read the writing's own words point at, or '' when the
    writing says nothing about how the person sounds."""
    lowered = (text or "").lower()
    for pattern, reads in _HINTS:
        if re.search(pattern, lowered):
            return reads.get(gender, reads[""])
    return ""


# --- the cast ------------------------------------------------------------------


def derive_voice(char: CharacterProfile, *, index: int = 0) -> VoiceProfile:
    """A card written deterministically from the character's notes. Never a
    brain call — the same notes always yield the same card. `index` is the
    character's position in the cast: with no timbre hint in the writing,
    it walks the palette so a cast of strangers still sounds like several
    people (the pairwise distinctness is enforced by `cast_voices`)."""
    text = character_text(char)
    gender = read_gender(text)
    age = read_age_band(text)
    timbre = read_timbre(text, gender)
    if not timbre:
        palette = _PALETTE[gender]
        timbre = palette[index % len(palette)]
    base = char.voice
    return VoiceProfile(
        voice_id=(base.voice_id if base is not None else f"vox-{char.char_id}"),
        engine=(base.engine if base is not None else "omni_preset"),
        engine_ref=(base.engine_ref if base is not None else ""),
        sample_asset_id=(base.sample_asset_id if base is not None else ""),
        accent=(base.accent if base is not None else ""),
        pace_wpm=(base.pace_wpm if base is not None else 150),
        pitch_band=(base.pitch_band if base is not None else ""),
        mic_distance=(base.mic_distance if base is not None else "close, dry"),
        approved=(base.approved if base is not None else False),
        gender=gender,
        age_band=age,
        timbre=timbre,
        source="derived",
    )


def parse_brain_voice(raw: Any, char: CharacterProfile) -> VoiceProfile | None:
    """The screenwriter brain's `voice` object for a character — {gender,
    age, timbre, accent} — as a card, or None when it wrote nothing usable.
    A gender the brain names outside female/male is dropped, never guessed."""
    if not isinstance(raw, dict):
        return None
    gender = _clean(raw.get("gender")).lower()
    if gender in {"woman", "f", "feminine"}:
        gender = "female"
    elif gender in {"man", "m", "masculine"}:
        gender = "male"
    if gender not in {"female", "male"}:
        gender = ""
    age = _clean(raw.get("age") or raw.get("age_band"))
    if age and age.isdigit():
        age = read_age_band(age)
    timbre = _clean(raw.get("timbre") or raw.get("quality") or raw.get("description"))
    accent = _clean(raw.get("accent"))
    if not (gender or age or timbre):
        return None
    base = char.voice
    return VoiceProfile(
        voice_id=(base.voice_id if base is not None else f"vox-{char.char_id}"),
        engine=(base.engine if base is not None else "omni_preset"),
        engine_ref=(base.engine_ref if base is not None else ""),
        gender=gender,
        age_band=age[:60],
        timbre=timbre[:120],
        accent=accent[:60],
        source="brain",
    )


def cast_voices(characters: list[CharacterProfile]) -> list[CharacterProfile]:
    """Give every character a voice card and make the cast distinct.

    A card the brain or the user wrote stands (its read is authored law);
    a character with no read gets the derivation. Then no two characters may
    share a gender-and-timbre pair: the later one walks the palette for its
    gender to the first unused read, and past the palette takes a pitch
    spread — so a four-woman scene is four voices, not one. Idempotent: a
    cast already distinct is returned byte-identical."""
    taken: set[tuple[str, str]] = set()
    for index, char in enumerate(characters):
        voice = char.voice
        if voice is None or not voice.cast or voice.source == "derived":
            voice = derive_voice(char, index=index)
        key = (voice.gender, voice.timbre.lower())
        if key in taken and voice.source == "derived":
            palette = _PALETTE[voice.gender]
            spare = [t for t in palette if (voice.gender, t.lower()) not in taken]
            if spare:
                voice.timbre = spare[0]
            else:
                n = sum(1 for g, _t in taken if g == voice.gender)
                voice.timbre = f"{voice.timbre}, {_PITCH_SPREAD[n % len(_PITCH_SPREAD)]}"
            key = (voice.gender, voice.timbre.lower())
        elif key in taken:
            # an authored read that collides keeps its words — the film's
            # author cast it — but the prompt still says the voices differ
            n = sum(1 for g, _t in taken if g == voice.gender)
            voice.timbre = f"{voice.timbre}, {_PITCH_SPREAD[n % len(_PITCH_SPREAD)]}"
            key = (voice.gender, voice.timbre.lower())
        taken.add(key)
        char.voice = voice
    return characters


def ensure_cast(characters: list[CharacterProfile]) -> list[CharacterProfile]:
    """Cast only when a character has no read yet (a package drafted before
    voice cards existed); a cast already written is never rewritten."""
    if any(c.voice is None or not c.voice.cast for c in characters):
        return cast_voices(characters)
    return characters


# --- what the card becomes ----------------------------------------------------


def voice_card(char: CharacterProfile) -> str:
    """The verbatim VOICE clause for this character's spoken prompts, or ''
    when the character has no card (a pre-card package: the prompt then
    reads exactly as before)."""
    if char.voice is None or not char.voice.cast:
        return ""
    return char.voice.card(char.name)


def voice_line(char: CharacterProfile) -> str:
    """The console's one-line summary: `female · thirties · warm and bright`."""
    if char.voice is None or not char.voice.cast:
        return ""
    v = char.voice
    bits = [v.gender or "voice", v.age_band, v.timbre]
    if v.accent:
        bits.append(f"{v.accent} accent")
    return " · ".join(b for b in bits if b)


def roster_notes(char: CharacterProfile) -> str:
    """The card as the read `narration.cast_voice` scores against the
    narrator rosters: the gender word (its tells), the age, and the timbre
    words (its tags), plus the casting notes."""
    if char.voice is None or not char.voice.cast:
        return _clean(char.age_body_notes)
    v = char.voice
    return " ".join(b for b in (v.gender, v.age_band, v.timbre, _clean(char.age_body_notes)) if b)


def character_for(speaker: str, characters: list[CharacterProfile]) -> CharacterProfile | None:
    """The cast member a script speaker names (case-insensitive), or None —
    never a guess: a line whose speaker is unknown carries no card."""
    want = _clean(speaker).upper()
    if not want:
        return None
    for char in characters:
        if _clean(char.name).upper() == want:
            return char
    return None


__all__ = [
    "FEMALE_TELLS",
    "MALE_TELLS",
    "cast_voices",
    "character_for",
    "character_text",
    "derive_voice",
    "ensure_cast",
    "parse_brain_voice",
    "read_age_band",
    "read_gender",
    "read_timbre",
    "roster_notes",
    "voice_card",
    "voice_line",
]
