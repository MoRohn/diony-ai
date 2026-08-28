"""Score beat grid and cut alignment contracts (Blueprint 24 §12).

Picture is frame-locked in Diony, so the score moves to the cuts: a cue's
source in-point is shifted by less than one bar so its downbeats land on the
hero cuts it plays under. The analysis is a measurement (`BeatGrid`) and the
alignment a pure decision (`align_cue_to_cuts`); the producer supplies the
audio evidence and applies the shift in the mix graph.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Sequence

from pydantic import BaseModel, Field

BEAT_GRID_VERSION = "diony-beat-grid-v1"
ALIGNMENT_TOLERANCE_MS = 60.0
BEATS_PER_BAR = 4


class EnergyLevel(StrEnum):
    VOID = "void"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class EnergyPhase(BaseModel):
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    level: EnergyLevel


class BeatGrid(BaseModel):
    """What a cue measured: tempo, beat and downbeat instants, energy phases.

    `rhythmic` is the trust boundary: energy phases are always usable, the
    beat grid only when the tracker found a stable pulse. A non-rhythmic cue
    is placed as phrase flow, never hard-cut to an imposed metronome.
    """

    version: str = BEAT_GRID_VERSION
    duration_seconds: float = Field(default=0.0, ge=0)
    bpm: float | None = Field(default=None, gt=0)
    rhythmic: bool = False
    confidence: float = Field(default=0.0, ge=0, le=1)
    beats_per_bar: int = Field(default=BEATS_PER_BAR, ge=1, le=12)
    beat_seconds: list[float] = Field(default_factory=list)
    downbeat_seconds: list[float] = Field(default_factory=list)
    energy_phases: list[EnergyPhase] = Field(default_factory=list)
    analyzer: str = "numpy"

    @property
    def bar_seconds(self) -> float:
        if not self.bpm:
            return 0.0
        return 60.0 / self.bpm * self.beats_per_bar


class AlignmentMode(StrEnum):
    BEAT_CUT = "beat_cut"
    PHRASE_FLOW = "phrase_flow"
    UNALIGNED = "unaligned"


class AlignedCut(BaseModel):
    cut_seconds: float = Field(ge=0)
    downbeat_seconds: float = Field(ge=0)
    error_ms: float = Field(ge=0)
    # "downbeat" when the cut sits on a bar line, "beat" when on a beat only.
    grid: str = "downbeat"


class ScoreAlignment(BaseModel):
    """One score cue's placement decision against the cuts it plays under."""

    cue_id: str
    role: str = "score"
    mode: AlignmentMode
    cue_start_seconds: float = Field(ge=0)
    cue_duration_seconds: float = Field(gt=0)
    source_shift_seconds: float = Field(default=0.0, ge=0)
    hero_cut_seconds: list[float] = Field(default_factory=list)
    aligned_cuts: list[AlignedCut] = Field(default_factory=list)
    missed_cuts: int = Field(default=0, ge=0)
    max_error_ms: float | None = Field(default=None, ge=0)
    bpm: float | None = Field(default=None, gt=0)
    reason: str = ""


def _nearest(value: float, candidates: Sequence[float]) -> float | None:
    best: float | None = None
    for candidate in candidates:
        if best is None or abs(candidate - value) < abs(best - value):
            best = candidate
    return best


def align_cue_to_cuts(
    grid: BeatGrid,
    *,
    cue_id: str,
    cue_start_seconds: float,
    cue_duration_seconds: float,
    cut_seconds: Sequence[float],
    source_duration_seconds: float | None = None,
    tolerance_ms: float = ALIGNMENT_TOLERANCE_MS,
    role: str = "score",
) -> ScoreAlignment:
    """Choose the in-point shift (< one bar) that lands the most hero cuts on downbeats.

    Only cuts strictly inside the cue's span count; the cue's own start is a
    cut by definition and is where the music enters. The candidate shifts are
    every downbeat's offset from every in-span cut. The winner lands the most
    cuts on downbeats within tolerance, then the most on beats, then the
    smallest summed error, ties to the smallest shift. Cuts a non-integer
    number of bars apart cannot all land — the hero cut the music enters on
    is what matters; the misses are counted, never hidden. A shift that would
    run the source out is capped, and a cue with no rhythm or no in-span hero
    cut stays phrase flow, unshifted.
    """
    inside = sorted(
        cut
        for cut in cut_seconds
        if cue_start_seconds + 0.25 < cut < cue_start_seconds + cue_duration_seconds - 0.25
    )
    base = dict(
        cue_id=cue_id,
        role=role,
        cue_start_seconds=round(cue_start_seconds, 6),
        cue_duration_seconds=round(cue_duration_seconds, 6),
        hero_cut_seconds=[round(cut, 6) for cut in inside],
        bpm=grid.bpm,
    )
    if not grid.rhythmic or not grid.downbeat_seconds or not grid.bpm:
        return ScoreAlignment(mode=AlignmentMode.PHRASE_FLOW, reason="no stable pulse measured; placed as phrase flow", **base)
    if not inside:
        return ScoreAlignment(mode=AlignmentMode.PHRASE_FLOW, reason="no hero cut inside the cue span", **base)
    bar = grid.bar_seconds
    headroom = (
        max(0.0, float(source_duration_seconds) - cue_duration_seconds)
        if source_duration_seconds is not None
        else bar
    )
    max_shift = min(bar, headroom)
    relative = [cut - cue_start_seconds for cut in inside]
    candidates: set[float] = {0.0}
    for downbeat in grid.downbeat_seconds:
        for cut in relative:
            shift = downbeat - cut
            while shift < 0:
                shift += bar
            shift %= bar
            if shift <= max_shift + 1e-9:
                candidates.add(round(shift, 6))
    beats = grid.beat_seconds or grid.downbeat_seconds
    tolerance = tolerance_ms / 1000.0
    best_key: tuple[int, int, float, float] | None = None
    best_shift = 0.0
    best_rows: list[AlignedCut] = []
    best_errors: list[float] = []
    for shift in sorted(candidates):
        shifted_downbeats = [beat - shift for beat in grid.downbeat_seconds]
        shifted_beats = [beat - shift for beat in beats]
        rows: list[AlignedCut] = []
        errors: list[float] = []
        on_downbeat = 0
        on_beat = 0
        for cut, rel in zip(inside, relative):
            nearest_down = _nearest(rel, shifted_downbeats)
            nearest_beat = _nearest(rel, shifted_beats)
            if nearest_down is None:
                continue
            error_down = abs(rel - nearest_down)
            error_beat = abs(rel - nearest_beat) if nearest_beat is not None else error_down
            errors.append(error_down * 1000.0)
            if error_down <= tolerance:
                on_downbeat += 1
                rows.append(AlignedCut(cut_seconds=round(cut, 6), downbeat_seconds=round(cue_start_seconds + nearest_down, 6), error_ms=round(error_down * 1000.0, 3), grid="downbeat"))
            elif error_beat <= tolerance and nearest_beat is not None:
                on_beat += 1
                rows.append(AlignedCut(cut_seconds=round(cut, 6), downbeat_seconds=round(cue_start_seconds + nearest_beat, 6), error_ms=round(error_beat * 1000.0, 3), grid="beat"))
        key = (on_downbeat, on_beat, -sum(errors), -shift)
        if best_key is None or key > best_key:
            best_key, best_shift, best_rows, best_errors = key, shift, rows, errors
    landed_down = sum(1 for row in best_rows if row.grid == "downbeat")
    landed_beat = len(best_rows) - landed_down
    missed = len(inside) - len(best_rows)
    max_error = max(best_errors) if best_errors else None
    if landed_down == 0:
        return ScoreAlignment(
            mode=AlignmentMode.PHRASE_FLOW,
            missed_cuts=len(inside),
            max_error_ms=round(max_error, 3) if max_error is not None else None,
            reason=(
                f"no shift lands a hero cut on a downbeat within {tolerance_ms:.0f} ms "
                f"(best {max_error:.0f} ms); placed as phrase flow"
                if max_error is not None
                else "no downbeat could serve the hero cuts; placed as phrase flow"
            ),
            **base,
        )
    reason = (
        f"{landed_down} of {len(inside)} hero cut(s) on downbeats"
        + (f", {landed_beat} on beats" if landed_beat else "")
        + (f", {missed} missed" if missed else "")
        + f" at {grid.bpm:.1f} BPM (shift {best_shift:.3f}s)"
    )
    return ScoreAlignment(
        mode=AlignmentMode.BEAT_CUT,
        source_shift_seconds=round(best_shift, 6),
        aligned_cuts=best_rows,
        missed_cuts=missed,
        max_error_ms=round(max_error, 3) if max_error is not None else None,
        reason=reason,
        **base,
    )


__all__ = [
    "ALIGNMENT_TOLERANCE_MS",
    "AlignedCut",
    "AlignmentMode",
    "BEAT_GRID_VERSION",
    "BeatGrid",
    "EnergyLevel",
    "EnergyPhase",
    "ScoreAlignment",
    "align_cue_to_cuts",
]
