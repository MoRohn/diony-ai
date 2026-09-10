"""Blueprint 31 — the Production Style: how the film's spoken words reach the master.

Three styles, one per session, chosen in the Director console beside the
narrator Voice:

- ``narrated`` (the default — exactly the pre-31 platform): every scripted or
  narrated line is recorded in post by ONE narrator against the assembled
  cut (Blueprint 28). Every location track is speechless by law; the words
  never ride a generator prompt.
- ``dialogue``: every line rides its clip's generation prompt, quoted
  verbatim, with the instruction that the character voices it on camera
  (or as a voice-over when the line is one). The provider's native audio IS
  the voice track; no post narrator runs for those clips.
- ``mix``: both instruments, each where it belongs. On-camera dialogue is
  voiced by the character in the clip; voice-over lines and one-shot
  narration are recorded in post. At the cut the two rails are balanced to
  one voice level, a spoken clip whose track never voiced its line is
  rescued by the narrator, and a track that carries a voice the script did
  not book is muted.

The style is resolved into a per-clip ``voicing`` ("post" | "generated") on
the contracts at draft/lock time — the compiler, review, speech guard, cue
sheet, and mix read the clip's own voicing, never the session's style, so a
style change can never silently reinterpret footage already shot.
"""

from __future__ import annotations

from diony_contracts.director import ProductionStyle

DEFAULT_STYLE = ProductionStyle.NARRATED

CATALOG: tuple[dict[str, str], ...] = (
    {
        "key": ProductionStyle.NARRATED.value,
        "label": "Narrated",
        "hint": (
            "one narrator records every line in post against the cut; "
            "location tracks stay speechless (Blueprint 28)"
        ),
    },
    {
        "key": ProductionStyle.DIALOGUE.value,
        "label": "Dialogue",
        "hint": (
            "the script lines ride each clip's prompt and the characters "
            "voice them in the clip; no post narrator"
        ),
    },
    {
        "key": ProductionStyle.MIX.value,
        "label": "Mix",
        "hint": (
            "characters speak on camera in the clip; voice-over is narrated "
            "in post; the cut balances, rescues, and mutes per the script"
        ),
    },
)


def catalog() -> list[dict[str, str]]:
    return [dict(entry) for entry in CATALOG]


def normalize_style(value: str | None) -> ProductionStyle:
    """A console/API style value as the platform knows it. '' resolves to
    the Narrated default; an unknown name raises ValueError."""
    chosen = str(value or "").strip().lower()
    if not chosen:
        return DEFAULT_STYLE
    try:
        return ProductionStyle(chosen)
    except ValueError as exc:
        raise ValueError(
            f"unknown production style {value!r} — choose one of: "
            + ", ".join(s.value for s in ProductionStyle)
        ) from exc


def voicing_for(style: ProductionStyle | str, *, on_camera: bool, off_screen: bool = False) -> str:
    """Who voices a line under this style: the post narrator or the
    provider inside the clip. ``on_camera`` is a scripted dialogue line
    (a character speaking in frame); False is a voice-over or a one-shot's
    narration — which Mix keeps on the narrator by doctrine. ``off_screen``
    (Blueprint 32) is a character's dialogue line spoken from outside the
    frame: still that character's line, so Mix voices it in the clip the
    way it voices their on-camera lines — one voice per character."""
    resolved = normalize_style(str(style))
    if resolved is ProductionStyle.DIALOGUE:
        return "generated"
    if resolved is ProductionStyle.MIX and (on_camera or off_screen):
        return "generated"
    return "post"


def describe(style: ProductionStyle | str) -> str:
    resolved = normalize_style(str(style))
    return next(e["label"] for e in CATALOG if e["key"] == resolved.value)
