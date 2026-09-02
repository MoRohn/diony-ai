"""Blueprint 29 — the Look Bible and the style frame.

A film whose shots each looked drawn by a different studio (s-e790706066,
2026-09-02: flat infographic → photoreal sedan → paper-cutout towers → sepia
clay town → sci-fi control room → isometric city, in one twelve-clip chain)
had no film-wide statement of its look anywhere in the pipeline: the medium
was re-derived per clip from loose style words, the setting fields were
written independently per setting, three places hard-coded photorealism, and
the only visual input a chained clip ever received was the previous clip's
last frame — something to morph away from whenever the subject changed.

This module owns the two answers:

- the **Look Bible** (``diony_contracts.production.LookBible``): one medium,
  palette, rendering language, lens/grade, and lighting philosophy, derived
  once by the master derivation (brain) or from the idea's tells (template),
  and carried VERBATIM — byte-identical — on every clip prompt, plate, sheet,
  and style frame of the production;
- the **style frame**: at a scene change inside a chained one-shot, the next
  clip does not open from the previous clip's last frame (a different subject
  the video model would have to morph); it opens from a STILL composed by the
  image lane in the established look, with the look's reference images as
  its references. The video model only animates; the still model holds the
  artist.
"""

from __future__ import annotations

import re

from diony_contracts.production import LookBible, look_is_photoreal

# The brain's output-contract line for the look (Blueprint 29). Appended to
# the master template's OUTPUT CONTRACT by master_prompt.
LOOK_CONTRACT = (
    "look ({medium, render_language, palette, lens_grade, lighting_system, "
    "negatives (list of 2–4 short phrases)}) — the film-wide LOOK BIBLE derived "
    "from visual_style and the idea: medium is the one medium every shot is made "
    "in (e.g. \"2.5D animated explainer, flat shaded with soft paper texture\" or "
    "\"photoreal live action on a digital cinema camera\"); render_language how "
    "surfaces, edges, and light are drawn; palette the colours and their "
    "discipline; lens_grade the lens, depth of field, contrast, and grade the whole "
    "film shares; lighting_system the lighting philosophy (not one scene's "
    "sources); negatives what the look must never become. This block is appended "
    "VERBATIM to every clip prompt of the film, so the prompt must not re-derive, "
    "restate, or contradict it,"
)

# --- template derivation (brain off) ----------------------------------------

_MEDIUM_TELLS: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (
        ("2.5d", "explainer", "motion graphic", "infographic"),
        "2.5D animated explainer, flat-shaded vector shapes with soft paper texture",
        "clean geometric shapes, thin consistent outlines, soft drop shadows, no photographic textures",
    ),
    (
        ("anime",),
        "hand-drawn anime, cel-shaded",
        "cel shading with clean ink lines, painted backgrounds, flat colour fills",
    ),
    (
        ("claymation", "clay", "stop-motion", "stop motion", "puppet"),
        "stop-motion claymation",
        "sculpted clay surfaces with fingerprints and matte texture, miniature-set depth",
    ),
    (
        ("pixel",),
        "pixel art animation",
        "hard-edged pixels on a fixed grid, limited palette, no anti-aliasing",
    ),
    (
        ("watercolor", "watercolour", "painterly", "painted"),
        "painterly animation on watercolour washes",
        "visible brush texture, bleeding edges, paper grain, soft painted light",
    ),
    (
        ("isometric", "low-poly", "low poly"),
        "isometric low-poly 3D animation",
        "faceted low-poly geometry, flat shading, soft ambient occlusion, no photographic materials",
    ),
    (
        ("comic", "graphic novel", "ink", "line art", "sketch"),
        "inked comic-book animation",
        "bold ink outlines, halftone shading, flat colour fills, paper grain",
    ),
    (
        ("animated", "animation", "cartoon", "pixar", "toon", "cgi", "3d render"),
        "stylized 3D animation",
        "soft subsurface materials, rounded forms, painterly lighting, no photographic footage",
    ),
    (
        ("documentary", "observational", "vérité", "verite"),
        "photoreal documentary live action",
        "natural skin texture, true blacks, motivated practical light, filmic contrast",
    ),
)

_PALETTE_TELLS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("neon", "cyberpunk"), "saturated neon magenta and cyan against deep black"),
    (("night", "midnight", "after dark", "moody"), "muted navy and charcoal with one warm amber accent, low saturation"),
    (("sunset", "golden hour", "dusk"), "warm amber and rose against cool violet shadow"),
    (("desert", "sand", "sun"), "bleached sand, ochre, and pale sky, warm and dry"),
    (("forest", "rain", "overcast", "grey"), "desaturated greens and slate grey, soft contrast"),
    (("minimal", "clean"), "restrained two-tone palette on a clean field, large negative space"),
)

_LENS_DEFAULT_PHOTOREAL = "35–50mm equivalents, shallow depth of field, filmic contrast, gentle grain"
_LENS_DEFAULT_STYLIZED = "35mm-equivalent framing, selective focus, soft filmic contrast, subtle grain"


def template_look(idea: str, visual_style: str = "") -> LookBible:
    """The deterministic look for a brainless setup, from the idea's tells.
    Photoreal by default — the house ground truth every prompt carried before
    the Look Bible — unless the idea names a medium."""
    lowered = f"{visual_style} {idea}".lower()
    medium = "photoreal live action on a digital cinema camera"
    render = "natural skin texture, true blacks, motivated practical light, filmic contrast"
    for tells, mapped_medium, mapped_render in _MEDIUM_TELLS:
        if any(t in lowered for t in tells):
            medium, render = mapped_medium, mapped_render
            break
    palette = "restrained natural palette, motivated contrast"
    for tells, mapped in _PALETTE_TELLS:
        if any(t in lowered for t in tells):
            palette = mapped
            break
    photoreal = look_is_photoreal(medium, render)
    negatives = (
        ["no illustration or CGI gloss", "no plastic skin", "no colour shifts between shots"]
        if photoreal
        else ["no photoreal footage", "no 3D render gloss", "no change of medium between shots"]
    )
    return LookBible(
        medium=medium,
        render_language=render,
        palette=palette,
        lens_grade=_LENS_DEFAULT_PHOTOREAL if photoreal else _LENS_DEFAULT_STYLIZED,
        lighting_system="one motivated key per scene with soft ambient fill, consistent colour temperature logic",
        negatives=negatives,
        source="template",
    )


# --- brain parse --------------------------------------------------------------


def _clean(value) -> str:
    if isinstance(value, (list, tuple)):
        value = "; ".join(str(v) for v in value if str(v).strip())
    return " ".join(str(value or "").split()).strip()


def parse_look(raw, *, idea: str, visual_style: str, warnings: list[str]) -> LookBible:
    """The brain's ``look`` object, parsed strictly and defaulted loudly: a
    missing block is derived from the visual style and the idea (named in
    warnings) — a film never ships without a look."""
    if not isinstance(raw, dict) or not _clean(raw.get("medium")):
        warnings.append("master: brain omitted the look bible — derived from visual_style")
        look = template_look(idea, visual_style)
        look.source = "brain"
        return look
    negatives = raw.get("negatives")
    if isinstance(negatives, str):
        negatives = [part for part in re.split(r"[;\n]", negatives)]
    if not isinstance(negatives, list):
        negatives = []
    look = LookBible(
        medium=_clean(raw.get("medium")),
        render_language=_clean(raw.get("render_language")),
        palette=_clean(raw.get("palette")),
        lens_grade=_clean(raw.get("lens_grade")),
        lighting_system=_clean(raw.get("lighting_system")),
        negatives=[_clean(n).rstrip(".") for n in negatives if _clean(n)][:4],
        source="brain",
    )
    if not look.palette:
        warnings.append("master: brain omitted the look palette")
    return look


# --- style frames -------------------------------------------------------------

STYLE_FRAME_LEAD = "A single still frame — the opening frame of the next shot:"


def style_frame_prompt(opening: str, look: LookBible, *, continues_from: str = "") -> str:
    """The image-lane prompt for a scene-change opener: the clip's first frame
    composed in the film's look, matched to the reference images (the look's
    anchors and the previous clip's last frame)."""
    opening = " ".join((opening or "the scene at rest, composed for the shot to come").split()).rstrip(".")
    parts = [f"{STYLE_FRAME_LEAD} {opening[:1].lower()}{opening[1:]}."]
    if continues_from:
        parts.append(
            f"It follows a shot that ended on {continues_from.rstrip('.')}; "
            "this is a new place or subject, cut to directly."
        )
    parts.append(look.block())
    parts.append(
        "Match the reference images exactly for medium, palette, line weight, "
        "shading, texture, and grain — the same studio's hand, as if the same "
        "artist drew this frame in the same session. No text, no captions, "
        "no watermark."
    )
    return " ".join(p for p in parts if p)


def describe(look: LookBible | None) -> str:
    """One reply-line description of the look."""
    if look is None or not look.medium:
        return ""
    return look.medium


__all__ = [
    "LOOK_CONTRACT",
    "STYLE_FRAME_LEAD",
    "describe",
    "parse_look",
    "style_frame_prompt",
    "template_look",
]
