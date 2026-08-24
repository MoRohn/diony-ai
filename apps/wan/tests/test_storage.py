from __future__ import annotations

import os
from collections import namedtuple
from pathlib import Path


def _snapshot(root: Path, *, revision: str = "a" * 40, size: int = 1024) -> Path:
    root.mkdir(parents=True)
    payload = root / "weights.safetensors"
    payload.write_bytes(b"w" * size)
    metadata = root / ".cache" / "huggingface" / "download" / "weights.safetensors.metadata"
    metadata.parent.mkdir(parents=True)
    metadata.write_text(f"{revision}\n{'b' * 64}\n0\n", encoding="utf-8")
    return root


def test_hot_checkpoint_is_atomic_verified_and_reused(tmp_path, monkeypatch) -> None:
    from diony_wan.storage import prepare_checkpoint

    source = _snapshot(tmp_path / "volume" / "Wan2.2-T2V-A14B")
    cache_root = tmp_path / "overlay"
    monkeypatch.setenv("DIONY_MODEL_HOT_CACHE", "1")
    monkeypatch.setenv("DIONY_MODEL_HOT_CACHE_ROOT", str(cache_root))
    monkeypatch.setenv("DIONY_MODEL_HOT_CACHE_GB", "1")
    monkeypatch.setenv("DIONY_MODEL_HOT_CACHE_MIN_FREE_GB", "0")

    hot = prepare_checkpoint("t2v-A14B", source=source)

    assert hot != source
    assert (hot / "weights.safetensors").read_bytes() == b"w" * 1024
    assert (hot / ".diony-hot-cache.json").is_file()
    assert not list(cache_root.glob("*.partial-*"))
    payload_mtime = (hot / "weights.safetensors").stat().st_mtime_ns

    again = prepare_checkpoint("t2v-A14B", source=source)

    assert again == hot
    assert (again / "weights.safetensors").stat().st_mtime_ns == payload_mtime


def test_hot_checkpoint_rejects_an_incomplete_snapshot(tmp_path, monkeypatch) -> None:
    from diony_wan.storage import prepare_checkpoint

    source = _snapshot(tmp_path / "volume" / "Wan2.2-I2V-A14B")
    (source / ".cache" / "huggingface" / "download" / "weights.incomplete").write_bytes(b"")
    cache_root = tmp_path / "overlay"
    monkeypatch.setenv("DIONY_MODEL_HOT_CACHE", "1")
    monkeypatch.setenv("DIONY_MODEL_HOT_CACHE_ROOT", str(cache_root))

    assert prepare_checkpoint("i2v-A14B", source=source) == source
    assert not cache_root.exists()


def test_hot_checkpoint_preserves_the_overlay_free_floor(tmp_path, monkeypatch) -> None:
    from diony_wan import storage

    source = _snapshot(tmp_path / "volume" / "Wan2.2-S2V-14B")
    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setenv("DIONY_MODEL_HOT_CACHE", "1")
    monkeypatch.setenv("DIONY_MODEL_HOT_CACHE_ROOT", str(tmp_path / "overlay"))
    monkeypatch.setenv("DIONY_MODEL_HOT_CACHE_MIN_FREE_GB", "64")
    monkeypatch.setattr(
        storage.shutil,
        "disk_usage",
        lambda _path: Usage(600 * 1024**3, 550 * 1024**3, 50 * 1024**3),
    )

    assert storage.prepare_checkpoint("s2v-14B", source=source) == source


def test_prefetch_never_evicts_a_cache_that_generation_may_be_using(tmp_path, monkeypatch) -> None:
    from diony_wan.storage import prefetch_checkpoint, wait_for_prefetch

    first = _snapshot(tmp_path / "volume" / "first", revision="1" * 40, size=700_000)
    second = _snapshot(tmp_path / "volume" / "second", revision="2" * 40, size=700_000)
    cache_root = tmp_path / "overlay"
    monkeypatch.setenv("DIONY_MODEL_HOT_CACHE", "1")
    monkeypatch.setenv("DIONY_MODEL_HOT_CACHE_ROOT", str(cache_root))
    monkeypatch.setenv("DIONY_MODEL_HOT_CACHE_GB", "0.001")
    monkeypatch.setenv("DIONY_MODEL_HOT_CACHE_MIN_FREE_GB", "0")

    from diony_wan.storage import prepare_checkpoint

    cached = prepare_checkpoint("t2v-A14B", source=first)
    assert cached != first

    prefetch_checkpoint("i2v-A14B", source=second)
    wait_for_prefetch("i2v-A14B", timeout=5)

    assert (cached / "weights.safetensors").is_file()
    assert not (cache_root / second.name / "weights.safetensors").exists()
