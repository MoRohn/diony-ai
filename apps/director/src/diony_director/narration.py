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

from diony_director.assembly import LOUDNESS, _ffmpeg, _probe

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

_FEMALE_TELLS = re.compile(r"\b(she|her|hers|woman|women|female|girl|mother|sister|queen|actress|lady)\b")
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
# Voice sits above the -18 LUFS clip bed; the duck keeps the location track
# out of its way (same sidechain values the Producer program mix earned).
NARRATION_LOUDNESS = "-15"
DUCK = "sidechaincompress=threshold=0.02:ratio=6:attack=25:release=350"
# A line may breathe into the next clip by its tail handle; past that the
# picture is too short for the words and the cut needs attention.
OVERRUN_TOLERANCE_S = 1.0


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
    # the line as actually performed — the verbatim words plus any speech
    # tags the direction mapped to; set at synthesis, audited in the report
    performed: str = ""
    start_s: float = 0.0
    window_s: float = 0.0


def _roster() -> dict[str, list[str]]:
    raw = os.environ.get(GROK_VOICE_ROSTER_ENV, "").strip()
    if not raw:
        return DEFAULT_ROSTER
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
        f"{GROK_VOICE_ROSTER_ENV} is set but is not a JSON object of "
        '{"Voice": ["tag", ...]} — fix it or unset it for the built-in roster'
    )


def cast_voice(*, speaker: str = "", tone_text: str = "", character_notes: str = "") -> tuple[str, str]:
    """Pick the roster voice that reads like this production.

    Tone text (the master prompt's mood, visual style, and performance, plus
    the cue's own direction) scores against each voice's tags; a character
    whose notes read a gender strongly prefers a matching voice. Ties break
    on a stable hash of the speaker, so one production casts each character
    to the same voice on every assemble — and different characters spread
    across the roster instead of piling onto one name."""
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
    best = max(scores.values())
    top = sorted(name for name, score in scores.items() if score == best)
    spread = int(hashlib.md5((speaker or "narrator").encode()).hexdigest(), 16)
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
) -> None:
    """Resolve every cue's voice, in authored order of authority: a clip's
    voice lock; the site-pinned DIONY_GROK_VOICE; else a cast from the
    roster against the master's tone and the speaking character."""
    pinned = os.environ.get(GROK_VOICE_ENV, "").strip()
    notes = {str(k).strip().lower(): str(v) for k, v in (character_notes or {}).items()}
    for cue in cues:
        if cue.voice_ref:
            cue.cast_note = "authored voice lock"
            continue
        if pinned:
            cue.voice_ref = pinned
            cue.cast_note = f"site-pinned {GROK_VOICE_ENV}"
            continue
        cue.voice_ref, cue.cast_note = cast_voice(
            speaker=cue.speaker,
            tone_text=f"{tone_text} {cue.direction}",
            character_notes=notes.get(cue.speaker.strip().lower(), ""),
        )


@dataclass
class NarrationReport:
    path: Path
    loudness_target: str
    cues: list = field(default_factory=list)  # {clip_id, speaker, line, start_s, duration_s}
    overruns: list = field(default_factory=list)


def backend() -> str:
    """Which narrator speaks: "grok" | "cli" | "" (none configured).

    An explicit DIONY_NARRATOR_BACKEND is law. Unset, an authored CLI
    template outranks the key-derived default — a site that pinned its own
    narrator meant it — and the Grok voice API is the zero-config default on
    every box that already holds the xAI key the picture lane uses."""
    from diony_director.providers import grok

    choice = os.environ.get(BACKEND_ENV, "").strip().lower()
    if choice in {"grok", "cli"}:
        return choice
    if choice:
        raise NarrationFailed(
            f"{BACKEND_ENV}={choice!r} names no narrator backend — expected grok or cli"
        )
    if os.environ.get(NARRATOR_ENV, "").strip():
        return "cli"
    if grok.configured():
        return "grok"
    return ""


def narrator_configured() -> bool:
    from diony_director.providers import grok

    which = backend()
    if which == "cli":
        return bool(os.environ.get(NARRATOR_ENV, "").strip())
    if which == "grok":
        return grok.configured()
    return False


def cue_sheet(entries: list[dict]) -> list[NarrationCue]:
    """The narration cues a cut carries, placed on the REAL timeline.

    Each entry: {op, clip, window: (source_in_s, duration_s)} in play order —
    the same shape deliverables consume. A scripted clip's line comes from its
    audio packet; a one-shot chain link's from the narration the master
    derivation split onto its op. A clip with no line contributes nothing."""
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
            delivery = "; ".join(
                f"{k}: {v}" for k, v in clip.audio_packet.delivery.items()
            )
            direction = "; ".join(
                x for x in (str(excerpt.get("parenthetical", "")), delivery) if x
            )
            voice_ref = str(clip.audio_packet.voice_ref or "")
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
                start_s=round(timeline_s + CUE_LEAD_IN_S, 3),
                window_s=round(duration, 3),
            )
        )
        timeline_s += duration
    return cues


UNCONFIGURED = (
    "no narrator backend is configured — narration is recorded in post "
    "(Blueprint 28); set XAI_API_KEY for the Grok voice API (the default), "
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
            f"Grok voice rejected {cue.clip_id} ({response.status_code}): "
            f"{response.text[:300]}"
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


def synthesize(cue: NarrationCue, output: Path) -> float:
    """One line → one wav; returns its measured duration. Loud on failure."""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    which = backend()
    if not which:
        raise NarrationUnavailable(UNCONFIGURED)
    if which == "grok":
        _synthesize_grok(cue, output)
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
) -> list[str]:
    """The narration mix graph: each cue conformed and placed, one voice rail,
    the film's own track ducked under it, then the program loudness relocked.
    Input 0 is the assembled film; inputs 1..N are the cue wavs in order."""
    chains: list[str] = []
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
    chains.append(f"[railraw]apad=whole_dur={film_duration_s:.3f}[rail]")
    chains.append("[rail]asplit=2[railmix][railkey]")
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

    delays_ms = [max(0, int(round(cue.start_s * 1000))) for cue in cues]
    chains = build_filter(
        len(cues), delays_ms, film_duration_s=film_s, loudness_target=loudness_target
    )
    cmd: list[str] = [_ffmpeg(), "-y", "-i", str(film)]
    for wav in wavs:
        cmd += ["-i", str(wav)]
    cmd += [
        "-filter_complex", ";".join(chains),
        "-map", "0:v", "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        str(dest),
    ]
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
    )
