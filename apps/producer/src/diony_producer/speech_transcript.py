"""Optional local transcript check of the speech Wan2.2 delivered (Blueprint 24).

When a faster-whisper model directory is installed on the host and named by
``DIONY_WHISPER_MODEL``, the delivered dialogue stem is transcribed and the
authored line is measured against it: word accuracy is the fraction of the
line's words heard back, and the last authored word must be heard so a cut
line is caught even when presence and tail measure fine. Without the model
the check is unmeasured and gates nothing — never a guess.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_WORD = re.compile(r"[a-z0-9']+")
_NUMBER_WORDS = {
    "0": "zero", "1": "one", "2": "two", "3": "three", "4": "four", "5": "five",
    "6": "six", "7": "seven", "8": "eight", "9": "nine", "10": "ten",
}


@dataclass(frozen=True)
class TranscriptMeasurement:
    transcript: str
    word_accuracy: float
    last_word_ok: bool
    model: str


def normalize_words(text: str) -> list[str]:
    words = _WORD.findall(str(text or "").casefold().replace("’", "'"))
    return [_NUMBER_WORDS.get(word, word) for word in words]


def word_accuracy(authored: str, heard: str) -> tuple[float, bool]:
    """(fraction of authored words heard, whether the final authored word was heard).

    Multiset overlap: a word authored twice must be heard twice to count
    twice. Empty authored text measures 1.0 (nothing could be missed).
    """
    expected = normalize_words(authored)
    actual = normalize_words(heard)
    if not expected:
        return 1.0, True
    remaining = list(actual)
    hits = 0
    for word in expected:
        match = _take_match(word, remaining)
        if match is not None:
            remaining.pop(match)
            hits += 1
    tail = expected[-1]
    last_ok = _take_match(tail, actual[-3:]) is not None if actual else False
    return round(hits / len(expected), 4), last_ok


# Names and rare words come back from an ASR model with a spelling of their
# own ("Marcus" → "Markus"); a close spelling is the same word for this gate.
FUZZY_WORD_RATIO = 0.8


def _take_match(word: str, pool: list[str]) -> int | None:
    from difflib import SequenceMatcher

    for index, candidate in enumerate(pool):
        if candidate == word:
            return index
    if len(word) < 4:
        return None
    for index, candidate in enumerate(pool):
        if len(candidate) >= 4 and SequenceMatcher(None, word, candidate).ratio() >= FUZZY_WORD_RATIO:
            return index
    return None


def whisper_model_path() -> Path | None:
    raw = os.environ.get("DIONY_WHISPER_MODEL", "").strip()
    if not raw:
        return None
    path = Path(raw)
    return path if path.exists() else None


@lru_cache(maxsize=1)
def _load_model(model_dir: str):
    from faster_whisper import WhisperModel  # type: ignore[import-not-found]

    device = "cuda" if os.environ.get("DIONY_WHISPER_DEVICE", "auto") in {"auto", "cuda"} else "cpu"
    compute = os.environ.get("DIONY_WHISPER_COMPUTE", "int8_float16" if device == "cuda" else "int8")
    try:
        return WhisperModel(model_dir, device=device, compute_type=compute)
    except Exception:  # noqa: BLE001 - GPU load failure falls back to CPU int8
        return WhisperModel(model_dir, device="cpu", compute_type="int8")


def transcribe_line(audio: Path, authored_line: str) -> TranscriptMeasurement | None:
    """Transcribe the stem locally and score it against the authored line; None when unmeasurable."""
    model_dir = whisper_model_path()
    if model_dir is None or not Path(audio).is_file():
        return None
    try:
        model = _load_model(str(model_dir))
        segments, _info = model.transcribe(str(audio), beam_size=1, vad_filter=True, word_timestamps=False)
        heard = " ".join(segment.text.strip() for segment in segments).strip()
    except Exception:  # noqa: BLE001 - an unmeasurable transcript never fails a take
        return None
    accuracy, last_ok = word_accuracy(authored_line, heard)
    return TranscriptMeasurement(transcript=heard[:600], word_accuracy=accuracy, last_word_ok=last_ok, model=model_dir.name)


__all__ = ["TranscriptMeasurement", "normalize_words", "transcribe_line", "whisper_model_path", "word_accuracy"]
