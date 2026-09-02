"""Clip review: measured evidence when tools exist, honest 'unmeasured' when not."""

import shutil
import subprocess

import pytest

from diony_director import packagebuilder, review, screenwriter


def _package():
    draft = screenwriter.write_script("a chase through a desert town", runtime_target_s=60)
    return packagebuilder.build_package("s-r", "a chase", draft)


def test_missing_media_fails_delivery(tmp_path) -> None:
    package = _package()
    clip = package.clips[0]
    result = review.review_clip(tmp_path / "nope.mp4", clip)
    assert result.verdict == "fail"
    assert result.families["delivery"] == "fail"


def test_tiny_media_fails_delivery(tmp_path) -> None:
    package = _package()
    clip = package.clips[0]
    path = tmp_path / "stub.mp4"
    path.write_bytes(b"x" * 10)
    result = review.review_clip(path, clip)
    assert result.families["delivery"] == "fail"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_real_clip_measures_duration_and_audio(tmp_path) -> None:
    package = _package()
    dialogue = next(c for c in package.clips if c.clip_class == "sync-dialogue")
    path = tmp_path / "clip.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc2=size=320x180:duration={dialogue.duration_s}",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={dialogue.duration_s}",
            "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(path),
        ],
        check=True, capture_output=True,
    )
    result = review.review_clip(path, dialogue)
    assert result.families["delivery"] == "pass"
    assert result.families["duration"] == "pass"
    assert result.families["audio"] == "pass"
    assert result.families["identity"] == "unmeasured"
    assert result.verdict == "warn"  # identity unmeasured on a character clip → never "pass"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_silent_clip_fails_audio_when_speech_is_scripted(tmp_path) -> None:
    package = _package()
    dialogue = next(c for c in package.clips if c.clip_class == "sync-dialogue")
    path = tmp_path / "silent.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc2=size=320x180:duration={dialogue.duration_s}",
            "-c:v", "libx264", "-preset", "ultrafast", str(path),
        ],
        check=True, capture_output=True,
    )
    result = review.review_clip(path, dialogue)
    assert result.families["audio"] == "fail"
    assert result.verdict == "fail"


class _Speech:
    """The shape review reads off a speech detection receipt."""

    def __init__(self, transcript: str, word_count: int, speech_s: float):
        self.transcript = transcript
        self.word_count = word_count
        self.speech_s = speech_s


def _stub_clip(tmp_path):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"\x00" * 4096)
    return path


def test_a_talking_voice_on_the_location_track_fails_review(tmp_path, monkeypatch) -> None:
    # Blueprint 28: the voice track is a post instrument — a provider-invented
    # voice on the location track stacks under the narrator as a second voice
    package = _package()
    clip = package.clips[0]
    monkeypatch.setattr(review, "location_speech", lambda p: _Speech("we should go now", 4, 2.2))
    result = review.review_clip(_stub_clip(tmp_path), clip)
    assert result.families["speech"] == "fail"
    assert result.verdict == "fail"
    assert any("voice" in issue for issue in result.issues)


def test_a_clean_location_track_passes_the_speech_family(tmp_path, monkeypatch) -> None:
    package = _package()
    clip = package.clips[0]
    monkeypatch.setattr(review, "location_speech", lambda p: _Speech("", 0, 0.0))
    result = review.review_clip(_stub_clip(tmp_path), clip)
    assert result.families["speech"] == "pass"


def test_speech_is_honestly_unmeasured_without_a_detector(tmp_path, monkeypatch) -> None:
    package = _package()
    clip = package.clips[0]
    monkeypatch.setattr(review, "location_speech", lambda p: None)
    result = review.review_clip(_stub_clip(tmp_path), clip)
    assert result.families["speech"] == "unmeasured"
    assert result.verdict != "fail"


def test_a_chain_link_with_a_talking_voice_fails_its_review(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(review, "location_speech", lambda p: _Speech("hello there my friend", 4, 1.8))
    result = review.review_chain_link(_stub_clip(tmp_path), op_id="op-1", duration_s=8)
    assert result.families["speech"] == "fail"
    assert result.verdict == "fail"
