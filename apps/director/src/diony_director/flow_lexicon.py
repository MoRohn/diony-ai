"""Flow/Veo prompt templating: the WORLD + CAMERA lexicon.

Doctrine distilled from the 22-command Gemini Flow cheat sheet
(r/promptingmagic, 2026-08): a Flow prompt is WORLD + CAMERA + BEAT — pick
ONE visual world and ONE camera behavior, then a subject, one visible action,
motivated light, and an audio mix. Stacked styles are competing instructions.

This lexicon is Google-route ONLY. The Grok dialect never sees it: Grok
prompts keep their own REALISM ground truth, and a /shorthand token in a
Grok prompt passes through untouched as literal text.

Three surfaces:
- a per-session template (world + camera) picked in the Director UI and
  persisted in state.json — the world becomes the style clause of every Veo
  picture prompt; the camera directs single-shot chat proposals (scripted
  clips keep their authored shots — one camera idea per clip);
- inline /shorthand tokens (``/filmic:``, ``/dollyin:``, ``/orbit`` …) in
  ideas and authored shot lines, expanded at compile time on the Veo lane;
- a custom lexicon file at ``{cluster_root}/.cluster/flow_lexicon.json``
  (``{"worlds": {key: {"label", "clause", "hint"?}}, "cameras": {...}}``)
  merged over the built-ins, so a house style is one JSON entry, not a fork.
"""

from __future__ import annotations

import re
from typing import Any

# --- Part I: visual worlds (the style clause slot) ---------------------------

BASELINE_WORLD = "baseline"

WORLDS: dict[str, dict[str, str]] = {
    BASELINE_WORLD: {
        "label": "Baseline cinema",
        "hint": "the house default — neutral cinema grammar",
        "clause": (
            "Shot on a cinema camera, 35mm lens, shallow depth of field, "
            "motivated practical light, natural color, restrained camera movement."
        ),
    },
    "filmic": {
        "label": "Filmic 35mm",
        "hint": "photographed-movie texture, organic grain",
        "clause": (
            "Grounded 35mm motion-picture texture: authentic film grain, natural "
            "highlight roll-off, restrained color, believable skin, natural motion blur."
        ),
    },
    "commercial": {
        "label": "Commercial",
        "hint": "product films, launch spots, branded clarity",
        "clause": (
            "Clean polished advertising clarity: high-key polish, crisp edges, "
            "vibrant color separation, controlled reflections, elegant "
            "camera-ready surfaces."
        ),
    },
    "luxury": {
        "label": "Luxury",
        "hint": "rich blacks, gold light, immaculate materials",
        "clause": (
            "Quiet opulence: rich blacks, warm gold light, brushed metal and "
            "polished stone, deep shadows, immaculate surfaces, restrained movement."
        ),
    },
    "documentary": {
        "label": "Documentary",
        "hint": "raw, observed, discovered rather than staged",
        "clause": (
            "Raw observed realism: handheld camera, available light, imperfect "
            "focus pulls, cinema-vérité texture, nothing staged."
        ),
    },
    "vintagefilm": {
        "label": "Vintage film",
        "hint": "nostalgic 16mm/8mm imperfection, memory and period",
        "clause": (
            "Nostalgic 16mm film: warm color shifts, gate weave, soft halation, "
            "occasional dust, mild exposure flicker, no modern digital sharpness."
        ),
    },
    "scifi": {
        "label": "Sci-fi",
        "hint": "clean speculative design, engineered futures",
        "clause": (
            "Clean speculative design: engineered future architecture, blue-white "
            "practical lights, restrained holographics, plausible technology, "
            "elegant geometry, no visual clutter."
        ),
    },
    "cyberpunk": {
        "label": "Cyberpunk",
        "hint": "rain, density, neon, technological decay",
        "clause": (
            "Dense neon decay: rain-slick surfaces, steam, tangled cables, broken "
            "magenta and cyan signage, layered urban depth, worn technological textures."
        ),
    },
    "dreamy": {
        "label": "Dreamy",
        "hint": "soft focus, bloom, feeling over physics",
        "clause": (
            "Soft emotional unreality: pastel light, blooming highlights, gauzy "
            "diffusion, slow drifting motion, gentle surrealism."
        ),
    },
    "surreal": {
        "label": "Surreal",
        "hint": "impossible images with intentional symbolism",
        "clause": (
            "Intentional impossibility: physics-defying imagery that stays "
            "compositionally clear, poetic rather than chaotic, one readable "
            "visual metaphor."
        ),
    },
    "minimal": {
        "label": "Minimal",
        "hint": "negative space and one decisive idea",
        "clause": (
            "One decisive idea: large negative space, muted palette, strict "
            "geometry, a single subject against a clean field."
        ),
    },
    "epic": {
        "label": "Epic",
        "hint": "scale, stakes, an unforgettable horizon",
        "clause": (
            "Blockbuster scale: enormous environments that dwarf the character, "
            "dramatic horizon, restrained grandeur, high production value."
        ),
    },
}

# --- Part II: camera behaviors (the shot slot) -------------------------------

CAMERAS: dict[str, dict[str, str]] = {
    "cinematic": {
        "label": "Cinematic baseline",
        "hint": "balanced default coverage",
        "clause": (
            "Medium-wide shot at eye level, natural depth, controlled handheld "
            "stability, balanced motion blur"
        ),
    },
    "droneview": {
        "label": "Drone view",
        "hint": "reveal geography and route",
        "clause": (
            "High aerial view drifting steadily, strong horizon parallax, "
            "revealing the geography and route below"
        ),
    },
    "closeup": {
        "label": "Close-up",
        "hint": "turn detail into emotion",
        "clause": (
            "Tight close-up on the face, held focus on the micro-expression, "
            "background dissolving to soft bokeh"
        ),
    },
    "wideangle": {
        "label": "Wide angle",
        "hint": "the environment joins the action",
        "clause": (
            "Low wide-angle shot, deep perspective, the subject recognizable "
            "but small against the environment"
        ),
    },
    "orbit": {
        "label": "Orbit",
        "hint": "reveal the subject from every side",
        "clause": (
            "Smooth 180-degree orbit around the subject, subject locked at "
            "frame center while the background reveals"
        ),
    },
    "dollyin": {
        "label": "Dolly in",
        "hint": "pressure, intimacy, discovery",
        "clause": (
            "Slow physical dolly-in from medium-wide toward the face, background "
            "falling out of focus, ending tight"
        ),
    },
    "dollyout": {
        "label": "Dolly out",
        "hint": "context, isolation, consequence",
        "clause": (
            "Slow physical dolly-out from a tight detail, pulling back to reveal "
            "the full surroundings"
        ),
    },
    "tracking": {
        "label": "Tracking",
        "hint": "move with the subject",
        "clause": (
            "Side-profile tracking shot at the subject's exact speed, torso "
            "stable in frame, background streaking past"
        ),
    },
    "slowmotion": {
        "label": "Slow motion",
        "hint": "stretch one physical beat",
        "clause": (
            "Extreme slow motion on one physical beat, micro-detail readable — "
            "droplets, fabric, dust suspended mid-air"
        ),
    },
    "timelapse": {
        "label": "Timelapse",
        "hint": "change from a fixed viewpoint",
        "clause": (
            "Locked-camera timelapse, the environment transforming while any "
            "figure is a brief rhythm within it"
        ),
    },
    "hyperlapse": {
        "label": "Hyperlapse",
        "hint": "compressed time while the camera travels",
        "clause": (
            "Stabilized hyperlapse traveling forward through the space, time "
            "compressed, continuous forward translation"
        ),
    },
}

# Productive stacks: one style plus one camera with different jobs. Surfaced
# to the UI as hints — never auto-applied.
PAIRINGS: tuple[dict[str, str], ...] = (
    {"world": "documentary", "camera": "tracking", "why": "raw realism plus readable movement"},
    {"world": "luxury", "camera": "dollyin", "why": "material polish plus product intimacy"},
    {"world": "minimal", "camera": "dollyout", "why": "simple composition plus revealing context"},
    {"world": "cyberpunk", "camera": "droneview", "why": "dense world plus geographic orientation"},
    {"world": "dreamy", "camera": "slowmotion", "why": "diffused emotion plus suspended detail"},
    {"world": "epic", "camera": "wideangle", "why": "large-scale style plus environmental framing"},
)


# One parse per file version: every expand()/style_clause() call reads the
# overlay, and an N-clip compile would otherwise stat-and-parse dozens of
# times. Keyed on (mtime_ns, size) — a rewrite invalidates, a miss is cheap.
_CUSTOM_CACHE: dict[str, Any] = {"sig": (), "value": None}


def _custom_lexicon() -> dict[str, dict[str, dict[str, str]]]:
    """User-defined worlds/cameras, merged over the built-ins. A corrupt or
    absent file — or any malformed entry inside it — is simply an empty
    overlay — customization never breaks boot or a compile."""
    import json

    from diony_contracts.cluster import cluster_root

    path = cluster_root() / ".cluster" / "flow_lexicon.json"
    try:
        stat = path.stat()
        sig: tuple = (str(path), stat.st_mtime_ns, stat.st_size)
    except OSError:
        sig = (str(path), "absent")
    if _CUSTOM_CACHE["sig"] == sig and _CUSTOM_CACHE["value"] is not None:
        return _CUSTOM_CACHE["value"]
    try:
        raw = json.loads(path.read_text())
    except Exception:  # noqa: BLE001 - missing/corrupt file → no overlay
        raw = {}
    out: dict[str, dict[str, dict[str, str]]] = {"worlds": {}, "cameras": {}}
    # A key must live in ONE family: a custom world named "orbit" would fight
    # the built-in camera at expansion time (cameras win), styling nothing.
    other_family = {"worlds": CAMERAS, "cameras": WORLDS}
    for family in ("worlds", "cameras"):
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
    cross = set(out["worlds"]) & set(out["cameras"])
    for slug in cross:
        del out["cameras"][slug]
    _CUSTOM_CACHE["sig"] = sig
    _CUSTOM_CACHE["value"] = out
    return out


def worlds() -> dict[str, dict[str, str]]:
    return {**WORLDS, **_custom_lexicon()["worlds"]}


def cameras() -> dict[str, dict[str, str]]:
    return {**CAMERAS, **_custom_lexicon()["cameras"]}


def catalog() -> dict[str, Any]:
    """The UI payload: ordered entries, pairing hints, and where custom
    entries live. Order is doctrine order — worlds then cameras as the
    cheat sheet teaches them, custom entries after."""

    def _rows(table: dict[str, dict[str, str]]) -> list[dict[str, str]]:
        return [
            {"key": key, "label": entry["label"], "hint": entry["hint"], "clause": entry["clause"]}
            for key, entry in table.items()
        ]

    from diony_contracts.cluster import cluster_root

    return {
        "formula": "WORLD + CAMERA + BEAT — one visual world, one camera behavior, one visible action",
        "default_world": BASELINE_WORLD,
        "worlds": _rows(worlds()),
        "cameras": _rows(cameras()),
        "pairings": list(PAIRINGS),
        "custom_path": str(cluster_root() / ".cluster" / "flow_lexicon.json"),
        "google_route_only": True,
    }


# --- Per-session template ----------------------------------------------------

_TEMPLATES: dict[str, dict[str, str]] = {}


def reset_templates() -> None:
    _TEMPLATES.clear()


def template_for(sid: str) -> dict[str, str]:
    stored = _TEMPLATES.get(sid) or {}
    return {
        "world": stored.get("world") or BASELINE_WORLD,
        "camera": stored.get("camera") or "",
    }


def set_template(sid: str, *, world: str = "", camera: str = "") -> dict[str, str]:
    world_key = (world or BASELINE_WORLD).strip().lower()
    camera_key = (camera or "").strip().lower()
    if world_key not in worlds():
        raise ValueError(
            f"unknown Flow world {world!r} — expected one of {', '.join(sorted(worlds()))}"
        )
    if camera_key and camera_key not in cameras():
        raise ValueError(
            f"unknown Flow camera {camera!r} — expected one of {', '.join(sorted(cameras()))}"
        )
    _TEMPLATES[sid] = {"world": world_key, "camera": camera_key}
    return template_for(sid)


def restore_template(sid: str, doc: Any) -> None:
    """Rehydrate from state.json. An entry naming a since-removed custom key
    falls back to defaults rather than bricking the session."""
    if not isinstance(doc, dict):
        return
    try:
        set_template(sid, world=str(doc.get("world", "")), camera=str(doc.get("camera", "")))
    except ValueError:
        pass


def style_clause(sid: str, *, override_world: str = "") -> str:
    """The world clause for a Veo prompt: an inline /world token on the shot
    beats the session template — per-shot authorship always wins. A stored
    world whose custom entry has since been deleted falls back to baseline;
    a missing lexicon entry must never fail a compile."""
    table = worlds()
    if override_world and override_world in table:
        return table[override_world]["clause"]
    entry = table.get(template_for(sid)["world"]) or WORLDS[BASELINE_WORLD]
    return entry["clause"]


def session_camera_clause(sid: str) -> str:
    key = template_for(sid)["camera"]
    return cameras()[key]["clause"] if key and key in cameras() else ""


# Authored camera prose means the author already directed the camera; a
# session template camera stacked on top is two competing behaviors in one
# paid prompt. Deliberately broad: a false hit merely leaves the template
# off one proposal, a miss ships a contradiction. Same doctrine as
# grok_lexicon.mentions_camera — kept per-module so each route's dialect
# stays self-contained.
_CAMERA_PROSE = re.compile(
    r"\b(?:camera|shot|close-?up|wide[- ]angle|pov|dolly|dollies|pan(?:s|ning)?|"
    r"orbit(?:s|ing)?|zoom(?:s|ing)?|tracking|crane|handheld|aerial|drone|"
    r"timelapse|hyperlapse|slow[- ]motion)\b",
    re.IGNORECASE,
)


def mentions_camera(text: str) -> bool:
    """Whether the text already carries camera direction in prose."""
    return bool(_CAMERA_PROSE.search(text))


# --- Inline /shorthand expansion --------------------------------------------

# A token is a /word at the start of the fragment or after whitespace/"("/"+"
# — never mid-word, so prose like "day/night cycle" or "pan/tracking hybrid"
# is not shorthand. Matching is case-insensitive ("/Closeup" works); table
# keys stay lowercase.
# :? only — a \s*:? here would eat the separating space between adjacent
# tokens ("/locked /dust" → "…movingdust…").
_TOKEN = re.compile(r"(?:^|(?<=[\s(+]))/([A-Za-z][A-Za-z0-9_-]*):?")
_TOKEN_JOIN = re.compile(r"\s*\+\s*(?=/)")


def expand(text: str) -> tuple[str, str, bool]:
    """Expand cheat-sheet shorthand in one prompt fragment.

    Camera tokens are replaced inline with their clause. World tokens are
    EXTRACTED (a world is a style, not a shot — it moves to the style slot);
    the last world named wins, per "one visual world per clip". Unknown
    tokens pass through untouched. Returns (expanded_text, world_key,
    camera_token_seen).
    """
    if "/" not in text:
        return text, "", False
    world_table, camera_table = worlds(), cameras()
    known = set(world_table) | set(camera_table)
    found_world = ""
    found_camera = False

    def _sub(match: re.Match[str]) -> str:
        nonlocal found_world, found_camera
        key = match.group(1).lower()
        if key in camera_table:
            found_camera = True
            clause = camera_table[key]["clause"]
            # Two adjacent camera tokens must not fuse into one run-on:
            # a comma lands between their clauses. A prose continuation
            # stays seamless — no comma.
            following = re.match(
                r"\s+/([A-Za-z][A-Za-z0-9_-]*)", match.string[match.end():]
            )
            if following and following.group(1).lower() in known:
                return clause + ","
            return clause
        if key in world_table:
            found_world = key
            return ""
        return match.group(0)

    # "/filmic + /dollyin:" — the "+" is token grammar, not prose.
    normalized = _TOKEN_JOIN.sub(" ", text)
    out = _TOKEN.sub(_sub, normalized)
    out = re.sub(r",\s*([,.;:!?)])", r"\1", out)  # no comma debris before punctuation
    out = re.sub(r"\s+([,.;:!?])", r"\1", out)  # no debris where a world stood
    out = re.sub(r"\s{2,}", " ", out).strip().lstrip(",.;:!? ").strip().rstrip(",;: ")
    return out, found_world, found_camera


def unknown_tokens(text: str) -> list[str]:
    """Slash tokens that look like shorthand but match no lexicon entry —
    almost always a typo about to ride a paid prompt as literal text."""
    if "/" not in text:
        return []
    known = set(worlds()) | set(cameras())
    seen: list[str] = []
    for match in _TOKEN.finditer(_TOKEN_JOIN.sub(" ", text)):
        key = match.group(1).lower()
        if key not in known and f"/{key}" not in seen:
            seen.append(f"/{key}")
    return seen


def advisories(text: str) -> list[str]:
    """Cheat-sheet-rule warnings for a Flow idea — advisory, never blocking.
    Stacked worlds are competing instructions and expand() keeps only the
    last; the author deserves to hear which one dropped before the money
    pass, not discover it in the print."""
    if "/" not in text:
        return []
    notes: list[str] = []
    world_table, camera_table = worlds(), cameras()
    world_hits: list[str] = []
    camera_hits: list[str] = []
    for match in _TOKEN.finditer(_TOKEN_JOIN.sub(" ", text)):
        key = match.group(1).lower()
        if key in world_table:
            world_hits.append(key)
        elif key in camera_table and key not in camera_hits:
            camera_hits.append(key)
    if len(set(world_hits)) > 1:
        winner = world_hits[-1]
        dropped = sorted({w for w in world_hits if w != winner})
        notes.append(
            f"one visual world per clip — /{winner} wins and "
            f"{', '.join('/' + w for w in dropped)} is dropped."
        )
    if len(camera_hits) > 1:
        notes.append(
            f"{len(camera_hits)} camera behaviors stack into competing "
            "instructions — the cheat sheet books one camera behavior per clip."
        )
    return notes
