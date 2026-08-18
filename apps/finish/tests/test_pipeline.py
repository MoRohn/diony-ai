from pathlib import Path

from diony_finish.pipeline import finish_clip, stitch_masters


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
