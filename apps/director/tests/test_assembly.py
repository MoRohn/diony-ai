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


def _mean_volume_db(path, start: float, duration: float) -> float:
    out = subprocess.run(
        [
            "ffmpeg", "-ss", str(start), "-t", str(duration), "-i", str(path),
            "-af", "volumedetect", "-f", "null", "-",
        ],
        capture_output=True,
        text=True,
    )
    for line in out.stderr.splitlines():
        if "mean_volume:" in line:
            return float(line.split("mean_volume:")[1].split("dB")[0])
    raise AssertionError(f"no mean_volume in volumedetect output for {path}")


def test_a_muted_spec_silences_a_clip_that_has_audio(tmp_path) -> None:
    # the speech guard's kill switch: a clip whose location track carries a
    # talking voice is cut with a silent bed — the voice can never reach the mix
    a = tmp_path / "a.mp4"
    b = tmp_path / "b.mp4"
    _make_clip(a, 1.5, audio=True)
    _make_clip(b, 1.5, audio=True)
    result = assembly.assemble(
        [a, b], tmp_path / "film.mp4", specs=[{}, {"mute": True}]
    )
    loud = _mean_volume_db(result.path, 0.2, 1.0)
    muted = _mean_volume_db(result.path, 1.7, 1.0)
    assert muted < loud - 12
    assert muted < -30


def test_empty_assembly_is_refused() -> None:
    with pytest.raises(assembly.AssemblyFailed):
        assembly.assemble([], __import__("pathlib").Path("/tmp/never.mp4"))
