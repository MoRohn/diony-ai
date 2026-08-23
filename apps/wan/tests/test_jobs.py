import json
import threading
from pathlib import Path

import pytest

from diony_wan.comfy import ComfyError, GenerationConfig, resolve_quality_tier
from diony_wan.jobs import WanJobManager


def test_quality_tier_selects_repeatable_defaults_and_allows_mode_override() -> None:
    preview = resolve_quality_tier(
        quality_tier="preview",
        prompt="A controlled preview shot.",
        seed=101,
    )
    assert preview.mode == "ti2v-5B"
    assert (preview.width, preview.height, preview.frames, preview.steps, preview.cfg) == (
        832,
        480,
        81,
        20,
        3.5,
    )

    identity = resolve_quality_tier(
        quality_tier="hero",
        prompt="A controlled image-driven shot.",
        seed=102,
        mode="i2v-A14B",
    )
    assert identity.mode == "i2v-A14B"
    assert identity.steps == 30
    assert identity.width == 1280

    with pytest.raises(ComfyError, match="quality tier"):
        resolve_quality_tier(quality_tier="ultra", prompt="x", seed=1)


def test_job_manager_persists_lifecycle_metrics_and_rejects_destination_collision(tmp_path: Path) -> None:
    started = threading.Event()
    release = threading.Event()

    def execute(job: dict) -> Path:
        manager.update_progress(
            job["id"],
            {
                "state": "processing",
                "prompt_id": "comfy-77",
                "queue_pending": 2,
                "vram_peak_bytes": 52 * 1024**3,
            },
        )
        started.set()
        assert release.wait(2)
        destination = Path(job["destination"])
        destination.write_bytes(b"x" * 64)
        return destination

    manager = WanJobManager(root=tmp_path / "jobs", execute=execute, max_workers=1)
    destination = tmp_path / "negative.mp4"
    job = manager.submit(
        config=GenerationConfig(mode="t2v-A14B", prompt="A static shot.", seed=77),
        destination=destination,
        quality_tier="production",
    )
    assert started.wait(1)
    assert manager.status(job["id"])["state"] == "running"
    assert manager.status(job["id"])["progress"]["prompt_id"] == "comfy-77"

    with pytest.raises(ComfyError, match="destination is already owned"):
        manager.submit(
            config=GenerationConfig(mode="t2v-A14B", prompt="Another shot.", seed=78),
            destination=destination,
            quality_tier="production",
        )

    release.set()
    completed = manager.wait(job["id"], timeout=2)
    assert completed["state"] == "completed"
    assert completed["metrics"]["elapsed_ms"] >= 0
    assert completed["metrics"]["vram_peak_bytes"] == 52 * 1024**3
    assert completed["output"]["bytes"] == 64
    assert manager.metrics()["completed"] == 1
    assert manager.metrics()["success_rate"] == 1.0
    assert manager.metrics()["peak_vram_bytes"] == 52 * 1024**3


def test_restart_recovery_adopts_landed_output_without_reexecuting(tmp_path: Path) -> None:
    calls: list[str] = []

    def execute(job: dict) -> Path:
        calls.append(job["id"])
        destination = Path(job["destination"])
        destination.write_bytes(b"x" * 64)
        return destination

    root = tmp_path / "jobs"
    first = WanJobManager(root=root, execute=execute, max_workers=1)
    job = first.submit(
        config=GenerationConfig(mode="t2v-A14B", prompt="A static shot.", seed=81),
        destination=tmp_path / "negative.mp4",
        quality_tier="production",
    )
    assert first.wait(job["id"], timeout=2)["state"] == "completed"
    assert len(calls) == 1

    persisted = root / f"{job['id']}.json"
    payload = json.loads(persisted.read_text())
    payload["state"] = "running"
    persisted.write_text(json.dumps(payload))

    recovered = WanJobManager(root=root, execute=execute, max_workers=1)
    report = recovered.recover()
    assert report == {"adopted": 1, "resumed": 0, "failed": 0}
    assert recovered.status(job["id"])["state"] == "completed"
    assert len(calls) == 1


def test_queued_job_can_be_cancelled_before_executor_starts(tmp_path: Path) -> None:
    gate = threading.Event()

    def execute(job: dict) -> Path:
        assert gate.wait(2)
        destination = Path(job["destination"])
        destination.write_bytes(b"x" * 64)
        return destination

    manager = WanJobManager(root=tmp_path / "jobs", execute=execute, max_workers=1)
    first = manager.submit(
        config=GenerationConfig(mode="t2v-A14B", prompt="First shot.", seed=1),
        destination=tmp_path / "first.mp4",
        quality_tier="production",
    )
    second = manager.submit(
        config=GenerationConfig(mode="t2v-A14B", prompt="Second shot.", seed=2),
        destination=tmp_path / "second.mp4",
        quality_tier="production",
    )

    assert manager.cancel(second["id"])["state"] == "cancelled"
    gate.set()
    assert manager.wait(first["id"], timeout=2)["state"] == "completed"
    assert manager.status(second["id"])["state"] == "cancelled"


def test_restart_recovery_records_lock_conflict_as_a_loud_failure(tmp_path: Path) -> None:
    root = tmp_path / "jobs"
    manager = WanJobManager(root=root, execute=lambda _job: tmp_path / "never.mp4")
    destination = tmp_path / "conflict.mp4"
    job_id = "wan-recovery-conflict"
    (root / f"{job_id}.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": job_id,
                "state": "running",
                "quality_tier": "production",
                "config": GenerationConfig(
                    mode="t2v-A14B", prompt="A static shot.", seed=5
                ).__dict__,
                "destination": str(destination),
                "image": None,
                "last_image": None,
                "events": [],
                "metrics": {},
            }
        )
    )
    manager._destination_lock_path(destination).write_text("wan-other-owner")

    assert manager.recover() == {"adopted": 0, "resumed": 0, "failed": 1}
    failed = manager.status(job_id)
    assert failed["state"] == "failed"
    assert "already owned" in failed["error"]
    assert manager.metrics()["last_recovery"]["failed"] == 1
