"""Grok Imagine prompt templating: the MOTION + CAMERA + ATMOSPHERE lexicon.

Doctrine distilled from the Grok Imagine Video 1.5 prompt guide (jxp.com,
studied 2026-09-02): a Grok prompt is motion verb + camera instruction +
atmosphere + audio cue, with the model weighing the EARLIEST words most —
motion or camera first, always. Direct, don't describe: "camera slowly
pushes in" works; "cinematic and dynamic" doesn't. Atmosphere stacks two or
three elements maximum. And the camera lock phrasing is a reliability cliff:
"camera not moving" and "locked camera" hold; "stable camera" and "steady
shot" read as smooth-motion descriptions and the camera drifts.

This lexicon is Grok-route ONLY. The Flow/Veo dialects never see it — they
speak the Flow lexicon — and a Grok-only token on a Flow clip rides through
as literal text, exactly as a Flow token does on Grok.

Three surfaces, mirroring flow_lexicon:
- a per-session template (camera + atmosphere) picked in the Director UI and
  persisted in state.json — applied to single-shot chat proposals (scripted
  clips keep their authored shots; the atmosphere, an environmental
  signature like a scene-wide weather, rides Grok picture clips too);
- inline /shorthand tokens (``/pushin``, ``/locked``, ``/dust`` …) in ideas
  and authored shot lines, expanded at compile time on the Grok lane with
  the guide's exact reliable phrasing;
- a custom lexicon file at ``{cluster_root}/.cluster/grok_lexicon.json``
  (``{"cameras": {key: {"label", "clause", "hint"?}}, "atmospheres": {...}}``)
  merged over the built-ins.
"""

from __future__ import annotations

import re
from typing import Any

# --- Camera instructions (the guide's reliable phrasings, verbatim) ----------

CAMERAS: dict[str, dict[str, str]] = {
    "pushin": {
        "label": "Push in",
        "hint": "subject enlarges, creates intimacy",
        "clause": "camera slowly pushes in",
    },
    "pullback": {
        "label": "Pull back",
        "hint": "expands context, establishes scale",
        "clause": "camera pulls back to reveal the wider scene",
    },
    "orbit": {
        "label": "Orbit",
        "hint": "360° reveal, product showcase",
        "clause": "camera orbits the subject slowly",
    },
    "panleft": {
        "label": "Pan left",
        "hint": "reveals new content leftward",
        "clause": "camera pans left",
    },
    "panright": {
        "label": "Pan right",
        "hint": "reveals new content rightward",
        "clause": "camera pans right",
    },
    "rise": {
        "label": "Rise",
        "hint": "aerial reveal, establishing shot",
        "clause": "camera rises above the subject",
    },
    "locked": {
        "label": "Locked",
        "hint": "the reliable lock — reduces identity drift on faces",
        "clause": "camera not moving",
    },
    "freeze": {
        "label": "Subject freeze",
        "hint": "holds the subject still — negative instructions work best",
        "clause": "subject frozen, hands completely still",
    },
}

# --- Atmosphere elements (stack two or three maximum) ------------------------

ATMOSPHERES: dict[str, dict[str, str]] = {
    "dust": {
        "label": "Dust",
        "hint": "action, athletic, western",
        "clause": "dust particles swirl around the subject",
    },
    "rain": {
        "label": "Rain",
        "hint": "drama, romance, urban",
        "clause": "rain falls in the foreground",
    },
    "fog": {
        "label": "Fog / haze",
        "hint": "mystery, forest, horror",
        "clause": "volumetric haze, low-lying fog",
    },
    "heat": {
        "label": "Heat shimmer",
        "hint": "desert, summer, industrial",
        "clause": "heat shimmer rising from the ground",
    },
    "particles": {
        "label": "Light particles",
        "hint": "fantasy, luxury, beauty",
        "clause": "golden light particles drift through the air",
    },
    "wind": {
        "label": "Wind",
        "hint": "portrait, outdoor, emotion",
        "clause": "the wind moves hair and clothing",
    },
    "water": {
        "label": "Water",
        "hint": "nature, calm, meditation",
        "clause": "water ripples across the surface",
    },
    "smoke": {
        "label": "Smoke",
        "hint": "industrial, moody, cinematic",
        "clause": "thin smoke drifts through the frame",
    },
}

# The guide's reliability cliff: these phrasings read as smooth-motion
# DESCRIPTIONS and the camera drifts. Each is rewritten to a reliable form
# that keeps the sentence grammatical — "steady shot of the harbor" must
# become "locked camera shot of the harbor", never "camera not moving of
# the harbor" riding a paid prompt as broken English.
_UNRELIABLE_LOCK_REWRITES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bsteady shot\b", re.IGNORECASE), "locked camera shot"),
    (re.compile(r"\b(?:stable|steady) camera\b", re.IGNORECASE), "locked camera"),
    (re.compile(r"\bcamera (?:stays|holds) steady\b", re.IGNORECASE), "camera not moving"),
)
RELIABLE_LOCK = "camera not moving"

# i2v doctrine: the model already sees the frame — redescription is noise
# and redesign is drift. Rides every Grok proposal that opens from a still.
I2V_DIRECTIVE = "Animate the source frame as it is; do not redesign it."


def harden_camera_locks(text: str) -> str:
    """Swap the phrasings the model misreads for ones it obeys."""
    for pattern, replacement in _UNRELIABLE_LOCK_REWRITES:
        text = pattern.sub(replacement, text)
    return text


# Authored camera prose — "camera pans across the harbor", "slow dolly in" —
# means the author already directed the camera; a session template camera
# stacked on top is two conflicting moves in one paid prompt, the exact
# mistake the guide warns about. Deliberately broad: a false hit merely
# leaves the template off one proposal, a miss ships a contradiction.
_CAMERA_PROSE = re.compile(
    r"\b(?:camera|shot|close-?up|wide[- ]angle|pov|dolly|dollies|pan(?:s|ning)?|"
    r"orbit(?:s|ing)?|zoom(?:s|ing)?|tracking|crane|handheld|aerial|drone|"
    r"timelapse|hyperlapse|slow[- ]motion)\b",
    re.IGNORECASE,
)


def mentions_camera(text: str) -> bool:
    """Whether the text already carries camera direction in prose."""
    return bool(_CAMERA_PROSE.search(text))


def sentence(text: str) -> str:
    """Uppercase only the first character — str.capitalize() would lowercase
    the rest and mangle proper nouns in custom clauses ("LED" → "Led")."""
    return text[:1].upper() + text[1:] if text else text


# One parse per file version — see flow_lexicon._CUSTOM_CACHE for the why.
_CUSTOM_CACHE: dict[str, Any] = {"sig": (), "value": None}


def _custom_lexicon() -> dict[str, dict[str, dict[str, str]]]:
    """User-defined cameras/atmospheres, merged over the built-ins. A corrupt
    or absent file — or any malformed entry inside it — is an empty overlay —
    customization never breaks boot or a compile."""
    import json

    from diony_contracts.cluster import cluster_root

    path = cluster_root() / ".cluster" / "grok_lexicon.json"
    try:
        stat = path.stat()
        sig: tuple = (str(path), stat.st_mtime_ns, stat.st_size)
    except OSError:
        sig = (str(path), "absent")
    if _CUSTOM_CACHE["sig"] == sig and _CUSTOM_CACHE["value"] is not None:
        return _CUSTOM_CACHE["value"]
    try:
        raw = json.loads(path.read_text())
    except Exception:
        raw = {}
    out: dict[str, dict[str, dict[str, str]]] = {"cameras": {}, "atmospheres": {}}
    other_family = {"cameras": ATMOSPHERES, "atmospheres": CAMERAS}
    for family in ("cameras", "atmospheres"):
        entries = raw.get(family) if isinstance(raw, dict) else None
        if not isinstance(entries, dict):
            continue
        for key, entry in entries.items():
            slug = str(key).strip().lower()
            if not re.fullmatch(r"[a-z][a-z0-9_-]*", slug):
                continue
            if slug in other_family[family]:
                continue
            if not isinstance(entry, dict):
                continue  # a bare string/list entry is malformed, not a clause
            clause = entry.get("clause", "")
            if not isinstance(clause, str) or not clause.strip():
                continue
            out[family][slug] = {
                "label": str(entry.get("label", slug)).strip() or slug,
                "hint": str(entry.get("hint", "custom")).strip(),
                "clause": clause.strip(),
            }
    for slug in set(out["cameras"]) & set(out["atmospheres"]):
        del out["atmospheres"][slug]
    _CUSTOM_CACHE["sig"] = sig
    _CUSTOM_CACHE["value"] = out
    return out


def cameras() -> dict[str, dict[str, str]]:
    return {**CAMERAS, **_custom_lexicon()["cameras"]}


def atmospheres() -> dict[str, dict[str, str]]:
    return {**ATMOSPHERES, **_custom_lexicon()["atmospheres"]}


def catalog() -> dict[str, Any]:
    def _rows(table: dict[str, dict[str, str]]) -> list[dict[str, str]]:
        return [
            {"key": key, "label": entry["label"], "hint": entry["hint"], "clause": entry["clause"]}
            for key, entry in table.items()
        ]

    from diony_contracts.cluster import cluster_root

    return {
        "formula": (
            "MOTION + CAMERA + ATMOSPHERE + AUDIO — the model weighs the earliest "
            "words most; direct, don't describe"
        ),
        "cameras": _rows(cameras()),
        "atmospheres": _rows(atmospheres()),
        "custom_path": str(cluster_root() / ".cluster" / "grok_lexicon.json"),
        "grok_route_only": True,
    }


# --- Per-session template ----------------------------------------------------

_TEMPLATES: dict[str, dict[str, str]] = {}


def reset_templates() -> None:
    _TEMPLATES.clear()


def template_for(sid: str) -> dict[str, str]:
    stored = _TEMPLATES.get(sid) or {}
    return {
        "camera": stored.get("camera") or "",
        "atmosphere": stored.get("atmosphere") or "",
    }


def set_template(sid: str, *, camera: str = "", atmosphere: str = "") -> dict[str, str]:
    camera_key = (camera or "").strip().lower()
    atmosphere_key = (atmosphere or "").strip().lower()
    if camera_key and camera_key not in cameras():
        raise ValueError(
            f"unknown Grok camera {camera!r} — expected one of {', '.join(sorted(cameras()))}"
        )
    if atmosphere_key and atmosphere_key not in atmospheres():
        raise ValueError(
            f"unknown Grok atmosphere {atmosphere!r} — expected one of {', '.join(sorted(atmospheres()))}"
        )
    _TEMPLATES[sid] = {"camera": camera_key, "atmosphere": atmosphere_key}
    return template_for(sid)


def restore_template(sid: str, doc: Any) -> None:
    """Rehydrate from state.json; a since-removed custom key falls back to
    defaults rather than bricking the session."""
    if not isinstance(doc, dict):
        return
    try:
        set_template(
            sid,
            camera=str(doc.get("camera", "")),
            atmosphere=str(doc.get("atmosphere", "")),
        )
    except ValueError:
        pass


def session_camera_clause(sid: str) -> str:
    key = template_for(sid)["camera"]
    return cameras().get(key, {}).get("clause", "") if key else ""


# The scripted setting's weather is law: a session atmosphere is a scene-wide
# signature, and one that contradicts the setting ("Rain falls" over "Night 1,
# clear") makes the pictures fight the script. Per-atmosphere conflict words —
# a custom atmosphere has no entry and always rides.
_ATMO_CONFLICTS: dict[str, re.Pattern[str]] = {
    "dust": re.compile(r"\b(?:rain|rainy|raining|downpour|storm|stormy|snow|snowy)\b", re.I),
    "rain": re.compile(r"\b(?:clear|sunny|cloudless|dry|snow|snowy|snowing)\b", re.I),
    "fog": re.compile(r"\b(?:clear|sunny|cloudless)\b", re.I),
    "heat": re.compile(
        r"\b(?:rain|rainy|raining|snow|snowy|storm|stormy|cold|freezing|winter)\b", re.I
    ),
    "smoke": re.compile(r"\b(?:downpour|storm|stormy)\b", re.I),
}


def session_atmosphere_clause(sid: str, *, weather: str = "") -> str:
    """The template atmosphere's clause — suppressed when the scripted
    weather names a condition it would contradict."""
    key = template_for(sid)["atmosphere"]
    if not key:
        return ""
    conflict = _ATMO_CONFLICTS.get(key)
    if weather and conflict and conflict.search(weather):
        return ""
    return atmospheres().get(key, {}).get("clause", "")


# --- Inline /shorthand expansion --------------------------------------------

# Same token grammar as the Flow lexicon: a /word at the start or after
# whitespace/"("/"+" — never mid-word, so "day/night" prose is safe; matching
# is case-insensitive. The tables differ: the same /orbit expands to Grok
# phrasing here and to Veo phrasing on the Flow lane — the shorthand is
# route-dialect-aware.
# :? only — a \s*:? here would eat the separating space between adjacent
# tokens ("/locked /dust" → "…movingdust…").
_TOKEN = re.compile(r"(?:^|(?<=[\s(+]))/([A-Za-z][A-Za-z0-9_-]*):?")
_TOKEN_JOIN = re.compile(r"\s*\+\s*(?=/)")


def expand(text: str) -> tuple[str, bool, bool]:
    """Expand Grok shorthand in one prompt fragment. Both families replace
    inline — camera and atmosphere are prose, not slots. Unknown tokens pass
    through untouched. Returns (expanded_text, camera_seen, atmosphere_seen)
    so a session template never stacks over an inline choice."""
    if "/" not in text:
        return text, False, False
    camera_table, atmosphere_table = cameras(), atmospheres()
    known = set(camera_table) | set(atmosphere_table)
    camera_seen = False
    atmosphere_seen = False

    def _sub(match: re.Match[str]) -> str:
        nonlocal camera_seen, atmosphere_seen
        key = match.group(1).lower()
        if key in camera_table:
            camera_seen = True
            clause = camera_table[key]["clause"]
        elif key in atmosphere_table:
            atmosphere_seen = True
            clause = atmosphere_table[key]["clause"]
        else:
            return match.group(0)
        # Two adjacent tokens must not fuse into one run-on ("camera not
        # moving dust particles swirl…"): a comma lands between their
        # clauses. A prose continuation stays seamless — no comma.
        following = re.match(r"\s+/([A-Za-z][A-Za-z0-9_-]*)", match.string[match.end() :])
        if following and following.group(1).lower() in known:
            return clause + ","
        return clause

    normalized = _TOKEN_JOIN.sub(" ", text)
    out = _TOKEN.sub(_sub, normalized)
    out = re.sub(r",\s*([,.;:!?)])", r"\1", out)  # no comma debris before punctuation
    out = re.sub(r"\s+([,.;:!?])", r"\1", out)
    out = re.sub(r"\s{2,}", " ", out).strip().lstrip(",.;:!? ").strip().rstrip(",;: ")
    return out, camera_seen, atmosphere_seen


def unknown_tokens(text: str) -> list[str]:
    """Slash tokens matching no Grok lexicon entry — on the Grok route they
    ride the paid prompt as literal text."""
    if "/" not in text:
        return []
    known = set(cameras()) | set(atmospheres())
    seen: list[str] = []
    for match in _TOKEN.finditer(_TOKEN_JOIN.sub(" ", text)):
        key = match.group(1).lower()
        if key not in known and f"/{key}" not in seen:
            seen.append(f"/{key}")
    return seen


# Mistake 4 in the guide: the model outputs 480p/720p — resolution words in
# the prompt are generation-wasters, and the Res control owns output size.
_RESOLUTION_ASKS = re.compile(
    r"\b(?:4k|8k|uhd|ultra[- ]hd|1080p|high[- ]resolution|ultra[- ]high[- ]detail)\b",
    re.IGNORECASE,
)


def advisories(text: str) -> list[str]:
    """Guide-rule warnings for a Grok idea — advisory, never blocking."""
    notes: list[str] = []
    table = atmospheres()
    atmo_hits = {
        m.group(1).lower()
        for m in _TOKEN.finditer(_TOKEN_JOIN.sub(" ", text))
        if m.group(1).lower() in table
    }
    if len(atmo_hits) > 3:
        notes.append(
            f"{len(atmo_hits)} atmosphere elements is a pile-up — the guide caps "
            "stacking at two or three."
        )
    asks = {m.group(0) for m in _RESOLUTION_ASKS.finditer(text)}
    if asks:
        notes.append(
            f"{', '.join(sorted(asks))} in a prompt wastes the generation — the "
            "model outputs 480p/720p and the Res control owns output size."
        )
    return notes
