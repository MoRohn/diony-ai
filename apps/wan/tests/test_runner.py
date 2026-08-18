from pathlib import Path

from diony_wan.runner import spark_argv


def test_argv_is_720p_official_and_never_dashscope(tmp_path, monkeypatch) -> None:
    gen = tmp_path / "generate.py"
    gen.write_text("# fake\n")
    ckpt = tmp_path / "Wan2.2-T2V-A14B"
    ckpt.mkdir()
    (ckpt / "config.json").write_text("{}")
    monkeypatch.setenv("DIONY_WAN_GENERATE", str(gen))
    monkeypatch.setenv("WAN_CKPT_T2V", str(ckpt))
    dest = tmp_path / "out.mp4"
    cmd = spark_argv(
        task="t2v-A14B",
        prompt="A tungsten newsroom, 35mm, held master.",
        seed=42,
        save_file=dest,
        frame_num=81,
    )
    joined = " ".join(cmd)
    assert "--task t2v-A14B" in joined
    assert "--size 1280*720" in joined
    assert "--offload_model True" in joined
    assert "--t5_cpu" in joined
    assert "--use_prompt_extend" not in cmd
    assert "dashscope" not in joined.lower()
    assert str(dest) in cmd
    still = tmp_path / "key.png"
    still.write_bytes(b"png")
    i2v_ckpt = tmp_path / "i2v"
    i2v_ckpt.mkdir()
    monkeypatch.setenv("WAN_CKPT_I2V", str(i2v_ckpt))
    i2v = spark_argv(
        task="i2v-A14B",
        prompt="Same room, push in.",
        seed=7,
        save_file=dest,
        frame_num=81,
        image=still,
    )
    assert "--task" in i2v and "i2v-A14B" in i2v
    assert "--image" in i2v
    assert str(still) in i2v
    clip = tmp_path / "prev.mp4"
    clip.write_bytes(b"not-a-still")
    no_img = spark_argv(
        task="t2v-A14B",
        prompt="Held master.",
        seed=1,
        save_file=dest,
        frame_num=81,
        image=clip,
    )
    assert "--image" not in no_img
