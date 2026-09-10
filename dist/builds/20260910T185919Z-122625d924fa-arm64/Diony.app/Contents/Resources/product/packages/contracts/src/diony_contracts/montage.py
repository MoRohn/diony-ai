"""Montage contracts the Director cuts with: the program look and subtitle sidecars.

Pure module — no I/O, no ffmpeg. The Director gathers measurements and hands
them in; this module owns the thresholds and the schemas so a receipt written
on any desk means the same thing. Blueprint 24 records the studied
production-craft sources and the policy.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator

# --------------------------------------------------------------------------- program look


class LookProfile(StrEnum):
    NEUTRAL = "neutral"
    CINEMATIC_WARM = "cinematic_warm"
    CINEMATIC_COOL = "cinematic_cool"
    MOODY_DARK = "moody_dark"
    BRIGHT_CLEAN = "bright_clean"
    VINTAGE_FILM = "vintage_film"
    HIGH_CONTRAST = "high_contrast"


# Program-level looks. Corrective work is the Finish legalizer's; these are
# creative and deliberately restrained: saturation never above 1.10 so skin
# stays on the vector line, shadows lifted rather than crushed, highlights
# rolled rather than clipped. The limiter downstream re-asserts legal range.
_LOOK_CHAINS: dict[LookProfile, str] = {
    LookProfile.NEUTRAL: "",
    LookProfile.CINEMATIC_WARM: (
        "colorbalance=rs=0.05:gs=0.01:bs=-0.04:rh=0.04:bh=-0.03,"
        "curves=all='0/0.02 0.5/0.52 1/0.98',eq=contrast=1.04:saturation=1.06"
    ),
    LookProfile.CINEMATIC_COOL: (
        "colorbalance=rs=-0.02:bs=0.05:rh=0.03:bh=-0.04,"
        "curves=all='0/0.02 0.5/0.49 1/0.98',eq=contrast=1.05:saturation=1.03"
    ),
    LookProfile.MOODY_DARK: (
        "curves=all='0/0.04 0.5/0.46 1/0.96',eq=contrast=1.08:saturation=0.88:brightness=-0.02"
    ),
    LookProfile.BRIGHT_CLEAN: "curves=all='0/0.03 0.5/0.54 1/1',eq=saturation=1.08:brightness=0.015",
    LookProfile.VINTAGE_FILM: (
        "colorbalance=rs=0.04:gs=0.02:bs=-0.02,curves=all='0/0.05 0.5/0.5 1/0.95',"
        "eq=saturation=0.9:contrast=0.97"
    ),
    LookProfile.HIGH_CONTRAST: (
        "curves=all='0/0 0.25/0.2 0.5/0.5 0.75/0.8 1/1',eq=contrast=1.12:saturation=1.05"
    ),
}

LOOK_INTENSITY_MIN = 0.3
LOOK_INTENSITY_MAX = 1.0
# Generated footage is already stylised; a full-strength creative look over it
# reads as processing. 0.6 is the shipped default, never 1.0 by accident.
LOOK_INTENSITY_DEFAULT = 0.6


def _escape_filter_path(path: str) -> str:
    return path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def look_filter(
    profile: LookProfile, intensity: float = LOOK_INTENSITY_DEFAULT, *, lut_path: str | None = None
) -> str:
    """One ffmpeg filter segment (no labels) applying the program look at `intensity`.

    Empty when there is nothing to apply, so a neutral Bible costs no re-encode
    path change. A Bible LUT (`lut_asset_id`) is applied after the profile,
    both blended against the ungraded picture at the same intensity.
    """
    chain = _LOOK_CHAINS.get(profile, "")
    if lut_path:
        lut = f"lut3d=file='{_escape_filter_path(str(lut_path))}'"
        chain = f"{chain},{lut}" if chain else lut
    if not chain:
        return ""
    amount = max(LOOK_INTENSITY_MIN, min(LOOK_INTENSITY_MAX, float(intensity)))
    if amount >= LOOK_INTENSITY_MAX - 1e-6:
        return chain
    return (
        f"split=2[lookplate][looksrc];[looksrc]{chain}[lookgraded];"
        f"[lookplate][lookgraded]blend=all_mode=normal:all_opacity={amount:.3f}"
    )


# --------------------------------------------------------------------------- subtitles


SUBTITLE_MAX_CHARS_PER_LINE = 42
SUBTITLE_MAX_LINES = 2
SUBTITLE_MIN_SECONDS = 0.5
SUBTITLE_GAP_SECONDS = 0.08


class SubtitleCue(BaseModel):
    index: int = Field(ge=1)
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    speaker: str = ""
    text: str

    @model_validator(mode="after")
    def ordered(self) -> SubtitleCue:
        if self.end_seconds <= self.start_seconds:
            raise ValueError("a cue must end after it starts")
        return self


def wrap_cue_text(
    text: str, *, max_chars: int = SUBTITLE_MAX_CHARS_PER_LINE, max_lines: int = SUBTITLE_MAX_LINES
) -> str:
    words = " ".join(str(text or "").split()).split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= max_chars or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        head = lines[: max_lines - 1]
        head.append(" ".join(lines[max_lines - 1 :]))
        lines = head
    return "\n".join(lines)


def _srt_timestamp(seconds: float) -> str:
    total_ms = int(round(max(0.0, seconds) * 1000))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def render_srt(cues: Sequence[SubtitleCue]) -> str:
    blocks = [
        f"{cue.index}\n{_srt_timestamp(cue.start_seconds)} --> {_srt_timestamp(cue.end_seconds)}\n{cue.text}\n"
        for cue in cues
    ]
    return "\n".join(blocks)


def render_vtt(cues: Sequence[SubtitleCue]) -> str:
    lines = ["WEBVTT", ""]
    for cue in cues:
        start = _srt_timestamp(cue.start_seconds).replace(",", ".")
        end = _srt_timestamp(cue.end_seconds).replace(",", ".")
        text = f"<v {cue.speaker}>{cue.text}" if cue.speaker else cue.text
        lines.extend([str(cue.index), f"{start} --> {end}", text, ""])
    return "\n".join(lines)


# --------------------------------------------------------------------------- final review
