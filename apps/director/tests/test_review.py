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
