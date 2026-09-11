"""Blueprint 28 — post-assembly narration: the voice track is a POST instrument.

Scripted and narrated lines are never generated inside a clip. The provider
gives picture and ambience only; after `assembly.assemble()` cuts the film,
this module records the authored words against the real cut timeline —
one synthesized cue per clip that carries a line, placed at that clip's
actual timeline start — and mixes them into the master with sidechain
ducking on the location track and a final loudness lock. One voice, exact
words, placement measured off the finished cut instead of hoped-for inside
a provider's own pacing.

Synthesis backends, resolved by ``backend()``:

- ``grok`` (the default whenever XAI_API_KEY is set): the Grok TTS API on
  the same xAI account the picture lane already uses — one consistent voice
  per production, no local model. Contract VERIFIED LIVE 2026-09-02 against
  docs.x.ai and this account: ``POST {DIONY_XAI_BASE}/v1/tts`` with
  ``{text, voice_id, language, output_format:{codec, sample_rate}}`` returns
  raw audio bytes (WAV at 48 kHz requested here — the mix's native rate);
  ``GET /v1/tts/voices`` lists the roster. DIONY_GROK_VOICE_PATH overrides
  the path, DIONY_GROK_VOICE pins one voice, DIONY_GROK_VOICE_LANGUAGE sets
  the language (default en); a clip's voice lock overrides per cue.
- ``cli``: an on-host command template, the same contract the audiogen
  generators use:

    DIONY_NARRATOR_CLI  e.g. ".../python /opt/narrator/say.py --text {text} --voice {voice} --output {output}"

  Placeholders: {text} (the verbatim line), {output} (the wav to write),
  {voice} (the clip's voice lock, may be empty), {speaker}, {direction}.

DIONY_NARRATOR_BACKEND forces a choice; unset, an authored CLI template wins
over the key-derived grok default. The adapter verifies every output decodes
and carries real length; a cue that fails is a loud failure, never a
silently mute film.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from diony_director.assembly import LOUDNESS, ROOM_BED_AMPLITUDE, _ffmpeg, _probe

NARRATOR_ENV = "DIONY_NARRATOR_CLI"
BACKEND_ENV = "DIONY_NARRATOR_BACKEND"
GROK_VOICE_PATH_ENV = "DIONY_GROK_VOICE_PATH"
GROK_VOICE_ENV = "DIONY_GROK_VOICE"
GROK_VOICE_PATH_DEFAULT = "/v1/tts"
GROK_VOICE_DEFAULT = "eve"  # the API's own default
GROK_VOICE_LANGUAGE_ENV = "DIONY_GROK_VOICE_LANGUAGE"
GROK_VOICE_LANGUAGE_DEFAULT = "en"
GROK_VOICE_ROSTER_ENV = "DIONY_GROK_VOICE_ROSTER"
GROK_CALL_TIMEOUT_S = 120.0
GEMINI_VOICE_ENV = "DIONY_GEMINI_VOICE"
GEMINI_VOICE_ROSTER_ENV = "DIONY_GEMINI_VOICE_ROSTER"
GEMINI_TTS_MODEL_ENV = "DIONY_GEMINI_TTS_MODEL"
# Google names gemini-3.1-flash-tts-preview as the successor of
# gemini-2.5-flash-preview-tts; a retirement rejection is followed the same
# way the brain follows one (brain.follow_retirement)
GEMINI_TTS_MODEL_DEFAULT = "gemini-3.1-flash-tts-preview"
GEMINI_VOICE_PREFIX = "gemini:"
GEMINI_CALL_TIMEOUT_S = 120.0

# The casting roster: a curated slice of the live Grok TTS voice list
# (voice_id and gender VERIFIED against GET /v1/tts/voices on this account,
# 2026-09-02; the full list holds 28). The tone tags are Diony's own casting
# reads — the material cast_voice() matches against the master prompt's tone
# and the speaking character. DIONY_GROK_VOICE_ROSTER overrides with JSON
# {voice_id: [tags]} without a code change.
DEFAULT_ROSTER: dict[str, list[str]] = {
    "ara": ["female", "warm", "bright", "upbeat", "playful", "energetic", "friendly"],
    "eve": ["female", "soothing", "calm", "intimate", "elegant", "soft", "romantic"],
    "luna": ["female", "gentle", "dreamy", "night", "soft", "wistful"],
    "celeste": ["female", "poised", "refined", "serene", "documentary", "measured"],
    "rex": ["male", "calm", "confident", "grounded", "documentary", "realistic", "steady"],
    "sal": ["male", "smooth", "measured", "noir", "tense", "mysterious", "narrator"],
    "leo": ["male", "authoritative", "epic", "dramatic", "intense", "energetic"],
    "atlas": ["male", "deep", "solemn", "grave", "monumental", "slow"],
}

# The Google narrator: Gemini's prebuilt speech voices (names from the Gemini
# API speech-generation docs, 2026-09-10). Casting tags are Diony's own reads
# of each voice — the docs give one word per voice ("bright", "firm",
# "breezy") and no gender; the gender reads here are Diony's, for the
# character-notes match, and DIONY_GEMINI_VOICE_ROSTER overrides the lot
# with JSON {"gemini:Name": ["tag", ...]}.
GEMINI_DEFAULT_ROSTER: dict[str, list[str]] = {
    "gemini:Kore": ["female", "firm", "confident", "documentary", "measured", "narrator"],
    "gemini:Aoede": ["female", "breezy", "bright", "friendly", "upbeat", "playful"],
    "gemini:Leda": ["female", "youthful", "gentle", "soft", "dreamy", "romantic"],
    "gemini:Achernar": ["female", "soft", "soothing", "calm", "intimate", "elegant"],
    "gemini:Sulafat": ["female", "warm", "serene", "poised", "refined"],
    "gemini:Gacrux": ["female", "mature", "grave", "solemn", "wistful"],
    "gemini:Charon": ["male", "informative", "calm", "grounded", "documentary", "steady"],
    "gemini:Puck": ["male", "upbeat", "energetic", "playful", "bright"],
    "gemini:Fenrir": ["male", "excitable", "dramatic", "intense", "epic", "energetic"],
    "gemini:Orus": ["male", "firm", "authoritative", "confident", "realistic"],
    "gemini:Algieba": ["male", "smooth", "measured", "noir", "mysterious", "tense", "narrator"],
    "gemini:Schedar": ["male", "even", "deep", "slow", "monumental"],
}

_FEMALE_TELLS = re.compile(
    r"\b(she|her|hers|woman|women|female|girl|mother|sister|queen|actress|lady)\b"
)
_MALE_TELLS = re.compile(r"\b(he|him|his|man|men|male|boy|father|brother|king|actor|gentleman)\b")

# Performed direction (Blueprint 28): the authored parenthetical becomes
# Grok speech tags — [tag] inline before the words, <tag>…</tag> wrapping
# them — so the delivery is ACTED, not just cast. Only tags confirmed in the
# live docs are mapped; an unmapped direction still shapes the casting and
# is otherwise left to the voice. The authored words themselves are never
# altered — tags ride around them, byte-identical. DIONY_NARRATOR_TAGS
# extends the map with JSON {"keyword": "[tag]" | "<tag>"} without a code
# change.
NARRATOR_TAGS_ENV = "DIONY_NARRATOR_TAGS"
DIRECTION_TAGS: dict[str, str] = {
    "whisper": "<whisper>",
    "whispering": "<whisper>",
    "whispered": "<whisper>",
    "hushed": "<whisper>",
    "under his breath": "<whisper>",
    "under her breath": "<whisper>",
    "under their breath": "<whisper>",
    "sotto voce": "<whisper>",
    "a beat": "[pause]",
    "beat": "[pause]",
    "after a pause": "[pause]",
    "pausing": "[pause]",
    "a long pause": "[long-pause]",
    "laughing": "[laugh]",
    "with a laugh": "[laugh]",
    "chuckling": "[laugh]",
    "sighing": "[sigh]",
    "with a sigh": "[sigh]",
    "wearily": "[sigh]",
}


def _direction_tags() -> dict[str, str]:
    raw = os.environ.get(NARRATOR_TAGS_ENV, "").strip()
    if not raw:
        return DIRECTION_TAGS
    try:
        extra = {
            str(k).strip().lower(): str(v).strip()
            for k, v in json.loads(raw).items()
            if str(v).strip().startswith(("[", "<"))
        }
    except (ValueError, AttributeError) as exc:
        raise NarrationFailed(
            f"{NARRATOR_TAGS_ENV} is set but is not a JSON object of "
            '{"keyword": "[tag]" | "<tag>"} — fix it or unset it'
        ) from exc
    return {**DIRECTION_TAGS, **extra}


def perform_line(line: str, direction: str) -> str:
    """The line as the narrator should ACT it: verified Grok speech tags
    derived from the authored direction, wrapped around the verbatim words.

    One wrapping style at most (the first the direction names) and each
    inline expression once, leading the words — 'a beat, laughing' becomes
    '[pause] [laugh] <words>'. A direction with no mapped tag performs
    nothing here; it already shaped the casting."""
    text = line.strip()
    lowered = f" {direction.strip().lower()} "
    if not text or not lowered.strip():
        return text
    tags = _direction_tags()
    placed: list[tuple[int, str]] = []
    claimed: list[tuple[int, int]] = []
    wrap = ""
    # longest keyword first, so 'a long pause' claims its span before 'pause'
    for keyword in sorted(tags, key=len, reverse=True):
        at = lowered.find(f" {keyword}")
        if at < 0:
            at = lowered.find(f"{keyword} ") if lowered.startswith(f"{keyword} ") else -1
        if at < 0:
            continue
        span = (at, at + len(keyword) + 1)
        if any(span[0] < end and start < span[1] for start, end in claimed):
            continue
        claimed.append(span)
        tag = tags[keyword]
        if tag.startswith("<"):
            wrap = wrap or tag
        elif tag not in (t for _, t in placed):
            placed.append((at, tag))
    # expressions lead the words in the order the direction names them
    inline = [tag for _, tag in sorted(placed)]
    if wrap:
        name = wrap.strip("<>")
        text = f"{wrap}{text}</{name}>"
    if inline:
        text = " ".join(inline[:2]) + " " + text
    return text


CUE_LEAD_IN_S = 0.15  # a cue lands just after its clip's cut, never on it
# One narrator rail means ONE voice at a time: a line that overruns its clip
# must finish (plus a breath) before the next cue may start. Without this
# layout an overrun played straight into the next cue's mark — two cast
# voices talking at once, the exact defect Blueprint 28 exists to prevent.
MIN_CUE_GAP_S = 0.25
# Voice sits above the -18 LUFS clip bed; the duck keeps the location track
# out of its way (same sidechain values the Producer program mix earned).
NARRATION_LOUDNESS = "-15"
DUCK = "sidechaincompress=threshold=0.02:ratio=6:attack=25:release=350"
# A line may breathe into the next clip by its tail handle; past that the
# picture is too short for the words and the cut needs attention.
OVERRUN_TOLERANCE_S = 1.0
# The film ends when the LAST LINE ends, not when the picture's booked length
# runs out: the final frame holds (cloned) for as long as the last cue still
# speaks, plus a breath, up to this cap. Past the cap the words are the
# authored law still — the surplus is named, never silently cut.
TAIL_BREATH_S = 0.4
MAX_TAIL_HOLD_S = 30.0


class NarrationUnavailable(RuntimeError):
    """No narrator backend is configured on this host."""


class NarrationFailed(RuntimeError):
    pass


@dataclass
class NarrationCue:
    order: int
    clip_id: str
    speaker: str
    line: str
    direction: str = ""
    voice_ref: str = ""
    cast_note: str = ""
    # Blueprint 31: the clip's own voicing — "generated" here means the
    # provider was booked to voice the line and the speech guard rescued it
    voicing: str = "post"
    # the line as actually performed — the verbatim words plus any speech
    # tags the direction mapped to; set at synthesis, audited in the report
    performed: str = ""
    start_s: float = 0.0
    window_s: float = 0.0


def _roster_from_env(env: str, default: dict[str, list[str]]) -> dict[str, list[str]]:
    raw = os.environ.get(env, "").strip()
    if not raw:
        return default
    try:
        parsed = json.loads(raw)
        roster = {
            str(name): [str(t).lower() for t in tags]
            for name, tags in parsed.items()
            if isinstance(tags, (list, tuple)) and tags
        }
        if roster:
            return roster
    except (ValueError, AttributeError):
        pass
    raise NarrationFailed(
        f"{env} is set but is not a JSON object of "
        '{"Voice": ["tag", ...]} — fix it or unset it for the built-in roster'
    )


def voice_backend(voice_ref: str) -> str:
    """Which narrator a voice id belongs to: `gemini:Name` is Gemini's, any
    other roster name is Grok's; '' when nothing is named."""
    ref = str(voice_ref or "").strip()
    if not ref:
        return ""
    return "gemini" if ref.lower().startswith(GEMINI_VOICE_PREFIX) else "grok"


def _roster(which: str | None = None) -> dict[str, list[str]]:
    """The roster the active narrator (or `which`) casts from."""
    which = which or backend() or "grok"
    if which == "gemini":
        return _roster_from_env(GEMINI_VOICE_ROSTER_ENV, GEMINI_DEFAULT_ROSTER)
    return _roster_from_env(GROK_VOICE_ROSTER_ENV, DEFAULT_ROSTER)


def available_backends() -> list[str]:
    """Every narrator this desk holds a key for, the active one first."""
    from diony_director import brain as brain_mod
    from diony_director.providers import grok

    rows = []
    if grok.configured():
        rows.append("grok")
    if brain_mod.gemini_configured():
        rows.append("gemini")
    active = backend()
    if active in rows:
        rows.remove(active)
        rows.insert(0, active)
    return rows


def _all_rosters() -> dict[str, list[str]]:
    """The union of every roster a key on this desk can speak — what the
    console offers, so a Google Flow film may still pick a Grok voice when
    the xAI key is present, and a Grok film a Gemini voice."""
    merged: dict[str, list[str]] = {}
    for which in available_backends() or ["grok"]:
        merged.update(_roster(which))
    return merged


IDENTITY_REF = re.compile(r"^vox-[A-Za-z0-9_.-]+(@v\d+)?$")


def is_identity_ref(voice_ref: str) -> bool:
    """A Diony character-identity voice ref (`vox-<char>@v1`), as opposed to
    a narrator voice id a backend can speak."""
    return bool(IDENTITY_REF.match(str(voice_ref or "").strip()))


def roster_catalog() -> list[dict]:
    """The selectable narrator voices for the Director console — every roster
    a key on this desk can speak: key = voice_id, label, backend, and the
    casting tags. Grok names stay bare; Gemini voices carry the `gemini:`
    prefix and a "Gemini · " label so the two houses read apart."""
    rows = []
    for name, tags in _all_rosters().items():
        which = voice_backend(name)
        bare = name[len(GEMINI_VOICE_PREFIX) :] if which == "gemini" else name
        rows.append(
            {
                "key": name,
                "label": (f"Gemini · {bare}" if which == "gemini" else bare[:1].upper() + bare[1:]),
                "backend": which,
                "tags": list(tags),
                "hint": ", ".join(t for t in tags if t not in {"male", "female"}),
            }
        )
    return rows


def describe_voice(voice_ref: str) -> str:
    """A roster voice as a casting read for a spoken prompt (Blueprint 31
    §10): `a female voice — soothing, calm, intimate`. '' for a voice the
    rosters do not know (a CLI narrator, an authored lock)."""
    ref = str(voice_ref or "").strip()
    if not ref:
        return ""
    tags = next((t for name, t in _all_rosters().items() if name.lower() == ref.lower()), None)
    if not tags:
        return ""
    gender = next((t for t in tags if t in {"male", "female"}), "")
    rest = [t for t in tags if t not in {"male", "female"}][:4]
    head = f"a {gender} voice" if gender else "a voice"
    return f"{head} — {', '.join(rest)}" if rest else head


def normalize_voice(value: str | None) -> str:
    """A console/API voice value as the rosters know it: '' for Auto (the
    Director casts), else the roster key. Unknown names raise ValueError."""
    chosen = str(value or "").strip().lower()
    if chosen in {"", "auto"}:
        return ""
    keys = {name.lower(): name for name in _all_rosters()}
    if chosen not in keys:
        raise ValueError(
            f"unknown narrator voice {value!r} — choose Auto or one of: "
            + ", ".join(sorted(keys.values()))
        )
    return keys[chosen]


def cast_voice(
    *,
    speaker: str = "",
    tone_text: str = "",
    character_notes: str = "",
    seed: str = "",
    avoid: set[str] | None = None,
) -> tuple[str, str]:
    """Pick the roster voice that reads like this production.

    Tone text (the master prompt's mood, visual style, and performance, plus
    the cue's own direction) scores against each voice's tags; a character
    whose notes read a gender strongly prefers a matching voice. `avoid` is
    the voices already cast to OTHER characters of this film (Blueprint 31
    §10): they lose to any other voice of the right gender so two characters
    never share a roster voice while one is free. Ties break
    on a stable hash of the speaker AND the film (`seed`, the session), so
    one production casts the same voice on every assemble — while different
    films spread across the roster instead of every tone-neutral film
    landing on the same name (the "one default voice for all clips" defect)."""
    roster = _roster()
    tone = f" {tone_text.strip().lower()} "
    notes = f" {character_notes.strip().lower()} "
    gender = ""
    if _FEMALE_TELLS.search(notes) and not _MALE_TELLS.search(notes):
        gender = "female"
    elif _MALE_TELLS.search(notes) and not _FEMALE_TELLS.search(notes):
        gender = "male"
    scores: dict[str, int] = {}
    hits: dict[str, list[str]] = {}
    for name, tags in roster.items():
        matched = [t for t in tags if t not in {"male", "female"} and (t in tone or t in notes)]
        score = len(matched)
        if gender:
            if gender in tags:
                score += 3
            elif "male" in tags or "female" in tags:
                score -= 3
        scores[name] = score
        hits[name] = matched
    taken = {str(v).lower() for v in (avoid or set())}
    if taken and any(name.lower() not in taken for name in scores):
        for name in list(scores):
            if name.lower() in taken:
                scores[name] -= 10
    best = max(scores.values())
    top = sorted(name for name, score in scores.items() if score == best)
    spread = int(hashlib.md5(f"{seed}|{speaker or 'narrator'}".encode()).hexdigest(), 16)
    pick = top[spread % len(top)]
    why = ", ".join(hits[pick][:3]) or "roster default for this tone"
    if gender:
        why = f"{gender} read; {why}"
    return pick, f"cast for: {why}"


def cast_cues(
    cues: list[NarrationCue],
    *,
    tone_text: str = "",
    character_notes: dict[str, str] | None = None,
    voice: str = "",
    seed: str = "",
    character_voices: dict[str, str] | None = None,
) -> None:
    """Resolve every cue's voice, in authored order of authority: a clip's
    voice lock; the voice chosen for this film in the Director console
    (`voice`); the site-pinned DIONY_GROK_VOICE; else ONE voice cast from
    the roster for the whole production (spread per film by `seed`).

    Single-speaker doctrine (Narrated): per-speaker casting handed a
    two-character scene two narrator voices — the multiple-voices defect
    wearing the casting sheet. The film speaks with one narrator throughout;
    only an authored voice lock (or the site pin) may say otherwise. A
    production whose cues all belong to one scripted speaker still casts to
    that character's read (gender tells and all); mixed speakers cast from
    the film's tone alone.

    Character voices (Blueprint 31 §10, Dialogue and Mix): `character_voices`
    maps a speaker name to that character's voice-card read (gender, age,
    timbre — `voices.roster_notes`). A cue whose speaker is in the map is
    the CHARACTER's line on the narrator rail — a voice-over the style keeps
    in post, or a mute take the speech guard rescued — and is cast to a
    roster voice that matches the character's read, one voice per character
    and distinct across the cast; neither the console's narrator choice nor
    the site pin overrides it (a woman's line is never read by the film's
    male narrator because a pill said so). Cues outside the map — the
    narrator's own — keep the doctrine above."""
    chosen = str(voice or "").strip()
    if chosen.lower() == "auto":
        chosen = ""  # the console's Auto: the Director casts
    pin_env = GEMINI_VOICE_ENV if backend() == "gemini" else GROK_VOICE_ENV
    pinned = os.environ.get(pin_env, "").strip()
    notes = {str(k).strip().lower(): str(v) for k, v in (character_notes or {}).items()}
    cast_reads = {
        str(k).strip().lower(): str(v)
        for k, v in (character_voices or {}).items()
        if str(k).strip() and str(v).strip()
    }
    # A `vox-<char>@vN` ref is the splitter's CHARACTER IDENTITY lock (the
    # Flow lane's "this character keeps one voice"), not a narrator voice
    # id: sent to Grok TTS verbatim it is an unknown voice, and read as an
    # authored lock it silenced every console choice and cast on the Flow
    # route. Under the single-speaker doctrine it resolves to the film's
    # narrator like any open cue; only a real voice id stays authored law.
    for cue in cues:
        if is_identity_ref(cue.voice_ref):
            cue.voice_ref = ""
            cue.cast_note = "character voice lock → the film's narrator"
    # the characters' own cues, cast first so the narrator never takes them
    by_character: dict[str, tuple[str, str]] = {}
    for cue in cues:
        key = cue.speaker.strip().lower()
        if cue.voice_ref or key not in cast_reads:
            continue
        if key not in by_character:
            picked, why = cast_voice(
                speaker=cue.speaker,
                tone_text=cast_reads[key],
                character_notes=cast_reads[key],
                seed=seed,
                avoid={v for v, _ in by_character.values()},
            )
            by_character[key] = (picked, why)
        picked, why = by_character[key]
        cue.voice_ref = picked
        rescued = " — rescued in the character's voice" if cue.voicing == "generated" else ""
        cue.cast_note = f"character voice ({cue.speaker.strip()}): {why}{rescued}"
    open_cues = [cue for cue in cues if not cue.voice_ref]
    speakers = {c.speaker.strip().lower() for c in open_cues if c.speaker.strip()}
    solo = next(iter(speakers)) if len(speakers) == 1 else ""
    voice = ""
    note = ""
    if open_cues and not chosen and not pinned:
        directions = " ".join(c.direction for c in open_cues if c.direction)
        voice, note = cast_voice(
            speaker=solo or "narrator",
            tone_text=f"{tone_text} {directions}",
            character_notes=notes.get(solo, ""),
            seed=seed,
        )
        note += " — one voice for the whole film"
    for cue in cues:
        if cue.voice_ref:
            if not cue.cast_note.startswith("character voice ("):
                cue.cast_note = "authored voice lock"
            continue
        identity = cue.cast_note if cue.cast_note.startswith("character voice lock") else ""
        prefix = f"{identity}; " if identity else ""
        if chosen:
            cue.voice_ref = chosen
            cue.cast_note = prefix + "chosen in the Director console — one voice for the whole film"
            continue
        if pinned:
            cue.voice_ref = pinned
            cue.cast_note = prefix + f"site-pinned {pin_env}"
            continue
        cue.voice_ref = voice
        cue.cast_note = prefix + note


@dataclass
class NarrationReport:
    path: Path
    loudness_target: str
    cues: list = field(default_factory=list)  # {clip_id, speaker, line, start_s, duration_s}
    overruns: list = field(default_factory=list)
    # cues pushed off their clip's mark so an overrunning predecessor could
    # finish — the no-overlap layout's receipts, never a silent adjustment
    shifts: list = field(default_factory=list)
    # how long the final frame held so the last line could finish (0 when
    # the picture already outlasted the voice), and the master's final length
    tail_hold_s: float = 0.0
    duration_s: float = 0.0


def tail_hold_for(
    cues: list[NarrationCue], durations: list[float], film_s: float
) -> tuple[float, float]:
    """(hold, surplus): the seconds the last frame must hold after the picture
    ends so the last spoken line completes with a breath, capped at
    MAX_TAIL_HOLD_S; `surplus` is what the cap still cuts (0 when it fits)."""
    if not cues:
        return 0.0, 0.0
    rail_end = max(cue.start_s + duration for cue, duration in zip(cues, durations))
    need = rail_end + TAIL_BREATH_S - film_s
    if need <= 0:
        return 0.0, 0.0
    hold = min(need, MAX_TAIL_HOLD_S)
    return round(hold, 3), round(max(0.0, need - MAX_TAIL_HOLD_S), 3)


def layout_cues(cues: list[NarrationCue], durations: list[float]) -> list[str]:
    """Guarantee the narrator rail speaks one cue at a time.

    Cues are scheduled at their clip's timeline start; a line that runs past
    the next cue's mark would otherwise play UNDER it — two voices at once.
    Each cue therefore starts no earlier than the previous cue's end plus a
    breath. The shift is applied in place and named in the returned receipts;
    the words themselves are never trimmed or sped up — the lasting fix for a
    chronic overrun is a longer clip, and overruns stay reported."""
    shifts: list[str] = []
    previous_end: float | None = None
    for cue, duration in zip(cues, durations):
        scheduled = cue.start_s
        earliest = (
            scheduled if previous_end is None else max(scheduled, previous_end + MIN_CUE_GAP_S)
        )
        if earliest > scheduled:
            cue.start_s = round(earliest, 3)
            shifts.append(
                f"{cue.clip_id}: cue pushed {earliest - scheduled:.2f}s late so the "
                "previous line could finish — one voice at a time"
            )
        previous_end = cue.start_s + duration
    return shifts


def backend() -> str:
    """Which narrator speaks: "grok" | "gemini" | "cli" | "" (none configured).

    An explicit DIONY_NARRATOR_BACKEND is law. Unset, an authored CLI
    template outranks the key-derived default — a site that pinned its own
    narrator meant it. Then the camera's own house: Gemini speech on the
    Google Flow camera when GEMINI_API_KEY is set, the Grok voice API on the
    Grok camera; and whichever key exists when the house's own is missing."""
    from diony_contracts.cluster import read_engine_choice
    from diony_director import brain as brain_mod
    from diony_director.providers import grok

    choice = os.environ.get(BACKEND_ENV, "").strip().lower()
    if choice in {"grok", "gemini", "cli"}:
        return choice
    if choice:
        raise NarrationFailed(
            f"{BACKEND_ENV}={choice!r} names no narrator backend — expected grok, gemini or cli"
        )
    if os.environ.get(NARRATOR_ENV, "").strip():
        return "cli"
    order = ["gemini", "grok"] if read_engine_choice() == "google-flow" else ["grok", "gemini"]
    for name in order:
        if name == "grok" and grok.configured():
            return "grok"
        if name == "gemini" and brain_mod.gemini_configured():
            return "gemini"
    return ""


def narrator_configured() -> bool:
    from diony_director import brain as brain_mod
    from diony_director.providers import grok

    which = backend()
    if which == "cli":
        return bool(os.environ.get(NARRATOR_ENV, "").strip())
    if which == "grok":
        return grok.configured()
    if which == "gemini":
        return brain_mod.gemini_configured()
    return False


def cue_sheet(entries: list[dict]) -> list[NarrationCue]:
    """The narration cues a cut carries, placed on the REAL timeline.

    Each entry: {op, clip, window: (source_in_s, duration_s)} in play order —
    the same shape deliverables consume. A scripted clip's line comes from its
    audio packet; a one-shot chain link's from the narration the master
    derivation split onto its op. A clip with no line contributes nothing.

    Blueprint 31: a clip whose voicing is "generated" (the character speaks
    the line in the clip) contributes nothing either — unless the entry is
    flagged ``narrate_anyway`` (the speech guard found the provider never
    voiced the line and the narrator rescues it)."""
    cues: list[NarrationCue] = []
    timeline_s = 0.0
    for index, entry in enumerate(entries):
        clip = entry.get("clip")
        op = entry["op"]
        _, duration = entry["window"]
        line = ""
        speaker = ""
        direction = ""
        voice_ref = ""
        line_at_s = 0.0
        voicing = (
            str(
                getattr(clip.audio_packet, "voicing", "post")
                if clip is not None
                else getattr(op, "voicing", "post")
            )
            or "post"
        )
        if voicing == "generated" and not entry.get("narrate_anyway"):
            timeline_s += duration
            continue
        if (
            clip is not None
            and clip.clip_class in {"sync-dialogue", "vo-over"}
            and clip.audio_packet.script_excerpt
        ):
            excerpt = clip.audio_packet.script_excerpt[0]
            line = str(excerpt.get("line", "")).strip()
            speaker = str(excerpt.get("speaker", ""))
            # the parenthetical plus the packet's delivery notes — both feed
            # the casting AND the performed speech tags
            delivery = "; ".join(f"{k}: {v}" for k, v in clip.audio_packet.delivery.items())
            direction = "; ".join(x for x in (str(excerpt.get("parenthetical", "")), delivery) if x)
            voice_ref = str(clip.audio_packet.voice_ref or "")
            # Blueprint 33: the line begins where the take's picture beats
            # put it, never past the clip's own end
            line_at_s = min(
                max(0.0, float(getattr(clip.audio_packet, "line_at_s", 0.0) or 0.0)),
                max(0.0, duration - 0.5),
            )
        elif getattr(op, "narration", ""):
            line = str(op.narration).strip()
        if not line:
            timeline_s += duration
            continue
        cues.append(
            NarrationCue(
                order=index,
                clip_id=(clip.clip_id if clip is not None else op.id),
                speaker=speaker,
                line=line,
                direction=direction,
                voice_ref=voice_ref,
                voicing=voicing,
                start_s=round(timeline_s + line_at_s + CUE_LEAD_IN_S, 3),
                window_s=round(duration - line_at_s, 3),
            )
        )
        timeline_s += duration
    return cues


UNCONFIGURED = (
    "no narrator backend is configured — narration is recorded in post "
    "(Blueprint 28); set GEMINI_API_KEY for Gemini speech (the Google Flow "
    "camera's own narrator) or XAI_API_KEY for the Grok voice API, "
    f"or {NARRATOR_ENV} for an on-host narrator, or assemble with narrate "
    "off to cut a mute picture"
)


def _narrator_command(cue: NarrationCue, output: Path) -> list[str]:
    template = os.environ.get(NARRATOR_ENV, "").strip()
    if not template:
        raise NarrationUnavailable(UNCONFIGURED)
    substitutions = {
        "text": cue.line,
        "output": str(output),
        "voice": cue.voice_ref,
        "speaker": cue.speaker,
        "direction": cue.direction,
    }
    command: list[str] = []
    for token in shlex.split(template):
        try:
            command.append(token.format(**substitutions))
        except (KeyError, IndexError) as exc:
            raise NarrationFailed(f"{NARRATOR_ENV} has an unknown placeholder: {token}") from exc
    return command


def _synthesize_cli(cue: NarrationCue, output: Path) -> None:
    # a CLI narrator gets the raw words and the direction placeholder — tag
    # syntax is the Grok contract's, not a universal one
    cue.performed = cue.line
    command = _narrator_command(cue, output)
    result = subprocess.run(command, capture_output=True, timeout=600)
    if result.returncode != 0 or not output.is_file():
        tail = result.stderr.decode(errors="replace")[-400:]
        raise NarrationFailed(
            f"narrator failed on {cue.clip_id} ({cue.speaker or 'narration'}): {tail}"
        )


def _synthesize_grok(cue: NarrationCue, output: Path) -> None:
    """One line through the Grok TTS API — the same xAI account and base
    URL the picture lane rides; a voice lock on the cue picks the voice.

    Contract verified live (docs.x.ai + this account, 2026-09-02):
    POST /v1/tts {text, voice_id, language, output_format} → raw audio
    bytes; a JSON envelope (the with_timestamps shape) is handled too."""
    from diony_director.providers import base as provider_base
    from diony_director.providers import grok

    if not grok.configured():
        raise NarrationUnavailable(UNCONFIGURED)
    path = os.environ.get(GROK_VOICE_PATH_ENV, GROK_VOICE_PATH_DEFAULT)
    cue.performed = perform_line(cue.line, cue.direction)
    payload = {
        "text": cue.performed,
        "voice_id": (cue.voice_ref or os.environ.get(GROK_VOICE_ENV, GROK_VOICE_DEFAULT)).lower(),
        "language": os.environ.get(GROK_VOICE_LANGUAGE_ENV, GROK_VOICE_LANGUAGE_DEFAULT),
        # WAV at the mix's native 48 kHz — no lossy hop before the film mix
        "output_format": {"codec": "wav", "sample_rate": 48000},
    }
    response = provider_base.post_with_retries(
        f"{grok.api_base()}{path}",
        what="Grok voice synthesis",
        json=payload,
        headers=grok._headers(),
        timeout=GROK_CALL_TIMEOUT_S,
    )
    if response.status_code >= 400:
        raise NarrationFailed(
            f"Grok voice rejected {cue.clip_id} ({response.status_code}): {response.text[:300]}"
        )
    body = response.content
    if response.headers.get("content-type", "").startswith("application/json"):
        import base64

        try:
            body = base64.b64decode(response.json()["audio"])
        except (KeyError, ValueError) as exc:
            raise NarrationFailed(
                f"Grok voice returned JSON without decodable audio for {cue.clip_id}"
            ) from exc
    output.write_bytes(body)


def _pcm_to_wav(pcm: bytes, *, rate: int, channels: int = 1, width: int = 2) -> bytes:
    import struct

    data_size = len(pcm)
    header = b"RIFF" + struct.pack("<I", 36 + data_size) + b"WAVE"
    header += b"fmt " + struct.pack(
        "<IHHIIHH", 16, 1, channels, rate, rate * channels * width, channels * width, width * 8
    )
    header += b"data" + struct.pack("<I", data_size)
    return header + pcm


def _synthesize_gemini(cue: NarrationCue, output: Path) -> None:
    """One line through Gemini speech generation — the Google narrator on the
    same Google AI Studio key the Gemini brain uses.

    Contract (Gemini API speech generation, 2026-09-10): POST
    ``{base}/v1beta/models/{model}:generateContent`` with the line as the
    content, ``responseModalities: ["AUDIO"]`` and a prebuilt voice; the
    reply's first inline part is base64 16-bit PCM (``audio/L16;…;rate=24000``),
    wrapped into a WAV here. The words ride verbatim: Gemini reads the whole
    prompt aloud, so no delivery tag or style instruction is prepended —
    the direction shapes the casting, never the text."""
    import base64

    from diony_director import brain as brain_mod
    from diony_director.providers import base as provider_base

    if not brain_mod.gemini_configured():
        raise NarrationUnavailable(UNCONFIGURED)
    model = (
        os.environ.get(GEMINI_TTS_MODEL_ENV, "").strip()
        or brain_mod.learned_model("gemini-tts")
        or GEMINI_TTS_MODEL_DEFAULT
    )
    voice_ref = cue.voice_ref or os.environ.get(GEMINI_VOICE_ENV, "").strip() or "gemini:Kore"
    voice = (
        voice_ref[len(GEMINI_VOICE_PREFIX) :] if voice_backend(voice_ref) == "gemini" else voice_ref
    )
    cue.performed = cue.line
    payload = {
        "contents": [{"parts": [{"text": cue.performed}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}},
        },
    }
    for _attempt in range(2):
        response = provider_base.post_with_retries(
            f"{brain_mod.gemini_base()}/v1beta/models/{model}:generateContent",
            what="Gemini speech synthesis",
            json=payload,
            headers={"x-goog-api-key": brain_mod.gemini_key(), "Content-Type": "application/json"},
            timeout=GEMINI_CALL_TIMEOUT_S,
        )
        if response.status_code < 400:
            break
        # a retired speech model names its successor; take it once, and keep it
        successor = brain_mod.follow_retirement(
            "gemini-tts", response.status_code, response.text, current=model
        )
        if successor and not os.environ.get(GEMINI_TTS_MODEL_ENV, "").strip():
            model = successor
            continue
        raise NarrationFailed(
            f"Gemini speech rejected {cue.clip_id} ({response.status_code}): {response.text[:300]}"
        )
    try:
        part = response.json()["candidates"][0]["content"]["parts"][0]["inlineData"]
        pcm = base64.b64decode(part["data"])
        mime = str(part.get("mimeType") or "audio/L16;codec=pcm;rate=24000")
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise NarrationFailed(
            f"Gemini speech returned no decodable audio for {cue.clip_id}"
        ) from exc
    rate_match = re.search(r"rate=(\d+)", mime)
    rate = int(rate_match.group(1)) if rate_match else 24000
    output.write_bytes(_pcm_to_wav(pcm, rate=rate))


def synthesize(cue: NarrationCue, output: Path) -> float:
    """One line → one wav; returns its measured duration. Loud on failure.

    A cue whose voice names a house (`gemini:Kore`, or a Grok roster name)
    speaks through that house whatever the default — a Google Flow film may
    keep a Grok narrator when the xAI key is present, and the other way round."""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    which = backend()
    if which != "cli":
        named = voice_backend(cue.voice_ref)
        if named and named in available_backends():
            which = named
    if not which:
        raise NarrationUnavailable(UNCONFIGURED)
    if which == "grok":
        _synthesize_grok(cue, output)
    elif which == "gemini":
        _synthesize_gemini(cue, output)
    else:
        _synthesize_cli(cue, output)
    duration = float(_probe(output)["duration_s"])
    if duration <= 0.05:
        raise NarrationFailed(
            f"narrator produced an empty take for {cue.clip_id} — "
            f"{output.name} probes as {duration:.2f}s"
        )
    return duration


def build_filter(
    cue_count: int,
    delays_ms: list[int],
    *,
    film_duration_s: float,
    loudness_target: str,
    tail_hold_s: float = 0.0,
) -> list[str]:
    """The narration mix graph: each cue conformed and placed, one voice rail,
    the film's own track ducked under it, then the program loudness relocked.
    Input 0 is the assembled film; inputs 1..N are the cue wavs in order.
    With a tail hold the picture's last frame is cloned for `tail_hold_s`
    and the location bed is padded under it (silence plus the room-tone
    floor, so the hold never falls into digital silence) — the voice rail
    is no longer cut at the picture's booked end."""
    chains: list[str] = []
    total_s = film_duration_s + max(0.0, tail_hold_s)
    rail_inputs = []
    for index in range(cue_count):
        chains.append(
            f"[{index + 1}:a]aresample=48000,aformat=channel_layouts=stereo,"
            f"loudnorm=I={NARRATION_LOUDNESS}:TP=-2.0:LRA=9,"
            f"adelay={delays_ms[index]}:all=1[n{index}]"
        )
        rail_inputs.append(f"[n{index}]")
    if cue_count > 1:
        chains.append(
            f"{''.join(rail_inputs)}amix=inputs={cue_count}:duration=longest:normalize=0[railraw]"
        )
    else:
        chains.append(f"{rail_inputs[0]}acopy[railraw]")
    chains.append(f"[railraw]apad=whole_dur={total_s:.3f}[rail]")
    chains.append("[rail]asplit=2[railmix][railkey]")
    if tail_hold_s > 0:
        chains.append(
            f"[0:a]aresample=48000,aformat=channel_layouts=stereo,"
            f"apad=pad_dur={tail_hold_s:.3f}[bedpad]"
        )
        chains.append(
            f"anoisesrc=r=48000:colour=brown:amplitude={ROOM_BED_AMPLITUDE}:d={tail_hold_s:.3f},"
            f"aformat=channel_layouts=stereo,adelay={int(round(film_duration_s * 1000))}:all=1[floor]"
        )
        chains.append("[bedpad][floor]amix=inputs=2:duration=first:normalize=0[bed]")
        chains.append(f"[0:v]tpad=stop_mode=clone:stop_duration={tail_hold_s:.3f}[vout]")
    else:
        chains.append("[0:a]aresample=48000,aformat=channel_layouts=stereo[bed]")
    chains.append(f"[bed][railkey]{DUCK}[ducked]")
    target = LOUDNESS.get(loudness_target, LOUDNESS["streaming"])
    chains.append(
        f"[ducked][railmix]amix=inputs=2:duration=first:normalize=0,"
        f"loudnorm=I={target}:TP=-1.0:LRA=11,aresample=48000[aout]"
    )
    return chains


def narrate(
    film: Path,
    cues: list[NarrationCue],
    dest: Path,
    *,
    loudness_target: str = "streaming",
    work_dir: Path | None = None,
) -> NarrationReport:
    """Record the cue sheet against the assembled film and mix it in.

    The picture stream is copied untouched — narration never re-encodes the
    cut. Overruns (a line longer than its clip window plus the tail
    tolerance) are named in the report, never silently trimmed: the words
    are the authored law, the fix is a longer clip."""
    if not cues:
        raise NarrationFailed("nothing to narrate: the cue sheet is empty")
    work = work_dir or dest.parent / "narration"
    work.mkdir(parents=True, exist_ok=True)
    film_probe = _probe(film)
    film_s = float(film_probe["duration_s"])

    durations: list[float] = []
    wavs: list[Path] = []
    overruns: list[str] = []
    for cue in cues:
        wav = work / f"cue-{cue.order:02d}.wav"
        durations.append(synthesize(cue, wav))
        wavs.append(wav)
        surplus = durations[-1] - (cue.window_s + OVERRUN_TOLERANCE_S)
        if surplus > 0:
            overruns.append(
                f"{cue.clip_id}: the line plays {durations[-1]:.1f}s in a "
                f"{cue.window_s:.1f}s clip ({surplus:.1f}s over) — book a longer clip"
            )

    # one voice at a time: an overrunning line finishes before the next
    # cue may start — cue marks shift late rather than ever overlapping
    shifts = layout_cues(cues, durations)
    delays_ms = [max(0, int(round(cue.start_s * 1000))) for cue in cues]
    # the film ends when the last line ends: hold the final frame for the
    # words that play past the picture's booked length (capped, and the
    # part the cap still cuts is named)
    tail_hold_s, tail_surplus_s = tail_hold_for(cues, durations, film_s)
    if tail_surplus_s > 0:
        last = max(zip(cues, durations), key=lambda pair: pair[0].start_s + pair[1])[0]
        overruns.append(
            f"{last.clip_id}: the last line still runs {tail_surplus_s:.1f}s past the "
            f"{MAX_TAIL_HOLD_S:.0f}s tail hold — book a longer final clip"
        )
    chains = build_filter(
        len(cues),
        delays_ms,
        film_duration_s=film_s,
        loudness_target=loudness_target,
        tail_hold_s=tail_hold_s,
    )
    cmd: list[str] = [_ffmpeg(), "-y", "-i", str(film)]
    for wav in wavs:
        cmd += ["-i", str(wav)]
    cmd += ["-filter_complex", ";".join(chains)]
    if tail_hold_s > 0:
        # the held frame is new picture: this is the one case narration
        # re-encodes the cut (same codec/quality as assembly)
        cmd += [
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
        ]
    else:
        cmd += ["-map", "0:v", "-map", "[aout]", "-c:v", "copy"]
    cmd += ["-c:a", "aac", "-b:a", "192k", str(dest)]
    result = subprocess.run(cmd, capture_output=True, timeout=1800)
    if result.returncode != 0 or not dest.is_file():
        tail = result.stderr.decode(errors="replace")[-800:]
        raise NarrationFailed(f"narration mix failed: {tail}")

    return NarrationReport(
        path=dest,
        loudness_target=loudness_target,
        cues=[
            {
                "clip_id": cue.clip_id,
                "speaker": cue.speaker,
                "line": cue.line,
                "direction": cue.direction,
                "performed": cue.performed or cue.line,
                "voice": cue.voice_ref,
                "cast": cue.cast_note,
                "start_s": cue.start_s,
                "duration_s": round(durations[index], 3),
            }
            for index, cue in enumerate(cues)
        ],
        overruns=overruns,
        shifts=shifts,
        tail_hold_s=tail_hold_s,
        duration_s=float(_probe(dest)["duration_s"]),
    )
