import hashlib
import json
import time

import pytest
from fastapi.testclient import TestClient

from diony_producer.app import app
from diony_contracts.fixture import demo_project
from diony_contracts.enums import AssetKind, QCVerdict
from diony_contracts.models import Asset, Brief, QCReport, Take
from diony_contracts.studio import expand_idea
from diony_producer.farm import list_generate_jobs, reclaim_landed_negatives


FAKE_GENERATE = """#!/usr/bin/env python3
import sys
from pathlib import Path
args = sys.argv
assert "--use_prompt_extend" not in args
assert "dashscope" not in " ".join(args).lower()
save = Path(args[args.index("--save_file") + 1])
save.parent.mkdir(parents=True, exist_ok=True)
save.write_bytes(b"\\x00\\x00\\x00\\x18ftypmp42" + b"\\x00" * 200)
"""


def test_farm_persists_and_verifies_the_actual_wan_execution_lane(tmp_path) -> None:
    from diony_producer import farm

    project = demo_project()
    take = project.takes[0]
    negative = tmp_path / "negative_720.mp4"
    negative.write_bytes(b"verified negative" * 8)
    digest = hashlib.sha256(negative.read_bytes()).hexdigest()
    negative.with_suffix(".generation.json").write_text(
        json.dumps(
            {
                "status": "succeeded",
                "backend": "native",
                "selected_lane": "wan22_quality_cli",
                "task": "i2v-A14B",
                "native_width": 1280,
                "native_height": 720,
                "frame_num": 81,
                "sample_steps": 24,
                "sample_solver": "unipc",
                "precision": "bfloat16",
                "negative_conditioning": True,
                "finish_required": True,
                "quality_contract_sha256": "a" * 64,
                "quality_contract_matched": True,
                "attention_backend": "auto",
                "elapsed_seconds": 900.0,
                "output_sha256": digest,
                "output_bytes": negative.stat().st_size,
            }
        )
    )

    farm._load_generation_receipt(take, negative)

    assert take.generation_receipt is not None
    assert take.generation_receipt.selected_lane == "wan22_quality_cli"
    negative.write_bytes(b"tampered negative" * 8)
    with pytest.raises(RuntimeError, match="does not match"):
        farm._load_generation_receipt(take, negative)


def test_reference_and_boundary_quality_floor_cannot_be_weakened(monkeypatch) -> None:
    from diony_producer.farm import _quality_ssim_floor

    monkeypatch.setenv("DIONY_REFERENCE_SSIM_MIN", "0.20")
    monkeypatch.setenv("DIONY_CONTINUITY_SSIM_MIN", "not-a-number")

    assert _quality_ssim_floor("DIONY_REFERENCE_SSIM_MIN") == 0.75
    assert _quality_ssim_floor("DIONY_CONTINUITY_SSIM_MIN") == 0.75


def test_camera_disk_floor_blocks_before_overlay_exhaustion(tmp_path, monkeypatch) -> None:
    from collections import namedtuple

    from diony_producer import farm

    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setenv("DIONY_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("DIONY_OVERLAY_MIN_FREE_GB", "15")
    monkeypatch.setattr(
        farm.shutil,
        "disk_usage",
        lambda path: Usage(600 * 1024**3, 586 * 1024**3, 14 * 1024**3)
        if str(path) == "/"
        else Usage(100 * 1024**3, 20 * 1024**3, 80 * 1024**3),
    )

    message = farm.camera_disk_block()

    assert "overlay" in message.casefold()
    assert "15 GB" in message
    assert "14 GB free" in message


def test_camera_disk_floor_uses_configured_volume_quota_when_fuse_df_is_global(
    tmp_path, monkeypatch
) -> None:
    from collections import namedtuple

    from diony_producer import farm

    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setenv("DIONY_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("DIONY_VOLUME_CAPACITY_GB", "1200")
    monkeypatch.setenv("DIONY_VOLUME_MIN_FREE_GB", "100")
    monkeypatch.setattr(farm, "_volume_used_bytes", lambda _path: 1110 * 1024**3)
    monkeypatch.setattr(
        farm.shutil,
        "disk_usage",
        lambda path: Usage(600 * 1024**3, 2 * 1024**3, 598 * 1024**3)
        if str(path) == "/"
        else Usage(658_000 * 1024**3, 320_000 * 1024**3, 338_000 * 1024**3),
    )

    message = farm.camera_disk_block()

    assert "Vault has 90 GB free" in message
    assert "100 GB safety floor" in message


def test_camera_disk_hold_is_infrastructure_not_take_qc(monkeypatch) -> None:
    from diony_producer import farm

    project = demo_project()
    take = project.takes[0]
    take.qc.verdict = QCVerdict.WARN
    take.qc.notes = "Queued for Wan2.2."
    monkeypatch.setattr(
        farm,
        "camera_disk_block",
        lambda: "GPU overlay has 14 GB free; 15 GB required.",
    )
    monkeypatch.setattr(farm, "camera_task_for", lambda *_args: "i2v-A14B")
    monkeypatch.setattr(farm, "persist_project", lambda *_args: None)
    monkeypatch.setattr(farm, "record_activity", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(farm._PERFORMANCE, "record", lambda **_kwargs: None)

    with pytest.raises(farm.CameraResourceBlocked, match="14 GB free"):
        farm.run_one(project, take)

    assert take.qc is not None
    assert take.qc.verdict != QCVerdict.FAIL


def test_farm_waits_for_storage_and_retries_without_consuming_queue(monkeypatch) -> None:
    from diony_producer import farm

    project = demo_project()
    take = project.takes[0]
    message = "GPU overlay has 14 GB free; 15 GB required."
    checks = iter([message, message, ""])
    disk_checks: list[str] = []
    calls: list[str] = []
    activity: list[str] = []

    def disk_block() -> str:
        value = next(checks, "")
        disk_checks.append(value)
        return value

    def pass_take(_project, current, **_kwargs) -> None:
        calls.append(current.id)
        current.qc.verdict = QCVerdict.PASS

    farm.reset_farm()
    monkeypatch.setenv("DIONY_INFRA_RETRY_SECONDS", "0.01")
    monkeypatch.setattr("diony_wan.runner.prove_generate", lambda: None)
    monkeypatch.setattr(farm, "camera_disk_block", disk_block)
    monkeypatch.setattr(farm, "_attention_plan", lambda *_args: (None, False, None))
    monkeypatch.setattr(farm, "run_one", pass_take)
    monkeypatch.setattr(
        farm,
        "record_activity",
        lambda _src, text, **_kwargs: activity.append(text),
    )
    monkeypatch.setattr("diony_producer.advance.maybe_advance_run", lambda _project: None)

    farm.start_farm(project, [take])

    deadline = time.time() + 5
    status = farm.farm_status()
    while status["busy"] and time.time() < deadline:
        time.sleep(0.01)
        status = farm.farm_status()

    assert len(disk_checks) >= 3
    assert any("retrying automatically" in row for row in activity)
    assert calls == [take.id]
    assert status["done"] == 1
    assert status["queued"] == 0
    assert status["error"] == ""


def test_farm_prewarms_an_approved_resident_before_first_shot_timer(
    tmp_path, monkeypatch
) -> None:
    from diony_producer import farm

    project = demo_project()
    take = project.takes[0]
    checkpoint = tmp_path / "Wan2.2-S2V-14B"
    checkpoint.mkdir()
    order: list[str] = []

    def prewarm(**kwargs) -> bool:
        order.append("prewarm")
        assert kwargs["identifier"].endswith("negative_720.mp4")
        return True

    def pass_take(_project, current, **_kwargs) -> None:
        order.append("shot")
        current.qc.verdict = QCVerdict.PASS

    farm.reset_farm()
    monkeypatch.setenv("DIONY_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path / "vault"))
    monkeypatch.setattr("diony_wan.runner.prove_generate", lambda: None)
    monkeypatch.setattr("diony_contracts.live.ckpt_dir", lambda _task: checkpoint)
    monkeypatch.setattr(
        "diony_wan.storage.prepare_checkpoint", lambda _task, source: checkpoint
    )
    monkeypatch.setattr("diony_wan.runner.prewarm_validated_resident", prewarm)
    monkeypatch.setattr(farm, "camera_task_for", lambda *_args: "s2v-14B")
    monkeypatch.setattr(farm, "camera_disk_block", lambda: "")
    monkeypatch.setattr(farm, "_attention_plan", lambda *_args: ("cudnn_sdpa", False, None))
    monkeypatch.setattr(farm, "run_one", pass_take)
    monkeypatch.setattr(farm, "record_activity", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("diony_producer.advance.maybe_advance_run", lambda _project: None)

    farm.start_farm(project, [take])

    deadline = time.time() + 5
    status = farm.farm_status()
    while status["busy"] and time.time() < deadline:
        time.sleep(0.01)
        status = farm.farm_status()

    assert order == ["prewarm", "shot"]
    assert status["error"] == ""

SLOW_GENERATE = """#!/usr/bin/env python3
import sys, time
from pathlib import Path
time.sleep(0.8)
save = Path(sys.argv[sys.argv.index("--save_file") + 1])
save.parent.mkdir(parents=True, exist_ok=True)
save.write_bytes(b"\\x00\\x00\\x00\\x18ftypmp42" + b"\\x00" * 200)
"""


def test_generate_process_scan_ignores_shell_commands_that_only_quote_the_camera(monkeypatch) -> None:
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    process_table = """
101 00:03 /bin/zsh -c echo '/usr/bin/python3 /opt/wan22/generate.py --task t2v-A14B'
202 00:02 /usr/bin/python3 /opt/wan22/generate.py --task t2v-A14B --base_seed 42 --save_file /diony/vault/projects/p/takes/sh-010-t1/negative_720.mp4
"""
    monkeypatch.setattr(
        "diony_producer.farm.subprocess.check_output",
        lambda *args, **kwargs: process_table,
    )

    jobs = list_generate_jobs()

    assert [job["pid"] for job in jobs] == [202]
    assert jobs[0]["take_id"] == "sh-010-t1"


def test_s2v_expected_passes_match_the_single_bucket_five_second_audio_contract(tmp_path) -> None:
    import wave

    from diony_producer.farm import _s2v_expected_passes

    audio = tmp_path / "clean-dialogue.wav"
    with wave.open(str(audio), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(48_000)
        output.writeframes(b"\x00\x00" * (48_000 * 5))

    assert _s2v_expected_passes("s2v-14B", str(audio), 80) == 2
    assert _s2v_expected_passes("s2v-14B", str(audio), 84) == 1
    assert _s2v_expected_passes("s2v-14B", str(audio), 88) == 1
    assert _s2v_expected_passes("t2v-A14B", str(audio), 80) == 1


def _live_env(tmp_path, monkeypatch, script: str) -> None:
    monkeypatch.delenv("DIONY_DRY_RUN", raising=False)
    monkeypatch.delenv("DIONY_WAN_URL", raising=False)
    monkeypatch.setenv("DIONY_LIVE", "1")
    gen = tmp_path / "generate.py"
    gen.write_text(script)
    gen.chmod(0o755)
    ckpt = tmp_path / "t2v"
    ckpt.mkdir(exist_ok=True)
    (ckpt / "w.json").write_text("{}")
    monkeypatch.setenv("DIONY_WAN_GENERATE", str(gen))
    monkeypatch.setenv("WAN_CKPT_T2V", str(ckpt))
    monkeypatch.setenv("DIONY_PYTHON", "python3")
    monkeypatch.setenv("DIONY_WAN_REPO", str(tmp_path))
    monkeypatch.setenv("DIONY_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path / "vault"))


def test_b200_farm_rebinds_operator_proof_to_one_project_canary(tmp_path, monkeypatch) -> None:
    import json

    from diony_contracts.cluster import write_site_lock
    from diony_producer import farm
    from diony_wan.attention_qualification import build_operator_receipt

    monkeypatch.setenv("DIONY_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path / "vault"))
    write_site_lock(
        {
            "sku": "b200-v1",
            "compute_path": "b200",
            "gpu_ready": True,
            "compute": {"path": "b200", "profile": "b200-v1"},
        }
    )
    evidence = {
        "gpu_name": "NVIDIA B200",
        "compute_capability": "10.0",
        "shape": [1, 40, 75600, 128],
        "dtype": "bfloat16",
        "candidate_backend": "cudnn_sdpa",
        "baseline_ms": [285.85, 285.39, 285.37],
        "candidate_ms": [92.08, 91.19, 91.18],
        "finite": True,
        "max_abs_delta": 0.000244140625,
        "mean_abs_delta": 0.0000111,
    }
    receipt_path = tmp_path / "vault" / "qualification" / "wan-cudnn-sdpa-validation.json"
    receipt_path.parent.mkdir(parents=True)
    receipt_path.write_text(
        json.dumps(
            build_operator_receipt(
                evidence,
                scope_project_id="studio-prior",
                approved_by="Diony B200 qualification",
            )
        )
    )
    project = demo_project()
    take = project.takes[0]

    backend, canary, selected_receipt = farm._attention_plan(project, take)

    rebound = json.loads(receipt_path.read_text())
    assert backend == "cudnn_sdpa"
    assert canary is True
    assert selected_receipt == receipt_path
    assert rebound["scope_project_id"] == project.id
    assert rebound["quality_canary_take_id"] == take.id
    assert (receipt_path.parent / "projects" / "studio-prior" / receipt_path.name).is_file()


def test_live_roll_writes_mp4(tmp_path, monkeypatch) -> None:
    _live_env(tmp_path, monkeypatch, FAKE_GENERATE)
    client = TestClient(app)
    client.post("/api/reset")
    opened = client.post(
        "/api/studio/open",
        json={
            "idea": "A lighthouse keeper paints the fog the color of warnings.",
            "customer_kind": "feature",
            "target_minutes": 2,
        },
    )
    assert opened.status_code == 200
    rolled = client.post("/api/studio/roll")
    assert rolled.status_code == 200
    farm = client.get("/api/farm").json()
    assert farm["live"] is True
    deadline = time.time() + 15
    mp4 = None
    while time.time() < deadline:
        project = client.get("/api/project").json()
        take = project["takes"][0]
        rel = take["artifacts"][0]["path"]
        path = tmp_path / "vault" / rel
        if path.is_file() and path.stat().st_size > 32 and rel.endswith(".mp4"):
            mp4 = path
            break
        time.sleep(0.2)
    assert mp4 is not None, "farm should write a Wan 720p mp4"
    assert mp4.read_bytes()[:8]  # clip bytes exist
    catalog = client.get("/api/catalog").json()
    assert catalog["live"] is True
    assert catalog["dry_run"] is False
    idle = time.time() + 20
    while time.time() < idle:
        if not client.get("/api/farm").json()["busy"]:
            return
        time.sleep(0.2)


def test_farm_busy_before_clip_lands(tmp_path, monkeypatch) -> None:
    _live_env(tmp_path, monkeypatch, SLOW_GENERATE)
    client = TestClient(app)
    client.post("/api/reset")
    client.post(
        "/api/studio/open",
        json={
            "idea": "A tungsten newsroom at 2 a.m., rain on the glass.",
            "customer_kind": "series",
            "target_minutes": 1,
        },
    )
    rolled = client.post("/api/studio/roll")
    assert rolled.status_code == 200
    farm = client.get("/api/farm").json()
    assert farm["live"] is True
    assert farm["busy"] is True
    deadline = time.time() + 20
    while time.time() < deadline:
        project = client.get("/api/project").json()
        rel = project["takes"][0]["artifacts"][0]["path"]
        path = tmp_path / "vault" / rel
        if path.is_file() and path.stat().st_size > 32:
            break
        time.sleep(0.2)
    else:
        raise AssertionError("slow farm never wrote the clip")
    idle = time.time() + 20
    while time.time() < idle:
        if not client.get("/api/farm").json()["busy"]:
            return
        time.sleep(0.2)


def test_studio_new_cannot_bypass_explicit_stop_for_a_slow_farm(tmp_path, monkeypatch) -> None:
    _live_env(tmp_path, monkeypatch, SLOW_GENERATE)
    client = TestClient(app)
    client.post("/api/reset")
    client.post(
        "/api/studio/open",
        json={
            "idea": "A tungsten newsroom at 2 a.m., rain on the glass.",
            "customer_kind": "series",
            "target_minutes": 1,
        },
    )
    rolled = client.post("/api/studio/roll")
    assert rolled.status_code == 200
    assert client.get("/api/farm").json()["busy"] is True
    fresh = client.post("/api/studio/new")
    assert fresh.status_code == 409
    assert "STOP" in fresh.json()["detail"]
    assert client.get("/api/project").json()["shots"]
    stopped = client.post("/api/production/stop", json={"disposition": "delete"})
    assert stopped.status_code == 200
    deadline = time.time() + 8
    while time.time() < deadline:
        if not client.get("/api/farm").json()["busy"]:
            break
        time.sleep(0.05)
    else:
        raise AssertionError("Refresh left the farm busy")
    assert client.get("/api/farm").json()["queued"] == 0
    assert client.get("/api/project").json()["production_stop"] is not None


def test_farm_prove_fail_stamps_every_take_once(tmp_path, monkeypatch) -> None:
    _live_env(tmp_path, monkeypatch, FAKE_GENERATE)

    def boom() -> None:
        raise RuntimeError("No module named 'dashscope'")

    monkeypatch.setattr("diony_wan.runner.prove_generate", boom)
    client = TestClient(app)
    client.post("/api/reset")
    client.post(
        "/api/studio/open",
        json={
            "idea": "A night game under tungsten towers, crowd in the rain.",
            "customer_kind": "feature",
            "target_minutes": 1,
        },
    )
    rolled = client.post("/api/studio/roll")
    assert rolled.status_code == 200
    deadline = time.time() + 5
    farm = {}
    while time.time() < deadline:
        farm = client.get("/api/farm").json()
        if not farm["busy"] and "dashscope" in (farm.get("error") or ""):
            break
        time.sleep(0.05)
    else:
        raise AssertionError(f"prove fail never landed: {farm}")
    assert farm["busy"] is False
    assert "dashscope" in (farm.get("error") or "")
    project = client.get("/api/project").json()
    takes = project["takes"]
    assert takes
    for take in takes:
        assert take["qc"]["verdict"] == "fail"
        assert "dashscope" in (take["qc"].get("notes") or "")


def test_resume_camera_does_not_spawn_a_farm(monkeypatch) -> None:
    started: list[int] = []
    monkeypatch.setattr("diony_producer.farm.start_farm", lambda *a, **k: started.append(1))
    monkeypatch.setattr("diony_producer.farm.adopt_live_farm", lambda p: None)
    monkeypatch.setattr("diony_producer.farm.reclaim_landed_negatives", lambda p: 0)
    monkeypatch.setattr("diony_producer.farm.project_matching_generate", lambda p: None)
    from diony_contracts.models import Bible, Character, Project
    from diony_producer.farm import resume_camera

    project = Project(
        id="studio-resume",
        bible=Bible(
            title="Resume",
            logline="A show that already has a camera.",
            visual_grammar="35mm.",
            world="One room.",
            characters=[Character(id="char-a", name="A")],
        ),
    )
    out = resume_camera(project)
    assert out.id == "studio-resume"
    assert started == []


def test_resume_camera_requeues_an_infrastructure_held_run(monkeypatch) -> None:
    from diony_producer import farm

    project = demo_project()
    project.mark_production_start()
    project.mark_production_end()
    selected = project.take(project.shots[0].selected_take_id)
    selected.qc.verdict = QCVerdict.FAIL
    selected.qc.notes = (
        "GPU overlay has less than the 15 GB safety floor. "
        "Scratch stays on the vault; free container disk before Wan loads another clip."
    )
    queued: list[list[str]] = []
    monkeypatch.setattr(farm, "project_matching_generate", lambda current: None)
    monkeypatch.setattr(farm, "adopt_live_farm", lambda current: None)
    monkeypatch.setattr(farm, "reclaim_landed_negatives", lambda current: 0)
    monkeypatch.setattr(farm, "list_generate_jobs", lambda: [])
    monkeypatch.setattr(farm, "live_camera", lambda: True)
    monkeypatch.setattr(farm, "enter_farm", lambda: None)
    monkeypatch.setattr(
        farm,
        "start_farm",
        lambda current, takes: queued.append([take.id for take in takes]),
    )

    resumed = farm.resume_camera(project)

    expected = [take.id for take in farm._selected_missing_takes(resumed, exclude_ids=set())]
    assert expected
    assert queued == [expected]
    assert resumed.production_ended_at is None
    assert selected.qc.verdict == QCVerdict.WARN


def test_camera_disk_block_when_vault_is_full(monkeypatch) -> None:
    from types import SimpleNamespace

    from diony_producer.farm import camera_disk_block

    monkeypatch.setattr(
        "diony_producer.farm.shutil.disk_usage",
        lambda path: SimpleNamespace(free=100),
    )
    assert "Vault" in camera_disk_block()


def test_camera_lock_roundtrip() -> None:
    from diony_producer.farm import clear_camera_lock, read_camera_lock, write_camera_lock

    write_camera_lock({"take_id": "sh-010-t1", "save": "/tmp/neg.mp4"})
    assert read_camera_lock()["take_id"] == "sh-010-t1"
    clear_camera_lock()
    assert read_camera_lock() == {}


def test_adopted_output_is_not_final_until_generate_exits(tmp_path, monkeypatch) -> None:
    from diony_producer.farm import generate_output_finalized

    output = tmp_path / "negative_720.mp4"
    output.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"writing" * 64)
    monkeypatch.setattr("diony_producer.farm.os.kill", lambda pid, signal: None)

    assert generate_output_finalized(1234, output) is False

    def exited(pid, signal):
        raise ProcessLookupError(pid)

    monkeypatch.setattr("diony_producer.farm.os.kill", exited)
    assert generate_output_finalized(1234, output) is True


def test_adopted_camera_job_auto_retakes_failed_continuity(tmp_path, monkeypatch) -> None:
    from diony_producer import farm as farm_module

    project = demo_project()
    shot = project.shots[0]
    take = project.takes_for(shot.id)[0]
    project.takes = [item for item in project.takes if item.shot_id != shot.id]
    project.takes.insert(0, take)
    shot.selected_take_id = take.id
    output = tmp_path / "projects" / project.id / "takes" / take.id / "negative_720.mp4"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"landed" * 64)
    job = {"pid": 1234, "save": str(output), "take_id": take.id}
    queued: list[list[str]] = []

    def attach_failed(_project, current, *args, **kwargs) -> None:
        current.qc = QCReport(
            id=f"qc-{current.id}",
            take_id=current.id,
            shot_id=current.shot_id,
            verdict=QCVerdict.FAIL,
            identity_ok=True,
            morph_ok=True,
            flicker_ok=True,
            duration_ok=True,
            legal_ok=True,
            continuity_from_take_id="prior-take",
            continuity_similarity=0.6778,
            continuity_verified=False,
            notes="Shot boundary continuity failed (first/last SSIM 0.6778; required 0.75).",
        )

    monkeypatch.setattr(farm_module, "list_generate_jobs", lambda: [job])
    monkeypatch.setattr(farm_module, "generate_process_alive", lambda pid: False)
    monkeypatch.setattr(farm_module, "attach_negative", attach_failed)
    monkeypatch.setattr(farm_module.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        farm_module,
        "start_farm",
        lambda current, takes: queued.append([item.id for item in takes]),
    )

    farm_module.adopt_live_farm(project)

    deadline = time.time() + 2
    while not queued and time.time() < deadline:
        time.sleep(0.01)

    assert queued[0][0] == f"{shot.id}-t2"
    assert shot.selected_take_id == f"{shot.id}-t2"


def test_adopted_b200_canary_rebinds_its_accelerated_lane_to_the_retake(
    tmp_path, monkeypatch
) -> None:
    from diony_producer import farm as farm_module

    project = demo_project()
    shot = project.shots[0]
    take = project.takes_for(shot.id)[0]
    project.takes = [item for item in project.takes if item.shot_id != shot.id]
    project.takes.insert(0, take)
    shot.selected_take_id = take.id
    output = tmp_path / "projects" / project.id / "takes" / take.id / "negative_720.mp4"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"landed" * 64)
    receipt = tmp_path / "wan-cudnn-sdpa-validation.json"
    receipt.write_text("{}")
    job = {"pid": 1234, "save": str(output), "take_id": take.id}
    rebound: list[tuple[str, str]] = []
    queued: list[list[str]] = []

    def attach_failed(_project, current, *args, **kwargs) -> None:
        current.qc = QCReport(
            id=f"qc-{current.id}",
            take_id=current.id,
            shot_id=current.shot_id,
            verdict=QCVerdict.FAIL,
            identity_ok=True,
            morph_ok=True,
            flicker_ok=True,
            duration_ok=True,
            legal_ok=True,
            audiovisual_measurement_qualified=True,
            notes="Voice performance failed: qualified audiovisual QC rejected this take.",
        )

    monkeypatch.setattr(farm_module, "list_generate_jobs", lambda: [job])
    monkeypatch.setattr(farm_module, "generate_process_alive", lambda pid: False)
    monkeypatch.setattr(farm_module, "attach_negative", attach_failed)
    monkeypatch.setattr(farm_module, "_bound_attention_canary", lambda *_args: receipt)
    monkeypatch.setattr(
        farm_module,
        "_rebind_attention_canary",
        lambda _project, failed, retry, _path: rebound.append((failed.id, retry.id)),
    )
    monkeypatch.setattr(farm_module.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        farm_module,
        "start_farm",
        lambda current, takes: queued.append([item.id for item in takes]),
    )

    farm_module.adopt_live_farm(project)

    deadline = time.time() + 2
    while not queued and time.time() < deadline:
        time.sleep(0.01)

    assert rebound == [(take.id, f"{shot.id}-t2")]
    assert queued[0][0] == f"{shot.id}-t2"


def test_unqualified_av_diagnostic_never_spends_another_full_camera_take() -> None:
    from diony_producer.farm import _auto_retake_quality_failure

    project = demo_project()
    take = project.takes[0]
    take.qc = QCReport(
        id=f"qc-{take.id}",
        take_id=take.id,
        shot_id=take.shot_id,
        verdict=QCVerdict.FAIL,
        identity_ok=True,
        morph_ok=True,
        flicker_ok=True,
        duration_ok=True,
        legal_ok=True,
        audiovisual_measurement_qualified=False,
        notes=(
            "Voice performance failed: lip-sync confidence 0.8013 is below 0.90; "
            "audiovisual offset p95 83.333 ms exceeds 40 ms."
        ),
    )

    assert _auto_retake_quality_failure(take) is False


def test_adoption_never_resurrects_an_unselected_missing_take(monkeypatch) -> None:
    from diony_producer import farm as farm_module

    project = demo_project()
    selected = project.takes[0]
    project.shot(selected.shot_id).selected_take_id = selected.id
    stale = selected.model_copy(deep=True)
    stale.id = f"{selected.shot_id}-aborted"
    stale.attempt = selected.attempt + 1
    stale.artifacts = []
    stale.qc = None
    project.takes.append(stale)
    monkeypatch.setattr(farm_module, "take_has_live_negative", lambda take, project: False)

    missing = farm_module._selected_missing_takes(project, exclude_ids=set())

    assert selected in missing
    assert stale not in missing


def test_reclaim_never_adopts_a_legacy_cross_project_negative(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DIONY_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path / "vault"))
    project = demo_project()
    take = project.takes[0]
    take.artifacts = []
    take.qc = None
    legacy = tmp_path / "vault" / "takes" / take.id / "negative_720.mp4"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"old-run" * 32)

    assert reclaim_landed_negatives(project) == 0
    assert take.artifacts == []


def test_reclaim_does_not_probe_an_inflight_generate_output(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DIONY_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path / "vault"))
    project = demo_project()
    take = project.takes[0]
    take.artifacts = []
    take.qc = None
    output = tmp_path / "vault" / "projects" / project.id / "takes" / take.id / "negative_720.mp4"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"still-writing" * 32)
    monkeypatch.setattr(
        "diony_producer.farm.list_generate_jobs",
        lambda: [{"pid": 1234, "save": str(output), "take_id": take.id}],
    )

    assert reclaim_landed_negatives(project) == 0
    assert take.artifacts == []
    assert take.qc is None


def test_farm_auto_retakes_failed_continuity_and_continues_rundown(monkeypatch) -> None:
    from diony_producer import farm as farm_module

    project = demo_project()
    calls: list[str] = []
    first_shot_id = project.shots[0].id
    first_take = project.takes_for(first_shot_id)[0]
    project.takes = [take for take in project.takes if take.shot_id != first_shot_id]
    project.takes.insert(0, first_take)
    project.shot(first_shot_id).selected_take_id = first_take.id

    def fail_once_then_pass(_project, take, **_kwargs) -> None:
        calls.append(take.id)
        if take.shot_id == first_shot_id and take.attempt == 1:
            take.qc = QCReport(
                id=f"qc-{take.id}",
                take_id=take.id,
                shot_id=take.shot_id,
                verdict=QCVerdict.FAIL,
                identity_ok=True,
                morph_ok=True,
                flicker_ok=True,
                duration_ok=True,
                legal_ok=True,
                continuity_from_take_id="prior-take",
                continuity_similarity=0.6778,
                continuity_verified=False,
                notes="Shot boundary continuity failed (first/last SSIM 0.6778; required 0.75).",
            )
            return
        take.qc = QCReport(
            id=f"qc-{take.id}",
            take_id=take.id,
            shot_id=take.shot_id,
            verdict=QCVerdict.PASS,
            identity_ok=True,
            morph_ok=True,
            flicker_ok=True,
            duration_ok=True,
            legal_ok=True,
            notes="AutoQC passed.",
        )

    farm_module.reset_farm()
    monkeypatch.setattr("diony_wan.runner.prove_generate", lambda: None)
    monkeypatch.setattr(farm_module, "run_one", fail_once_then_pass)
    monkeypatch.setattr("diony_producer.advance.maybe_advance_run", lambda project: None)
    farm_module.start_farm(project, list(project.takes))

    deadline = time.time() + 5
    status = farm_module.farm_status()
    while status["busy"] and time.time() < deadline:
        time.sleep(0.01)
        status = farm_module.farm_status()

    first_shot_takes = project.takes_for(first_shot_id)
    assert calls[:2] == [f"{first_shot_id}-t1", f"{first_shot_id}-t2"]
    assert calls[2:] == [take.id for take in project.takes if take.shot_id != first_shot_id]
    assert len(first_shot_takes) == 2
    assert first_shot_takes[0].qc.verdict == QCVerdict.FAIL
    assert first_shot_takes[1].qc.verdict == QCVerdict.PASS
    assert project.shot(first_shot_id).selected_take_id == first_shot_takes[1].id
    assert status["queued"] == 0
    assert status["error"] == ""


def test_b200_attention_canary_auto_retakes_voice_qc_without_losing_rundown(
    tmp_path, monkeypatch
) -> None:
    from diony_producer import farm as farm_module
    from diony_wan.attention_qualification import bind_project_canary, build_operator_receipt

    project = demo_project()
    first_shot_id = project.shots[0].id
    first_take = project.takes_for(first_shot_id)[0]
    project.takes = [take for take in project.takes if take.shot_id != first_shot_id]
    project.takes.insert(0, first_take)
    project.shot(first_shot_id).selected_take_id = first_take.id
    receipt_path = tmp_path / "wan-cudnn-sdpa-validation.json"
    receipt_path.write_text(
        json.dumps(
            bind_project_canary(
                build_operator_receipt(
                    {
                        "gpu_name": "NVIDIA B200",
                        "compute_capability": "10.0",
                        "shape": [1, 40, 75600, 128],
                        "dtype": "bfloat16",
                        "candidate_backend": "cudnn_sdpa",
                        "baseline_ms": [285.85, 285.39, 285.37],
                        "candidate_ms": [92.08, 91.19, 91.18],
                        "finite": True,
                        "max_abs_delta": 0.000244140625,
                        "mean_abs_delta": 0.0000111,
                    },
                    scope_project_id=project.id,
                    approved_by="Diony B200 qualification",
                ),
                project_id=project.id,
                take_id=first_take.id,
            )
        )
    )
    calls: list[str] = []
    canary_pending = True

    def fail_once_then_pass(_project, take, **_kwargs) -> None:
        calls.append(take.id)
        take.qc = QCReport(
            id=f"qc-{take.id}",
            take_id=take.id,
            shot_id=take.shot_id,
            verdict=(
                QCVerdict.FAIL
                if take.shot_id == first_shot_id and take.attempt == 1
                else QCVerdict.PASS
            ),
            identity_ok=True,
            morph_ok=True,
                flicker_ok=True,
                duration_ok=True,
                legal_ok=True,
                audiovisual_measurement_qualified=True,
                notes=(
                    "Voice performance failed: lip-sync confidence 0.7956 is below 0.90."
                if take.shot_id == first_shot_id and take.attempt == 1
                else "AutoQC passed."
            ),
        )

    def attention_plan(_project, _take):
        return (
            ("cudnn_sdpa", True, receipt_path)
            if canary_pending
            else ("cudnn_sdpa", False, receipt_path)
        )

    def complete_canary(_project, _take, _receipt):
        nonlocal canary_pending
        canary_pending = False

    farm_module.reset_farm()
    monkeypatch.setattr("diony_wan.runner.prove_generate", lambda: None)
    monkeypatch.setattr(farm_module, "_attention_plan", attention_plan)
    monkeypatch.setattr(farm_module, "_complete_attention_canary", complete_canary)
    monkeypatch.setattr(farm_module, "run_one", fail_once_then_pass)
    monkeypatch.setattr("diony_producer.advance.maybe_advance_run", lambda project: None)
    farm_module.start_farm(project, list(project.takes))

    deadline = time.time() + 5
    status = farm_module.farm_status()
    while status["busy"] and time.time() < deadline:
        time.sleep(0.01)
        status = farm_module.farm_status()

    assert calls[:2] == [f"{first_shot_id}-t1", f"{first_shot_id}-t2"]
    assert calls[2:] == [take.id for take in project.takes if take.shot_id != first_shot_id]
    assert project.shot(first_shot_id).selected_take_id == f"{first_shot_id}-t2"
    assert json.loads(receipt_path.read_text())["quality_canary_take_id"] == f"{first_shot_id}-t2"
    assert status["queued"] == 0
    assert status["error"] == ""


def test_farm_preserves_rundown_when_pre_dispatch_raises(monkeypatch) -> None:
    """An input/preflight exception must not pop every waiting Take and report idle."""
    from diony_producer import farm as farm_module

    project = demo_project()
    takes = list(project.takes[:3])
    calls: list[str] = []

    def fail_preflight(_project, take, **_kwargs) -> None:
        calls.append(take.id)
        raise RuntimeError("I2V reference lock requires a readable still image")

    farm_module.reset_farm()
    monkeypatch.setattr("diony_wan.runner.prove_generate", lambda: None)
    monkeypatch.setattr(farm_module, "_attention_plan", lambda *_args: (None, False, None))
    monkeypatch.setattr(farm_module, "run_one", fail_preflight)
    monkeypatch.setattr("diony_producer.advance.maybe_advance_run", lambda project: None)
    farm_module.start_farm(project, takes)

    deadline = time.time() + 5
    status = farm_module.farm_status()
    while status["busy"] and time.time() < deadline:
        time.sleep(0.01)
        status = farm_module.farm_status()

    assert calls == [takes[0].id]
    assert status["busy"] is False
    assert status["queued"] == len(takes)
    assert "readable still image" in status["error"]


def test_farm_stops_for_human_only_after_four_failed_auto_retakes(monkeypatch) -> None:
    from diony_producer import farm as farm_module

    project = demo_project()
    shot = project.shots[0]
    first_take = project.takes_for(shot.id)[0]
    project.takes = [take for take in project.takes if take.shot_id != shot.id]
    project.takes.insert(0, first_take)
    shot.selected_take_id = first_take.id
    calls: list[str] = []

    def fail_continuity(_project, take, **_kwargs) -> None:
        calls.append(take.id)
        take.qc = QCReport(
            id=f"qc-{take.id}",
            take_id=take.id,
            shot_id=take.shot_id,
            verdict=QCVerdict.FAIL,
            identity_ok=True,
            morph_ok=True,
            flicker_ok=True,
            duration_ok=True,
            legal_ok=True,
            continuity_from_take_id="prior-take",
            continuity_similarity=0.6778,
            continuity_verified=False,
            notes="Shot boundary continuity failed (first/last SSIM 0.6778; required 0.75).",
        )

    farm_module.reset_farm()
    monkeypatch.setattr("diony_wan.runner.prove_generate", lambda: None)
    monkeypatch.setattr(farm_module, "run_one", fail_continuity)
    monkeypatch.setattr("diony_producer.advance.maybe_advance_run", lambda project: None)
    farm_module.start_farm(project, [first_take])

    deadline = time.time() + 5
    status = farm_module.farm_status()
    while status["busy"] and time.time() < deadline:
        time.sleep(0.01)
        status = farm_module.farm_status()

    assert calls == [f"{shot.id}-t{attempt}" for attempt in range(1, 5)]
    assert len(project.takes_for(shot.id)) == 4
    assert project.shot(shot.id).selected_take_id == f"{shot.id}-t4"
    assert status["queued"] == 0
    assert status["error"] == "Shot boundary continuity failed (first/last SSIM 0.6778; required 0.75)."
    assert project.production_ended_at is not None


def test_resume_camera_auto_retries_a_persisted_selected_quality_failure(monkeypatch) -> None:
    from diony_producer import farm as farm_module

    project = demo_project()
    shot = project.shots[0]
    failed = project.takes_for(shot.id)[0]
    project.takes = [take for take in project.takes if take.shot_id != shot.id]
    project.takes.insert(0, failed)
    shot.selected_take_id = failed.id
    failed.qc = QCReport(
        id=f"qc-{failed.id}",
        take_id=failed.id,
        shot_id=failed.shot_id,
        verdict=QCVerdict.FAIL,
        identity_ok=True,
        morph_ok=True,
        flicker_ok=True,
        duration_ok=True,
        legal_ok=True,
        continuity_from_take_id="prior-take",
        continuity_similarity=0.6778,
        continuity_verified=False,
        notes="Shot boundary continuity failed (first/last SSIM 0.6778; required 0.75).",
    )
    queued: list[list[str]] = []
    monkeypatch.setattr(farm_module, "project_matching_generate", lambda current: None)
    monkeypatch.setattr(farm_module, "adopt_live_farm", lambda current: None)
    monkeypatch.setattr(farm_module, "reclaim_landed_negatives", lambda current: 0)
    monkeypatch.setattr(farm_module, "list_generate_jobs", lambda: [])
    monkeypatch.setattr(farm_module, "live_camera", lambda: True)
    monkeypatch.setattr(
        farm_module,
        "start_farm",
        lambda current, takes: queued.append([take.id for take in takes]),
    )

    resumed = farm_module.resume_camera(project)

    assert resumed.shot(shot.id).selected_take_id == f"{shot.id}-t2"
    expected_waiting = [
        take.id
        for take in resumed.takes
        if take.shot_id != shot.id and take.id == resumed.shot(take.shot_id).selected_take_id
    ]
    assert queued == [[f"{shot.id}-t2", *expected_waiting]]
    assert failed.qc.verdict == QCVerdict.FAIL


def test_same_background_lock_rejects_late_reference_drift(tmp_path, monkeypatch) -> None:
    """A matching cream canvas must not hide a changed character or inserted text card."""
    from diony_producer import farm as farm_module

    monkeypatch.setenv("DIONY_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path / "vault"))
    reference = Asset(
        id="ast-character",
        kind=AssetKind.IMAGE,
        filename="character.png",
        mime="image/png",
        bytes=64,
        path="assets/ast-character/character.png",
        sha256="reference-sha",
    )
    project = expand_idea(
        Brief(
            idea="Using this attached character and same background, animate the character directly.",
            customer_kind="series",
            production_kind="sample",
            sample_seconds=5,
            attachment_ids=[reference.id],
        ),
        assets=[reference],
    )
    shot = project.shots[0]
    take = Take(id="sh-010-t1", shot_id=shot.id, attempt=1, engine=shot.engine, seed=shot.seed)
    project.takes = [take]
    relative = f"projects/{project.id}/takes/{take.id}/negative_720.mp4"
    negative = tmp_path / "vault" / relative
    negative.parent.mkdir(parents=True)
    negative.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"drift" * 32)
    monkeypatch.setattr(
        farm_module,
        "probe_media",
        lambda *args, **kwargs: {
            "width": 1280,
            "height": 720,
            "fps": 23.976,
            "frames": 120,
        },
    )
    monkeypatch.setattr(
        farm_module,
        "_reference_similarities",
        lambda *args, **kwargs: {
            "first": 0.99,
            "quarter": 0.84,
            "middle": 0.79,
            "three_quarter": 0.76,
            "last": 0.71,
        },
    )

    farm_module.attach_negative(project, take, relative, shot.duration_frames, live=True)

    assert take.qc is not None
    assert take.qc.verdict == QCVerdict.FAIL
    assert "required 0.75" in take.qc.notes


def test_under_delivered_negative_fails_the_duration_gate(tmp_path, monkeypatch) -> None:
    """A booked frame may be trimmed from delivered picture, never stretched into it."""
    from diony_producer import farm as farm_module

    monkeypatch.setenv("DIONY_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path / "vault"))
    monkeypatch.delenv("DIONY_MEASURED_QC_REQUIRED", raising=False)
    project = expand_idea(
        Brief(
            idea="A courier delivers a letter that rewrites the city map.",
            customer_kind="feature",
            target_minutes=0.5,
        )
    )
    shot = project.shots[0]
    assert shot.duration_frames == 81
    take = Take(id="sh-010-t1", shot_id=shot.id, attempt=1, engine=shot.engine, seed=shot.seed)
    project.takes = [take]
    relative = f"projects/{project.id}/takes/{take.id}/negative_720.mp4"
    negative = tmp_path / "vault" / relative
    negative.parent.mkdir(parents=True)
    negative.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"short" * 32)
    monkeypatch.setattr(
        farm_module,
        "probe_media",
        lambda *args, **kwargs: {"width": 1280, "height": 720, "fps": 23.976, "frames": 76},
    )

    farm_module.attach_negative(project, take, relative, shot.duration_frames, live=True)

    assert take.qc is not None
    assert take.qc.verdict == QCVerdict.FAIL
    assert take.qc.duration_ok is False
    assert take.qc.notes.startswith("Duration mismatch")
    # An honest shortfall is a camera failure the farm may retake automatically.
    assert any(take.qc.notes.startswith(p) for p in farm_module._AUTO_RETAKE_QC_PREFIXES)


def test_legacy_over_booked_shot_reconciles_to_measured_camera_truth(tmp_path, monkeypatch) -> None:
    """A pre-lane Shot that booked 120 frames keeps only what Wan delivered."""
    from diony_producer import farm as farm_module

    monkeypatch.setenv("DIONY_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path / "vault"))
    monkeypatch.delenv("DIONY_MEASURED_QC_REQUIRED", raising=False)
    project = expand_idea(
        Brief(
            idea="A courier delivers a letter that rewrites the city map.",
            customer_kind="feature",
            target_minutes=0.5,
        )
    )
    shot = project.shots[0]
    shot.duration_frames = 120  # persisted before the lane-true booking contract
    take = Take(id="sh-010-t1", shot_id=shot.id, attempt=1, engine=shot.engine, seed=shot.seed)
    project.takes = [take]
    relative = f"projects/{project.id}/takes/{take.id}/negative_720.mp4"
    negative = tmp_path / "vault" / relative
    negative.parent.mkdir(parents=True)
    negative.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"legacy" * 32)
    monkeypatch.setattr(
        farm_module,
        "probe_media",
        lambda *args, **kwargs: {"width": 1280, "height": 720, "fps": 23.976, "frames": 81},
    )

    farm_module.attach_negative(project, take, relative, 120, live=True)

    assert take.qc is not None
    assert take.qc.duration_ok is True
    assert take.qc.verdict == QCVerdict.PASS
    assert shot.duration_frames == 81


def test_auto_retake_prefers_the_directive_and_jumps_the_seed(tmp_path, monkeypatch) -> None:
    from diony_producer import farm as farm_module

    monkeypatch.setenv("DIONY_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path / "vault"))
    project = expand_idea(
        Brief(
            idea="A courier delivers a letter that rewrites the city map.",
            customer_kind="feature",
            target_minutes=0.5,
        )
    )
    shot = project.shots[0]
    take = farm_module.new_generation_take(project, shot, live=True)
    project.takes = [take]
    shot.selected_take_id = take.id
    take.qc.verdict = QCVerdict.FAIL
    take.qc.notes = "Measured motion failed (delta p95 0.0002); the plate is effectively frozen."
    calls = {"directive": 0, "repair": 0}

    def fake_directive(project_arg, shot_arg, take_arg):
        calls["directive"] += 1
        return True

    def fake_repair(shot_arg, notes):
        calls["repair"] += 1
        return True

    monkeypatch.setattr(farm_module, "direct_retake", fake_directive)
    monkeypatch.setattr(farm_module, "repair_wan_prompt_for_qc", fake_repair)

    retry = farm_module.auto_retake_for_qc_failure(project, take)

    assert retry is not None
    assert calls == {"directive": 1, "repair": 0}
    # The retake seed is a distant deterministic jump, not an adjacent draw.
    assert retry.seed != shot.seed
    assert abs(retry.seed - shot.seed) > 1000
    again = farm_module.new_generation_take(project, shot, attempt=retry.attempt, live=True)
    assert again.seed == retry.seed

    # Without a directive, the keyword repair table still runs.
    monkeypatch.setattr(farm_module, "direct_retake", lambda *args: False)
    retry.qc.verdict = QCVerdict.FAIL
    retry.qc.notes = "Measured motion failed (delta p95 0.0002); the plate is effectively frozen."
    second = farm_module.auto_retake_for_qc_failure(project, retry)
    assert second is not None
    assert calls["repair"] == 1


def test_select_hero_print_scores_candidates_and_keeps_ties_deterministic(
    tmp_path, monkeypatch
) -> None:
    from diony_producer import farm as farm_module

    monkeypatch.setenv("DIONY_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path / "vault"))
    project = expand_idea(
        Brief(
            idea="A courier delivers a letter that rewrites the city map.",
            customer_kind="feature",
            target_minutes=0.5,
        )
    )
    shot = project.shots[1]
    assert shot.hero is True
    first = farm_module.new_generation_take(project, shot, attempt=1, live=True)
    second = farm_module.new_generation_take(project, shot, attempt=2, live=True)
    assert first.seed != second.seed
    project.takes = [first, second]
    shot.selected_take_id = first.id

    # A passing sibling beats a failed print.
    first.qc.verdict = QCVerdict.FAIL
    second.qc.verdict = QCVerdict.PASS
    best = farm_module.select_hero_print(project, shot)
    assert best.id == second.id
    assert shot.selected_take_id == second.id

    # With equal verdicts, the stronger measured identity wins.
    first.qc.verdict = QCVerdict.PASS
    first.qc.reference_similarity = 0.91
    second.qc.reference_similarity = 0.84
    best = farm_module.select_hero_print(project, shot)
    assert best.id == first.id

    # A dead tie stays deterministic: the earlier attempt keeps the print.
    second.qc.reference_similarity = 0.91
    best = farm_module.select_hero_print(project, shot)
    assert best.id == first.id


def test_live_roll_queues_two_candidates_for_hero_shots_only(tmp_path, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    import sys

    from diony_producer.app import app

    app_module = sys.modules["diony_producer.app"]

    monkeypatch.setenv("DIONY_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path / "vault"))
    monkeypatch.setattr(app_module, "live_camera", lambda: True)
    monkeypatch.setattr(app_module, "enter_farm", lambda: None)
    queued_batches = []
    monkeypatch.setattr(
        app_module, "start_farm", lambda project, takes: queued_batches.append(list(takes))
    )
    client = TestClient(app)
    client.post("/api/reset")
    opened = client.post(
        "/api/studio/open",
        json={
            "idea": "A lighthouse keeper paints the fog the color of warnings.",
            "customer_kind": "feature",
            "target_minutes": 0.5,
        },
    )
    assert opened.status_code == 200
    shots = opened.json()["shots"]
    hero_ids = {shot["id"] for shot in shots if shot["hero"]}
    assert hero_ids  # the coverage pool flags face-critical recipes

    rolled = client.post("/api/studio/roll")
    assert rolled.status_code == 200
    takes = rolled.json()["takes"]
    per_shot: dict[str, int] = {}
    for take in takes:
        per_shot[take["shot_id"]] = per_shot.get(take["shot_id"], 0) + 1
    for shot in shots:
        assert per_shot[shot["id"]] == (2 if shot["id"] in hero_ids else 1)
    # Candidate seeds differ within each hero pair.
    for shot_id in hero_ids:
        seeds = [take["seed"] for take in takes if take["shot_id"] == shot_id]
        assert len(set(seeds)) == 2

    # The kill switch returns hero shots to single-take.
    monkeypatch.setenv("DIONY_HERO_CANDIDATES", "0")
    client.post("/api/reset")
    client.post(
        "/api/studio/open",
        json={
            "idea": "A lighthouse keeper paints the fog the color of warnings.",
            "customer_kind": "feature",
            "target_minutes": 0.5,
        },
    )
    rolled = client.post("/api/studio/roll")
    takes = rolled.json()["takes"]
    assert len(takes) == len(shots)
