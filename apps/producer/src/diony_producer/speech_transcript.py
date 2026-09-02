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
_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
         "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS = {2: "twenty", 3: "thirty", 4: "forty", 5: "fifty", 6: "sixty", 7: "seventy", 8: "eighty", 9: "ninety"}
_CONTRACTIONS = {
    "i'm": "i am", "you're": "you are", "we're": "we are", "they're": "they are",
    "it's": "it is", "that's": "that is", "there's": "there is", "he's": "he is", "she's": "she is",
    "isn't": "is not", "aren't": "are not", "wasn't": "was not", "weren't": "were not",
    "don't": "do not", "doesn't": "does not", "didn't": "did not", "can't": "cannot", "won't": "will not",
    "couldn't": "could not", "wouldn't": "would not", "shouldn't": "should not",
    "i'll": "i will", "you'll": "you will", "we'll": "we will", "they'll": "they will", "he'll": "he will", "she'll": "she will",
    "i've": "i have", "you've": "you have", "we've": "we have", "they've": "they have",
    "i'd": "i would", "you'd": "you would", "we'd": "we would", "they'd": "they would",
    "let's": "let us",
}


@dataclass(frozen=True)
class SpeechDetection:
    """Is there a talking voice on this track at all? (Blueprint 28: the
    director lane's location tracks are speechless by doctrine — any heard
    words are a defect, whatever they say.)"""

    transcript: str
    word_count: int
    speech_s: float
    model: str


@dataclass(frozen=True)
class TranscriptMeasurement:
    transcript: str
    word_accuracy: float
    last_word_ok: bool
    model: str
    missed_words: tuple[str, ...] = ()


def number_words(value: int) -> list[str]:
    """0–999 as spoken words, the way an ASR model writes them back."""
    if value < 0 or value > 999:
        return [str(value)]
    if value < 20:
        return [_ONES[value]]
    if value < 100:
        tens, ones = divmod(value, 10)
        return [_TENS[tens]] + ([_ONES[ones]] if ones else [])
    hundreds, rest = divmod(value, 100)
    words = [_ONES[hundreds], "hundred"]
    return words + (number_words(rest) if rest else [])


def normalize_words(text: str) -> list[str]:
    """Casefolded words with digits spelled out and contractions expanded."""
    lowered = str(text or "").casefold().replace("\u2019", "'")
    for contraction, expansion in _CONTRACTIONS.items():
        lowered = re.sub(rf"\b{re.escape(contraction)}\b", expansion, lowered)
    out: list[str] = []
    for word in _WORD.findall(lowered):
        if word.isdigit():
            out.extend(number_words(int(word)))
        else:
            out.append(word)
    return out


def word_accuracy(authored: str, heard: str) -> tuple[float, bool]:
    """(fraction of authored words heard, whether the final authored word was heard)."""
    accuracy, last_ok, _missed = score_transcript(authored, heard)
    return accuracy, last_ok


def score_transcript(authored: str, heard: str) -> tuple[float, bool, list[str]]:
    """Accuracy, last-word check, and the authored words that were not heard.

    Multiset overlap: a word authored twice must be heard twice to count
    twice. Empty authored text measures 1.0 (nothing could be missed).
    """
    expected = normalize_words(authored)
    actual = normalize_words(heard)
    if not expected:
        return 1.0, True, []
    remaining = list(actual)
    hits = 0
    missed: list[str] = []
    for word in expected:
        match = _take_match(word, remaining)
        if match is not None:
            remaining.pop(match)
            hits += 1
        else:
            missed.append(word)
    tail = expected[-1]
    last_ok = _take_match(tail, actual[-3:]) is not None if actual else False
    return round(hits / len(expected), 4), last_ok, missed


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


def detect_speech(audio: Path) -> SpeechDetection | None:
    """Whether a voice speaks on this track, measured locally; None when
    unmeasurable (no staged model, unreadable media). The VAD filter keeps
    instrumental score and ambience from reading as words."""
    model_dir = whisper_model_path()
    if model_dir is None or not Path(audio).is_file():
        return None
    try:
        model = _load_model(str(model_dir))
        segments, _info = model.transcribe(
            str(audio), beam_size=1, vad_filter=True, word_timestamps=False
        )
        heard: list[str] = []
        speech_s = 0.0
        for segment in segments:
            text = segment.text.strip()
            if text:
                heard.append(text)
                speech_s += max(0.0, float(segment.end) - float(segment.start))
    except Exception:  # noqa: BLE001 - an unmeasurable track never fails a take
        return None
    transcript = " ".join(heard).strip()
    return SpeechDetection(
        transcript=transcript[:600],
        word_count=len(normalize_words(transcript)),
        speech_s=round(speech_s, 2),
        model=model_dir.name,
    )


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
    accuracy, last_ok, missed = score_transcript(authored_line, heard)
    return TranscriptMeasurement(
        transcript=heard[:600],
        word_accuracy=accuracy,
        last_word_ok=last_ok,
        model=model_dir.name,
        missed_words=tuple(missed[:24]),
    )


__all__ = ["SpeechDetection", "TranscriptMeasurement", "detect_speech", "normalize_words", "number_words", "score_transcript", "transcribe_line", "whisper_model_path", "word_accuracy"]
