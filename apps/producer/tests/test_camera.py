from pathlib import Path

from diony_producer.camera import resolve_camera_task


def test_i2v_needs_a_still(tmp_path) -> None:
    still = tmp_path / "hero.jpg"
    still.write_bytes(b"jpg")
    clip = tmp_path / "prev.mp4"
    clip.write_bytes(b"mp4")
    assert resolve_camera_task("i2v-A14B", still) == "i2v-A14B"
    assert resolve_camera_task("i2v-A14B", None) == "t2v-A14B"
    assert resolve_camera_task("i2v-A14B", clip) == "t2v-A14B"
    assert resolve_camera_task("s2v-14B", still) == "t2v-A14B"
    assert resolve_camera_task("t2v-A14B", still) == "t2v-A14B"
