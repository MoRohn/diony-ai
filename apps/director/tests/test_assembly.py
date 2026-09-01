"""Assembly: mixed silent/sounded clips become one normalized, loudness-locked film."""

import shutil
import subprocess

import pytest

from diony_director import assembly

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg not installed"
)


def _make_clip(path, duration: float, *, audio: bool, size: str = "320x180") -> None:
    cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc2=size={size}:duration={duration}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}", "-c:a", "aac", "-shortest"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", str(path)]
    subprocess.run(cmd, check=True, capture_output=True)


def test_mixed_audio_clips_assemble_into_one_film(tmp_path) -> None:
    a = tmp_path / "a.mp4"
    b = tmp_path / "b.mp4"
    _make_clip(a, 1.0, audio=True)
    _make_clip(b, 1.0, audio=False)

    result = assembly.assemble([a, b], tmp_path / "film.mp4")
    assert result.path.is_file()
    assert result.clip_count == 2
    assert 1.6 <= result.duration_s <= 2.6
    assert result.look == "diony-default"


def test_empty_assembly_is_refused() -> None:
    with pytest.raises(assembly.AssemblyFailed):
        assembly.assemble([], __import__("pathlib").Path("/tmp/never.mp4"))
