"""Local beat-grid analysis of a score cue (Blueprint 24 §12).

Pure numpy on ffmpeg-decoded mono PCM: an onset envelope from spectral flux,
tempo from the envelope's autocorrelation in the 55–200 BPM band, beat phase
by maximising onset energy on the grid, downbeats by the low band's energy
across the four candidate phases, and energy phases from one-second RMS.
When `librosa` is importable on the host it supplies the tempo and beats
instead; the envelope, downbeats, and energy stay Diony's. Grids are cached
beside the vault by the cue's bytes so a re-mix never re-analyses.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from diony_contracts.score import BEAT_GRID_VERSION, BeatGrid, EnergyLevel, EnergyPhase
from diony_producer.vault import vault_root

SAMPLE_RATE = 22_050
FRAME = 2048
HOP = 512
BPM_MIN = 55.0
BPM_MAX = 200.0
# Autocorrelation peak prominence at which a pulse is trusted; below it the
# cue is phrase flow. Measured on click tracks (≈5) versus pads (≈1.2).
RHYTHMIC_PROMINENCE = 1.8
# Onset energy on the chosen grid over the mean everywhere; a pad or noise
# floor sits near 1, a real pulse far above it.
PULSE_STRENGTH_MIN = 3.0
LOW_BAND_HZ = 150.0
TEMPO_PRIOR_BPM = 120.0


def _decode(path: Path):
    import numpy as np

    from diony_producer.speech_audio import decode_pcm

    samples = decode_pcm(path, sample_rate=SAMPLE_RATE)
    return np.asarray(samples, dtype=np.float32)


def onset_envelope(signal, sample_rate: int = SAMPLE_RATE):
    """Half-wave-rectified spectral flux per hop, normalised to [0, 1]."""
    import numpy as np

    if len(signal) < FRAME * 2:
        return np.zeros(1, dtype=np.float32), np.zeros(1, dtype=np.float32)
    window = np.hanning(FRAME).astype(np.float32)
    count = 1 + (len(signal) - FRAME) // HOP
    frames = np.lib.stride_tricks.as_strided(
        signal,
        shape=(count, FRAME),
        strides=(signal.strides[0] * HOP, signal.strides[0]),
        writeable=False,
    )
    spectrum = np.abs(np.fft.rfft(frames * window, axis=1)).astype(np.float32)
    magnitude = np.log1p(spectrum)
    flux = np.diff(magnitude, axis=0, prepend=magnitude[:1])
    envelope = np.maximum(flux, 0.0).sum(axis=1)
    low_bins = int(LOW_BAND_HZ / (sample_rate / FRAME)) + 1
    low_energy = (spectrum[:, :low_bins] ** 2).sum(axis=1)
    peak = float(envelope.max()) if envelope.size else 0.0
    if peak > 0:
        envelope = envelope / peak
    return envelope.astype(np.float32), low_energy.astype(np.float32)


def estimate_tempo(envelope, sample_rate: int = SAMPLE_RATE) -> tuple[float | None, float]:
    """(bpm, prominence) from the onset envelope's autocorrelation."""
    import numpy as np

    if envelope.size < 8:
        return None, 0.0
    centred = envelope - envelope.mean()
    if not np.any(centred):
        return None, 0.0
    hops_per_second = sample_rate / HOP
    lag_min = int(hops_per_second * 60.0 / BPM_MAX)
    lag_max = int(hops_per_second * 60.0 / BPM_MIN)
    if lag_max >= envelope.size:
        lag_max = envelope.size - 1
    if lag_min < 1 or lag_max <= lag_min:
        return None, 0.0
    spectrum = np.fft.rfft(centred, n=2 * envelope.size)
    autocorr = np.fft.irfft(spectrum * np.conj(spectrum))[: envelope.size]
    if autocorr[0] <= 0:
        return None, 0.0
    autocorr = autocorr / autocorr[0]
    band = autocorr[lag_min : lag_max + 1]
    baseline = float(np.mean(np.abs(band))) or 1e-6
    # Octave resolution: a beat lag that falls between two hop bins splits
    # its peak, and a half-tempo lag always correlates too. Refine every local
    # maximum parabolically and weight it by a log-Gaussian prior centred on
    # 120 BPM (one octave sigma), the way beat trackers choose the felt pulse.
    best: tuple[float, float, float] | None = None  # (weighted, peak, lag)
    for lag in range(max(lag_min, 1), min(lag_max, autocorr.size - 2) + 1):
        y0, y1, y2 = float(autocorr[lag - 1]), float(autocorr[lag]), float(autocorr[lag + 1])
        if y1 < y0 or y1 < y2 or y1 <= 0:
            continue
        denominator = y0 - 2.0 * y1 + y2
        delta = 0.5 * (y0 - y2) / denominator if denominator != 0 else 0.0
        delta = max(-0.5, min(0.5, delta))
        peak = y1 - 0.25 * (y0 - y2) * delta
        refined = lag + delta
        bpm = 60.0 * hops_per_second / refined
        prior = float(np.exp(-0.5 * (np.log2(bpm / TEMPO_PRIOR_BPM)) ** 2))
        weighted = peak * prior
        if best is None or weighted > best[0]:
            best = (weighted, peak, refined)
    if best is None:
        return None, 0.0
    _weighted, peak, refined = best
    prominence = peak / baseline if peak > 0 else 0.0
    bpm = 60.0 * hops_per_second / refined
    return round(bpm, 2), round(prominence, 3)


def _frame_center_offset(sample_rate: int) -> float:
    """Analysis frames are labelled by their centre: an onset registers in the
    first frame whose window reaches it, ~FRAME/2 samples before its true time."""
    return (FRAME / 2.0) / sample_rate


def beat_times(envelope, bpm: float, sample_rate: int = SAMPLE_RATE) -> tuple[list[float], float]:
    """(beat instants, pulse strength): the grid phase collecting the most onset energy.

    Pulse strength is the mean onset energy on the chosen grid over the mean
    everywhere; a click track measures ~20, a pad or noise ~1.
    """
    import numpy as np

    hops_per_second = sample_rate / HOP
    period = hops_per_second * 60.0 / bpm
    if period < 1:
        return [], 0.0
    best_phase = 0.0
    best_score = -1.0
    steps = max(4, int(period))
    for step in range(steps):
        phase = period * step / steps
        positions = np.arange(phase, envelope.size, period)
        score = float(envelope[positions.astype(int)].sum())
        if score > best_score:
            best_score, best_phase = score, phase
    positions = np.arange(best_phase, envelope.size, period)
    on_grid = float(envelope[positions.astype(int)].mean()) if positions.size else 0.0
    overall = float(envelope.mean()) or 1e-6
    strength = on_grid / overall
    center = _frame_center_offset(sample_rate)
    return [round(float(position) / hops_per_second + center, 4) for position in positions], round(strength, 3)


def downbeat_times(beats: list[float], low_energy, beats_per_bar: int = 4, sample_rate: int = SAMPLE_RATE) -> list[float]:
    """Every fourth beat, phased where the low band hits hardest."""

    if not beats:
        return []
    hops_per_second = sample_rate / HOP
    center = _frame_center_offset(sample_rate)
    best_offset = 0
    best_score = -1.0
    for offset in range(min(beats_per_bar, len(beats))):
        score = 0.0
        for beat in beats[offset::beats_per_bar]:
            index = int(round((beat - center) * hops_per_second))
            if 0 <= index < low_energy.size:
                score += float(low_energy[index])
        if score > best_score:
            best_score, best_offset = score, offset
    return beats[best_offset::beats_per_bar]


def energy_phases(signal, sample_rate: int = SAMPLE_RATE) -> list[EnergyPhase]:
    import numpy as np

    if signal.size == 0:
        return []
    seconds = int(np.ceil(signal.size / sample_rate))
    rms = np.array(
        [float(np.sqrt(np.mean(np.square(signal[i * sample_rate : (i + 1) * sample_rate])))) if signal[i * sample_rate : (i + 1) * sample_rate].size else 0.0 for i in range(seconds)],
        dtype=np.float32,
    )
    peak = float(rms.max()) if rms.size else 0.0
    levels = []
    for value in (rms / peak if peak > 0 else rms):
        if value < 0.2:
            levels.append(EnergyLevel.VOID)
        elif value < 0.4:
            levels.append(EnergyLevel.LOW)
        elif value < 0.65:
            levels.append(EnergyLevel.MEDIUM)
        else:
            levels.append(EnergyLevel.HIGH)
    phases: list[EnergyPhase] = []
    for second, level in enumerate(levels):
        if phases and phases[-1].level == level:
            phases[-1] = phases[-1].model_copy(update={"end_seconds": float(second + 1)})
        else:
            phases.append(EnergyPhase(start_seconds=float(second), end_seconds=float(second + 1), level=level))
    return phases


def analyze_signal(signal, sample_rate: int = SAMPLE_RATE) -> BeatGrid:
    envelope, low = onset_envelope(signal, sample_rate)
    duration = round(signal.size / sample_rate, 3)
    bpm, prominence = estimate_tempo(envelope, sample_rate)
    analyzer = "numpy"
    beats: list[float] = []
    try:  # pragma: no cover - host-only accelerator
        import librosa  # type: ignore[import-not-found]

        tempo, frames = librosa.beat.beat_track(y=signal, sr=sample_rate, hop_length=HOP)
        tempo_value = float(tempo[0] if hasattr(tempo, "__len__") else tempo)
        if tempo_value > 0 and len(frames) >= 4:
            bpm = round(tempo_value, 2)
            beats = [round(float(value), 4) for value in librosa.frames_to_time(frames, sr=sample_rate, hop_length=HOP)]
            analyzer = "librosa"
    except Exception:  # noqa: BLE001 - numpy path is the contract
        beats = []
    strength = 0.0
    if bpm and not beats:
        beats, strength = beat_times(envelope, bpm, sample_rate)
    elif bpm and beats:
        _grid, strength = beat_times(envelope, bpm, sample_rate)
    rhythmic = bool(bpm) and prominence >= RHYTHMIC_PROMINENCE and strength >= PULSE_STRENGTH_MIN
    confidence = max(0.0, min(1.0, (prominence - 1.0) / 4.0)) if rhythmic else 0.0
    return BeatGrid(
        duration_seconds=duration,
        bpm=bpm if bpm else None,
        rhythmic=rhythmic,
        confidence=round(confidence, 3),
        beat_seconds=beats if rhythmic else [],
        downbeat_seconds=downbeat_times(beats, low, sample_rate=sample_rate) if rhythmic else [],
        energy_phases=energy_phases(signal, sample_rate),
        analyzer=analyzer,
    )


def _cache_path(path: Path) -> Path:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    return vault_root() / "runtime" / "audio" / "beatgrids" / f"{digest}.{BEAT_GRID_VERSION}.json"


def analyze_beat_grid(path: Path, *, use_cache: bool = True) -> BeatGrid | None:
    """The cue's grid, cached by its bytes; None when the audio cannot be decoded."""
    path = Path(path)
    if not path.is_file():
        return None
    cache = _cache_path(path) if use_cache else None
    if cache is not None and cache.is_file():
        try:
            return BeatGrid.model_validate_json(cache.read_text(encoding="utf-8"))
        except ValueError:
            pass
    try:
        signal = _decode(path)
    except Exception:  # noqa: BLE001 - an unmeasurable cue is phrase flow
        return None
    grid = analyze_signal(signal)
    if cache is not None:
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(grid.model_dump_json(), encoding="utf-8")
        except OSError:
            pass
    return grid


__all__ = ["analyze_beat_grid", "analyze_signal", "beat_times", "downbeat_times", "energy_phases", "estimate_tempo", "onset_envelope"]
