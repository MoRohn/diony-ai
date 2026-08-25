import json
import shutil
import subprocess
from pathlib import Path

import pytest

from diony_finish.pipeline import (
    FinishModelCache,
    FinishError,
    _decode_frames,
    _decoded_signal_bounds,
    _encode_sr_stream,
    _signal_bounds_are_limited,
    _select_tile_size,
    _validate_rrdb_checkpoint,
    finish_clip,
    finished_media_has_picture,
    stitch_masters,
)


class _Weight:
    def __init__(self, shape: tuple[int, ...]) -> None:
        self.shape = shape


def test_x2_checkpoint_contract_requires_pixel_unshuffle_channels() -> None:
    state = {
        "conv_first.weight": _Weight((64, 12, 3, 3)),
        "conv_up2.weight": _Weight((64, 64, 3, 3)),
    }
    _validate_rrdb_checkpoint(state, Path("RealESRGAN_x2.pth"))

    state["conv_first.weight"] = _Weight((64, 3, 3, 3))
    with pytest.raises(FinishError, match="expected 12.*pixel-unshuffle"):
        _validate_rrdb_checkpoint(state, Path("wrong.pth"))


def test_stitch_filter_uses_temporal_overlap_instead_of_hard_concat() -> None:
    from diony_finish.pipeline import _stitch_filter

    graph, output, total = _stitch_filter(
        [(Path("one.mov"), 0, 120), (Path("two.mov"), 8, 120)],
        fps=23.976,
    )

    assert "xfade=transition=fade" in graph
    assert "duration=0.333667" in graph
    assert "offset=4.671338" in graph
    assert "format=yuv422p10le" in graph
    assert "limiter=min=72:max=932:planes=1" in graph
    assert "limiter=min=72:max=952:planes=6" in graph
    assert output == "[program]"
    assert total == 232


def test_stitch_trims_to_exact_timeline_frames_and_refuses_to_stretch(tmp_path) -> None:
    """A booked frame may be trimmed from delivered picture, never stretched into it."""
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("FFmpeg tools are required")
    clips: list[Path] = []
    for index, source in enumerate(("testsrc", "testsrc2"), start=1):
        clip = tmp_path / f"clip-{index}.mov"
        subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"{source}=s=160x90:r=23.976:d=4",
                "-frames:v",
                "81",
                "-an",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(clip),
            ],
            check=True,
        )
        clips.append(clip)

    destination = tmp_path / "exact-timeline.mov"
    stitch_masters(
        [(clips[0], 0, 81), (clips[1], 8, 74)],
        destination,
        fps=23.976,
    )
    probe = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-count_frames",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=nb_read_frames",
            "-of",
            "default=nw=1:nk=1",
            str(destination),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    # 81 + 74 booked frames sharing an 8-frame temporal blend.
    assert int(probe.stdout.strip()) == 147

    with pytest.raises(FinishError, match="refusing to stretch"):
        stitch_masters(
            [(clips[0], 0, 120), (clips[1], 8, 128)],
            tmp_path / "stretched.mov",
            fps=23.976,
        )


def test_rrdb_x2_matches_upstream_shape_and_output() -> None:
    torch = pytest.importorskip("torch")
    from diony_finish.rrdb import RRDBNet

    model = RRDBNet(num_block=1, scale=2)
    assert tuple(model.conv_first.weight.shape) == (64, 12, 3, 3)
    assert model.conv_up2 is not None
    output = model(torch.zeros(1, 3, 8, 8))
    assert tuple(output.shape) == (1, 3, 16, 16)


def test_b200_uses_large_tiles_but_small_gpus_keep_safe_tiles(monkeypatch) -> None:
    monkeypatch.delenv("DIONY_SR_TILE", raising=False)
    gib = 1024**3
    assert _select_tile_size(183 * gib) == 720
    assert _select_tile_size(16 * gib) == 192

    monkeypatch.setenv("DIONY_SR_TILE", "384")
    assert _select_tile_size(183 * gib) == 384


def test_finish_encoder_preserves_picture_and_source_duration(tmp_path) -> None:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg tools are required")
    np = pytest.importorskip("numpy")
    frame = np.zeros((36, 64, 3), dtype=np.uint8)
    frame[:18, :32] = [240, 20, 20]
    frame[:18, 32:] = [20, 220, 40]
    frame[18:, :32] = [20, 40, 240]
    frame[18:, 32:] = [235, 220, 30]
    destination = tmp_path / "color-and-timing.mov"

    _encode_sr_stream(
        [frame, frame, frame, frame],
        8.0,
        destination,
        source_fps=4.0,
    )

    decoded, _ = _decode_frames(destination)
    assert len(decoded) == 8
    assert max(float(decoded[0][:, :, channel].std()) for channel in range(3)) > 20
    probe = subprocess.run(
        [
            shutil.which("ffprobe") or "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,nb_frames,color_range,color_space",
            "-of",
            "json",
            str(destination),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    stream = json.loads(probe.stdout)["streams"][0]
    assert stream["width"] == 1920
    assert stream["height"] == 1080
    assert stream["color_range"] == "tv"
    assert stream["color_space"] == "bt709"


def test_finish_encoder_consumes_a_single_pass_frame_stream(tmp_path) -> None:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg tools are required")
    np = pytest.importorskip("numpy")
    frame = np.zeros((36, 64, 3), dtype=np.uint8)
    frame[:, :32] = [230, 30, 20]
    frame[:, 32:] = [20, 60, 230]
    yielded = 0

    def frames():
        nonlocal yielded
        for _ in range(4):
            yielded += 1
            yield frame.copy()

    destination = tmp_path / "streamed-prores.mov"
    _encode_sr_stream(frames(), 8.0, destination, source_fps=4.0)

    assert yielded == 4
    probe = subprocess.run(
        [
            shutil.which("ffprobe") or "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,pix_fmt,nb_frames",
            "-of",
            "json",
            str(destination),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    stream = json.loads(probe.stdout)["streams"][0]
    assert stream["codec_name"] == "prores"
    assert stream["pix_fmt"] == "yuv422p10le"
    bounds = _decoded_signal_bounds(destination)
    assert _signal_bounds_are_limited(bounds), bounds
    assert int(stream["nb_frames"]) == 8


def test_decoded_signal_bounds_require_broadcast_legal_10_bit_samples(monkeypatch) -> None:
    class Result:
        returncode = 0
        stdout = ""
        stderr = "\n".join(
            [
                "lavfi.signalstats.YMIN=64",
                "lavfi.signalstats.YMAX=940",
                "lavfi.signalstats.UMIN=64",
                "lavfi.signalstats.UMAX=960",
                "lavfi.signalstats.VMIN=64",
                "lavfi.signalstats.VMAX=960",
                "lavfi.signalstats.YMIN=72",
                "lavfi.signalstats.YMAX=932",
            ]
        )

    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: Result())
    bounds = _decoded_signal_bounds(Path("master.mov"))

    assert bounds == {
        "YMIN": 64.0,
        "YMAX": 940.0,
        "UMIN": 64.0,
        "UMAX": 960.0,
        "VMIN": 64.0,
        "VMAX": 960.0,
    }
    assert _signal_bounds_are_limited(bounds) is True
    assert _signal_bounds_are_limited({**bounds, "YMAX": 941.0}) is False
    assert _signal_bounds_are_limited({**bounds, "VMIN": 63.0}) is False
    assert _signal_bounds_are_limited({}) is False


def test_finish_model_cache_reuses_only_an_unchanged_checkpoint(tmp_path) -> None:
    checkpoint = tmp_path / "RealESRGAN_x2.pth"
    checkpoint.write_bytes(b"first")
    cache = FinishModelCache()
    loaded = []

    def loader(path: Path):
        loaded.append(path.read_bytes())
        return object(), "cuda"

    first = cache.get(checkpoint, loader)
    again = cache.get(checkpoint, loader)
    checkpoint.write_bytes(b"updated checkpoint bytes")
    changed = cache.get(checkpoint, loader)

    assert first is again
    assert changed is not first
    assert loaded == [b"first", b"updated checkpoint bytes"]


def test_program_stitch_uses_the_same_10_bit_prores_master_codec(tmp_path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("FFmpeg tools are required")
    clips = []
    for index, source in enumerate(("testsrc", "testsrc2"), start=1):
        clip = tmp_path / f"source-{index}.mov"
        subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"{source}=s=320x180:r=24:d=0.5",
                "-an",
                "-c:v",
                "prores_ks",
                "-profile:v",
                "3",
                "-pix_fmt",
                "yuv422p10le",
                str(clip),
            ],
            check=True,
        )
        clips.append(clip)
    destination = tmp_path / "program-master.mov"

    stitch_masters([(clips[0], 0, 12), (clips[1], 2, 12)], destination, fps=24.0)

    probe = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,pix_fmt,color_space",
            "-of",
            "json",
            str(destination),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    stream = json.loads(probe.stdout)["streams"][0]
    assert stream["codec_name"] == "prores"
    assert stream["pix_fmt"] == "yuv422p10le"


def test_finished_media_rejects_flat_green_video(tmp_path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg is required")
    flat = tmp_path / "flat-green.mov"
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=green:s=1920x1080:r=24:d=1",
            "-an",
            "-c:v",
            "prores_ks",
            "-profile:v",
            "3",
            str(flat),
        ],
        check=True,
    )

    assert finished_media_has_picture(flat) is False


def test_finished_media_rejects_green_field_with_single_channel_noise(tmp_path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg is required")
    noisy_green = tmp_path / "noisy-green.mov"
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=green:s=1920x1080:r=24:d=1",
            "-vf",
            "drawbox=x=0:y=0:w=48:h=ih:color=0x006000:t=fill",
            "-an",
            "-c:v",
            "prores_ks",
            "-profile:v",
            "3",
            str(noisy_green),
        ],
        check=True,
    )

    assert finished_media_has_picture(noisy_green) is False


def test_finished_media_rejects_structured_but_frozen_video(tmp_path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg is required")
    frozen = tmp_path / "frozen-picture.mov"
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=white:s=1920x1080:r=24:d=1",
            "-vf",
            "drawbox=x=200:y=160:w=720:h=600:color=red:t=fill",
            "-an",
            "-c:v",
            "prores_ks",
            "-profile:v",
            "3",
            str(frozen),
        ],
        check=True,
    )

    assert finished_media_has_picture(frozen) is False


def test_finished_media_accepts_real_moving_picture(tmp_path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg is required")
    moving = tmp_path / "moving-picture.mov"
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=1920x1080:r=24:d=1",
            "-an",
            "-c:v",
            "prores_ks",
            "-profile:v",
            "3",
            str(moving),
        ],
        check=True,
    )

    assert finished_media_has_picture(moving) is True


def test_finished_media_verification_rejects_wrong_dimensions_and_delivery_codec(tmp_path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg is required")
    from diony_finish.pipeline import verify_finished_media

    wrong_size = tmp_path / "wrong-size.mov"
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=1280x720:r=24:d=1",
            "-an",
            "-c:v",
            "prores_ks",
            "-profile:v",
            "3",
            "-pix_fmt",
            "yuv422p10le",
            "-color_range",
            "tv",
            "-colorspace",
            "bt709",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            str(wrong_size),
        ],
        check=True,
    )
    with pytest.raises(FinishError, match="1920.1080"):
        verify_finished_media(wrong_size)

    wrong_codec = tmp_path / "wrong-codec.mp4"
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=1920x1080:r=24:d=1",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-color_range",
            "tv",
            "-colorspace",
            "bt709",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            str(wrong_codec),
        ],
        check=True,
    )
    with pytest.raises(FinishError, match="ProRes.*10-bit"):
        verify_finished_media(wrong_codec)


def test_fake_finish_writes_master(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DIONY_FINISH_FAKE", "1")
    monkeypatch.delenv("DIONY_DRY_RUN", raising=False)
    src = tmp_path / "neg.mp4"
    src.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 200)
    dest = tmp_path / "finish_1080.mov"
    out = finish_clip(src, dest, fps=24.0)
    assert out.is_file()
    assert out.stat().st_size > 32


def test_fake_program_stitch(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DIONY_FINISH_FAKE", "1")
    monkeypatch.delenv("DIONY_DRY_RUN", raising=False)
    clip = tmp_path / "finish_1080.mov"
    clip.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 400)
    dest = tmp_path / "program" / "master_1080.mov"
    out = stitch_masters([(clip, 0, 24), (clip, 8, 24)], dest, fps=24.0)
    assert out.is_file()
    assert out.stat().st_size > 32


def test_temporal_sr_is_a_hard_dependency_not_a_silent_downgrade() -> None:
    from diony_finish.pipeline import require_temporal_sr

    try:
        import cv2  # noqa: F401
    except ImportError:
        with pytest.raises(FinishError, match="boiling grain"):
            require_temporal_sr()
    else:
        require_temporal_sr()


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg is required")
def test_post_sr_gate_accepts_a_faithful_finish_and_rejects_added_boil(tmp_path) -> None:
    import json as json_module

    from diony_finish.pipeline import PostSRQCError, measure_post_sr, verify_post_sr

    ffmpeg = shutil.which("ffmpeg")
    negative = tmp_path / "negative_720.mp4"
    subprocess.run(
        [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=24:d=2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(negative),
        ],
        check=True,
    )
    faithful = tmp_path / "finish_faithful.mov"
    subprocess.run(
        [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(negative),
            "-vf", "scale=1920:1080:flags=bicubic",
            "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", str(faithful),
        ],
        check=True,
    )
    receipt = verify_post_sr(negative, faithful, label="post-SR QC test")
    assert receipt["identity_ssim"] is None or receipt["identity_ssim"] >= 0.75
    assert receipt["boil_ratio"] <= 2.5
    stored = json_module.loads(
        (faithful.parent / (faithful.name + ".post-sr.json")).read_text()
    )
    assert stored["measurement_version"] == "diony-post-sr-v1"

    boiling = tmp_path / "finish_boiling.mov"
    subprocess.run(
        [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(negative),
            "-vf", "scale=1920:1080:flags=bicubic,noise=alls=40:allf=t+u",
            "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", str(boiling),
        ],
        check=True,
    )
    measured = measure_post_sr(negative, boiling)
    assert measured["boil_ratio"] > receipt["boil_ratio"]
    with pytest.raises(PostSRQCError):
        verify_post_sr(negative, boiling, label="post-SR QC test")
