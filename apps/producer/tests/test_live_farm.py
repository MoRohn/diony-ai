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
        "diony_wan.storage.prepare_checkpoint", lambda _task, source, **_kw: checkpoint
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
            voice_ready_ok=False,
            speech_backend="wan2.2-s2v-native-tts",
            speech_presence_score=0.11,
            notes="Voice audio failed: voiced-speech presence 0.11 is below 0.20.",
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
            voice_ready_ok=(False if take.shot_id == first_shot_id and take.attempt == 1 else None),
            speech_backend="wan2.2-s2v-native-tts",
            speech_presence_score=0.12,
            notes=(
                "Voice audio failed: voiced-speech presence 0.12 is below 0.20."
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


def test_farm_holds_the_shot_for_review_after_four_failed_auto_retakes(monkeypatch) -> None:
    """The retake cap settles on the best attempt by policy, not a hold or a stop."""
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

    activity: list[str] = []
    farm_module.reset_farm()
    monkeypatch.setattr("diony_wan.runner.prove_generate", lambda: None)
    monkeypatch.setattr(farm_module, "run_one", fail_continuity)
    monkeypatch.setattr(farm_module, "record_activity", lambda _src, text, **_kw: activity.append(text))
    monkeypatch.setattr("diony_producer.advance.maybe_advance_run", lambda project: None)
    # Every simulated attempt landed a negative; only its measurement failed.
    monkeypatch.setattr(farm_module, "take_has_live_negative", lambda take, project: True)
    farm_module.start_farm(project, [first_take])

    deadline = time.time() + 5
    status = farm_module.farm_status()
    while status["busy"] and time.time() < deadline:
        time.sleep(0.01)
        status = farm_module.farm_status()

    assert calls == [f"{shot.id}-t{attempt}" for attempt in range(1, 5)]
    assert len(project.takes_for(shot.id)) == 4
    # Identical measurements tie; the latest attempt (most direction applied) wins.
    assert project.shot(shot.id).selected_take_id == f"{shot.id}-t4"
    assert status["queued"] == 0
    assert status["error"] == "", "a settled print is not a farm error"
    settled = project.take(f"{shot.id}-t4")
    assert settled.qc.verdict == QCVerdict.WARN
    assert settled.qc.settled_best_of == 4
    assert settled.qc.notes.startswith("Best of 4 attempts selected by policy")
    assert "continuity failed" in settled.qc.notes
    assert any("4 attempts spent; best attempt" in line for line in activity)
    assert not any("held for review" in line for line in activity)
    assert project.production_ended_at is None, "a settled print does not freeze the clock"


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


def test_staging_detail_shows_landed_bytes_rate_and_time_left() -> None:
    from diony_producer.farm import _staging_detail

    gib = 1024**3
    early = _staging_detail("i2v-A14B", "sh-010", 0, 126 * gib, 0.5)
    assert early == "Staging i2v-A14B on the GPU hot tier · sh-010 · 0 / 126 GB"

    later = _staging_detail("i2v-A14B", "sh-010", 60 * gib, 126 * gib, 300.0)
    assert later.startswith("Staging i2v-A14B on the GPU hot tier · sh-010 · 60 / 126 GB · 205 MB/s · about 6 min left")
    assert "?" not in later


def test_staging_listener_drives_the_status_line_and_announces_once(monkeypatch) -> None:
    from diony_producer import farm

    lines: list[str] = []
    monkeypatch.setattr(farm, "record_activity", lambda _src, text, **_kw: lines.append(text))
    monkeypatch.setattr(farm, "_shot_id", "sh-010")
    monkeypatch.setattr(farm, "_detail", "")
    farm._staging_clock.clear()
    gib = 1024**3

    farm._on_staging("s2v-14B", 0, 46 * gib)
    farm._on_staging("s2v-14B", 10 * gib, 46 * gib)

    assert farm._detail.startswith("Staging s2v-14B on the GPU hot tier · sh-010 · 10 / 46 GB")
    assert lines == [
        "sh-010 · staging s2v-14B into the GPU hot tier (46 GB, first use on this box); "
        "Wan starts when the copy verifies"
    ]


def test_measured_qc_hold_is_per_shot_not_a_production_stop() -> None:
    from diony_contracts.enums import QCVerdict
    from diony_contracts.fixture import demo_project
    from diony_contracts.models import QCReport
    from diony_producer.farm import _measured_qc_hold

    project = demo_project()
    take = project.take(project.shots[0].selected_take_id)
    def report(verdict: QCVerdict, notes: str) -> QCReport:
        return QCReport(
            id=f"qc-{take.id}",
            take_id=take.id,
            shot_id=take.shot_id,
            verdict=verdict,
            identity_ok=True,
            morph_ok=True,
            flicker_ok=True,
            duration_ok=True,
            legal_ok=True,
            notes=notes,
        )

    take.qc = report(
        QCVerdict.FAIL,
        "Voice audio failed: speech is still active 12 ms before the window ends (needs 62.5 ms); "
        "the line was cut by the picture.",
    )
    assert _measured_qc_hold(take) is True

    take.qc = report(QCVerdict.FAIL, "Wan negative is 640×360; Diony requires native 1280×720.")
    assert _measured_qc_hold(take) is False, "an integrity fault still stops Production"

    take.qc = report(QCVerdict.PASS, "")
    assert _measured_qc_hold(take) is False


def _farm_run(monkeypatch, run_one, tmp_path):
    from diony_producer import farm as farm_module

    project = demo_project()
    shot = project.shots[0]
    first_take = project.takes_for(shot.id)[0]
    project.takes = [take for take in project.takes if take.shot_id != shot.id]
    project.takes.insert(0, first_take)
    shot.selected_take_id = first_take.id
    activity: list[str] = []
    farm_module.reset_farm()
    monkeypatch.setenv("DIONY_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path / "vault"))
    monkeypatch.setattr("diony_wan.runner.prove_generate", lambda: None)
    monkeypatch.setattr(farm_module, "run_one", run_one)
    monkeypatch.setattr(farm_module, "record_activity", lambda _src, text, **_kw: activity.append(text))
    monkeypatch.setattr("diony_producer.advance.maybe_advance_run", lambda project: None)
    farm_module.start_farm(project, [first_take])
    deadline = time.time() + 5
    status = farm_module.farm_status()
    while status["busy"] and time.time() < deadline:
        time.sleep(0.01)
        status = farm_module.farm_status()
    return project, first_take, status, activity


def test_bounded_voice_timing_verdict_holds_the_shot_without_a_camera_render(tmp_path, monkeypatch) -> None:
    from diony_producer.voice import VoicePipelineError

    def refuse(_project, _take, **_kwargs) -> None:
        raise VoicePipelineError("Voice timing failed: 19 words cannot finish inside sh-010's 3.4 s window")

    project, take, status, activity = _farm_run(monkeypatch, refuse, tmp_path)

    assert status["error"] == ""
    assert status["queued"] == 0
    assert take.qc is not None and take.qc.verdict == QCVerdict.FAIL
    assert take.qc.notes.startswith("Voice timing failed")
    assert take.qc.voice_ready_ok is False
    assert any("held for review; Production continues" in line for line in activity)
    assert project.production_ended_at is not None


def test_missing_voice_backend_still_stops_production(tmp_path, monkeypatch) -> None:
    from diony_producer.voice import VoicePipelineError

    def missing(_project, _take, **_kwargs) -> None:
        raise VoicePipelineError("Voice is required, but the Wan2.2 speech pack is not ready on this host.")

    _project, take, status, activity = _farm_run(monkeypatch, missing, tmp_path)

    assert status["error"].startswith("Voice is required")
    assert not (take.qc and take.qc.notes.startswith("Voice is required"))
    assert not any("held for review" in line for line in activity)


def test_stale_unmatched_receipt_is_rejudged_by_the_contract(tmp_path) -> None:
    """A runner that pinned 81 frames wrote 'unmatched' for a 49-frame booking."""
    import hashlib
    import json

    from diony_contracts.fixture import demo_project
    from diony_producer.farm import _INFRASTRUCTURE_QC_PREFIXES, _load_generation_receipt

    project = demo_project()
    take = project.take(project.shots[0].selected_take_id)
    negative = tmp_path / "negative_720.mp4"
    negative.write_bytes(b"negative bytes" * 16)
    receipt = {
        "status": "succeeded", "backend": "native", "selected_lane": "wan22_quality_cli",
        "task": "i2v-A14B", "native_width": 1280, "native_height": 720, "frame_num": 49,
        "sample_steps": 24, "inference_frames_per_pass": 49, "denoise_passes": 1,
        "effective_denoise_steps": 24, "sample_solver": "unipc", "precision": "bfloat16",
        "negative_conditioning": True, "finish_required": True,
        "quality_contract_sha256": "a" * 64, "quality_contract_matched": False,
        "attention_backend": "auto", "output_sha256": hashlib.sha256(negative.read_bytes()).hexdigest(),
        "output_bytes": negative.stat().st_size,
    }
    negative.with_suffix(".generation.json").write_text(json.dumps(receipt))

    loaded = _load_generation_receipt(take, negative)

    assert loaded.quality_contract_matched is True
    assert take.generation_receipt is not None and take.generation_receipt.frame_num == 49
    assert any("Wan execution receipt".startswith(p) or p.startswith("Wan execution receipt") for p in _INFRASTRUCTURE_QC_PREFIXES)


def test_native_rate_s2v_negative_covers_the_booking_without_retiming() -> None:
    """57 frames at 16 fps is 3.56 s: it covers an 81-frame 23.976 booking."""
    import math

    from diony_contracts.live import s2v_frame_num

    booked, fps = 81, 23.976
    native = s2v_frame_num(booked, fps)
    delivered_booking_frames = math.floor(native / 16 * fps)
    assert delivered_booking_frames >= booked
    # A short 24 fps lane delivery still trims the booking to camera truth.
    assert math.floor(77 / 24.0 * fps) < booked


def test_retake_cap_settles_on_the_highest_scoring_attempt() -> None:
    """The four measured sh-020 attempts: the near-sync take wins, not the last one."""
    from diony_producer.farm import attempt_score, settle_exhausted_shot, settled_print

    project = demo_project()
    shot = project.shots[0]
    base = project.takes_for(shot.id)[0]
    project.takes = [take for take in project.takes if take.shot_id != shot.id]
    # (voiced-speech presence, speech tail ms, reference SSIM, temporal residual)
    measured = [
        (0.22, 30.0, 0.5734, 0.0477),
        (0.46, 240.0, 0.5736, 0.0457),
        (0.30, 10.0, 0.5738, 0.0656),
        (0.25, 60.0, 0.5757, 0.0550),
    ]
    for attempt, (presence, tail, reference, residual) in enumerate(measured, start=1):
        take = base.model_copy(update={"id": f"{shot.id}-t{attempt}", "attempt": attempt})
        take.qc = QCReport(
            id=f"qc-{take.id}",
            take_id=take.id,
            shot_id=shot.id,
            verdict=QCVerdict.FAIL,
            identity_ok=False,
            morph_ok=True,
            flicker_ok=True,
            duration_ok=True,
            legal_ok=True,
            voice_ready_ok=False,
            speech_backend="wan2.2-s2v-native-tts",
            speech_presence_score=presence,
            speech_tail_ms=tail,
            reference_similarity=reference,
            temporal_residual_p95=residual,
            notes=f"Voice audio failed: speech is still active {tail:.0f} ms before the window ends (needs 62.5 ms).",
        )
        project.takes.append(take)
    shot.selected_take_id = f"{shot.id}-t4"
    scores = [attempt_score(project.take(f"{shot.id}-t{n}")) for n in range(1, 5)]
    assert max(scores) == scores[1]

    from diony_producer import farm as farm_module

    activity: list[str] = []
    farm_module.record_activity, kept = (lambda _src, text, **_kw: activity.append(text)), farm_module.record_activity
    farm_module.take_has_live_negative, kept_live = (lambda take, project: True), farm_module.take_has_live_negative
    farm_module.persist_project, kept_persist = (lambda project: None), farm_module.persist_project
    try:
        best = settle_exhausted_shot(project, shot)
    finally:
        farm_module.record_activity = kept
        farm_module.take_has_live_negative = kept_live
        farm_module.persist_project = kept_persist
    assert best is not None and best.id == f"{shot.id}-t2"
    assert shot.selected_take_id == best.id
    assert best.qc.verdict == QCVerdict.WARN and best.qc.settled_best_of == 4
    assert best.qc.settled_score == scores[1]
    assert settled_print(project, shot.id) is best
    # Settling is idempotent and never re-ranks a settled Shot.
    assert settle_exhausted_shot(project, shot) is best
    # Hero selection honours the settled print instead of re-ranking rejections over it.
    from diony_producer.farm import _candidate_score, select_hero_print

    shot.hero = True
    farm_module.persist_project, kept_persist = (lambda project: None), farm_module.persist_project
    try:
        assert select_hero_print(project, shot) is best
    finally:
        farm_module.persist_project = kept_persist
    assert shot.selected_take_id == best.id
    assert _candidate_score(best) > _candidate_score(project.take(f"{shot.id}-t4"))
    assert any("best attempt sh-010-t2 proceeds by policy" in line or "proceeds by policy" in line for line in activity)


def test_reference_lock_measures_against_the_conditioning_image(tmp_path, monkeypatch) -> None:
    """A close-up or boundary-conditioned print is compared with that still, not the wide one."""
    from diony_producer import camera
    from diony_producer import farm as farm_module

    project = demo_project()
    shot = project.shots[0]
    take = project.takes_for(shot.id)[0]
    shot.keyframe_asset_ids = shot.keyframe_asset_ids or ["ast-still"]
    wide = tmp_path / "wide.png"
    wide.write_bytes(b"wide")
    closeup = tmp_path / "closeup.png"
    closeup.write_bytes(b"closeup")
    negative = tmp_path / "negative_720.mp4"
    negative.write_bytes(b"x" * 64)
    compared: list[Path] = []
    monkeypatch.setattr(farm_module, "reference_frame", lambda project, shot: wide)
    monkeypatch.setattr(
        farm_module, "_ssim_at", lambda negative, reference, *, seconds=0.0: compared.append(reference) or 0.9
    )
    media = {"fps": 16.0, "frames": 65}
    assert farm_module._reference_similarities(project, take, negative, media)
    assert compared and all(path == wide for path in compared)

    compared.clear()
    camera.record_conditioning_image(negative, closeup, source="closeup")
    assert farm_module._reference_similarities(project, take, negative, media)
    assert compared and all(path == closeup for path in compared)

    # A tampered or missing conditioning still falls back to the wide reference.
    closeup.write_bytes(b"changed")
    compared.clear()
    farm_module._reference_similarities(project, take, negative, media)
    assert compared and all(path == wide for path in compared)


def test_reclaim_re_attaches_only_unjudged_negatives(tmp_path, monkeypatch) -> None:
    """A restart never re-measures rejected takes or overwrites a settled print."""
    from diony_producer import farm as farm_module

    monkeypatch.setenv("DIONY_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path / "vault"))
    project = demo_project()
    shot = project.shots[0]
    base = project.takes_for(shot.id)[0]
    project.takes = [take for take in project.takes if take.shot_id != shot.id]
    verdicts = {
        "judged-fail": {"verdict": QCVerdict.FAIL},
        "judged-pass": {"verdict": QCVerdict.PASS},
        "settled": {"verdict": QCVerdict.WARN, "settled_best_of": 4, "settled_score": 0.7},
        "queued": {"verdict": QCVerdict.WARN, "notes": "Queued for Wan2.2 on this pair."},
    }
    for label, update in verdicts.items():
        take = base.model_copy(update={"id": f"{shot.id}-{label}"})
        take.qc = base.qc.model_copy(update={**update, "take_id": take.id})
        project.takes.append(take)
        output = tmp_path / "vault" / "projects" / project.id / "takes" / take.id / "negative_720.mp4"
        output.parent.mkdir(parents=True)
        output.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"landed" * 32)
    monkeypatch.setattr(farm_module, "list_generate_jobs", lambda: [])
    monkeypatch.setattr(farm_module, "take_has_live_negative", lambda take, project: True)
    monkeypatch.setattr(farm_module, "persist_project", lambda project: None)
    attached: list[str] = []
    monkeypatch.setattr(
        farm_module, "attach_negative", lambda project, take, rel, frames, **kw: attached.append(take.id)
    )
    assert farm_module.reclaim_landed_negatives(project) == 1
    assert attached == [f"{shot.id}-queued"]


def test_re_measuring_a_settled_print_keeps_the_policy_decision(tmp_path, monkeypatch) -> None:
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
    take = Take(id="sh-010-t4", shot_id=shot.id, attempt=4, engine=shot.engine, seed=shot.seed)
    take.qc = QCReport(
        id=f"qc-{take.id}",
        take_id=take.id,
        shot_id=shot.id,
        verdict=QCVerdict.WARN,
        identity_ok=False,
        morph_ok=True,
        flicker_ok=True,
        duration_ok=True,
        legal_ok=True,
        settled_best_of=4,
        settled_score=0.7,
        notes="Best of 4 attempts selected by policy (score 0.70) · Reference lock failed",
    )
    project.takes = [take]
    relative = f"projects/{project.id}/takes/{take.id}/negative_720.mp4"
    negative = tmp_path / "vault" / relative
    negative.parent.mkdir(parents=True)
    negative.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"drift" * 32)
    monkeypatch.setattr(
        farm_module,
        "probe_media",
        lambda *args, **kwargs: {"width": 1280, "height": 720, "fps": 23.976, "frames": 120},
    )
    monkeypatch.setattr(
        farm_module,
        "_reference_similarities",
        lambda *args, **kwargs: {"first": 0.99, "quarter": 0.84, "middle": 0.79, "three_quarter": 0.76, "last": 0.71},
    )

    farm_module.attach_negative(project, take, relative, shot.duration_frames, live=True)

    assert take.qc is not None
    assert take.qc.verdict == QCVerdict.WARN
    assert take.qc.settled_best_of == 4 and take.qc.settled_score == 0.7
    assert take.qc.notes.startswith("Best of 4 attempts selected by policy (score 0.70)")
    assert "required 0.75" in take.qc.notes


def test_a_rejudged_canary_is_promoted_on_resume_and_never_stops_the_run(tmp_path, monkeypatch) -> None:
    from diony_producer import farm as farm_module

    monkeypatch.setenv("DIONY_VAULT", str(tmp_path))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path))
    project = demo_project()
    take = project.takes[0]
    take.qc = QCReport(
        id="qc-c", take_id=take.id, shot_id=take.shot_id, verdict=QCVerdict.PASS,
        identity_ok=True, morph_ok=True, flicker_ok=True, duration_ok=True, legal_ok=True,
        notes="Wan2.2 720p negative. Re-judged mouth-sync evidence passed.",
    )
    receipt_dir = tmp_path / "qualification"
    receipt_dir.mkdir()
    path = receipt_dir / farm_module._ATTENTION_RECEIPT_NAME
    path.write_text(json.dumps({"scope_project_id": project.id, "quality_canary_take_id": take.id, "stage": "operator_passed_quality_canary_required"}))
    monkeypatch.setattr("diony_wan.attention_qualification.attention_decision", lambda _p, project_id: type("D", (), {"enabled": False})())
    promoted: list[str] = []
    monkeypatch.setattr(farm_module, "_complete_attention_canary", lambda _p, t, _path: promoted.append(t.id))
    monkeypatch.setattr(farm_module, "record_activity", lambda *_a, **_k: None)
    assert farm_module._promote_rejudged_canary(project, take) is True and promoted == [take.id]

    # Bound to another take, or still failing: nothing to promote.
    other = project.takes[1]
    assert farm_module._promote_rejudged_canary(project, other) is False
    take.qc = take.qc.model_copy(update={"verdict": QCVerdict.FAIL})
    assert farm_module._promote_rejudged_canary(project, take) is False

    # A promotion failure is logged and leaves the run alone.
    take.qc = take.qc.model_copy(update={"verdict": QCVerdict.PASS})

    def explode(_p, _t, _path):
        raise RuntimeError("no 1080 finish on this desk")

    monkeypatch.setattr(farm_module, "_complete_attention_canary", explode)
    assert farm_module._promote_rejudged_canary(project, take) is False


def test_a_resume_retake_inherits_the_canary_binding(tmp_path, monkeypatch) -> None:
    from diony_producer import farm as farm_module

    monkeypatch.setenv("DIONY_VAULT", str(tmp_path))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path))
    project = demo_project()
    failed, retry = project.takes[0], project.takes[1]
    (tmp_path / "qualification").mkdir()
    path = tmp_path / "qualification" / farm_module._ATTENTION_RECEIPT_NAME
    path.write_text(json.dumps({"scope_project_id": project.id, "quality_canary_take_id": failed.id}))
    rebound: list[tuple[str, str]] = []
    monkeypatch.setattr(farm_module, "_rebind_attention_canary", lambda _p, f, r, _path: rebound.append((f.id, r.id)))
    assert farm_module._rebind_canary_after_resume(project, failed, retry) is True
    assert rebound == [(failed.id, retry.id)]
    assert farm_module._rebind_canary_after_resume(project, retry, failed) is False, "not bound to that take"


def test_a_voice_hold_judged_under_older_gates_rerolls_once(tmp_path, monkeypatch) -> None:
    """sh-060, 2026-08-27: the harshness gate was fixed, the hold stayed."""
    from diony_producer import farm as farm_module

    project = demo_project()
    take = project.takes[0]
    old = QCReport(
        id="qc-o", take_id=take.id, shot_id=take.shot_id, verdict=QCVerdict.FAIL,
        identity_ok=False, morph_ok=True, flicker_ok=True, duration_ok=True, legal_ok=True,
        voice_ready_ok=False, notes="Voice audio failed locked identity or studio-audio QC",
    )
    take.qc = old
    assert farm_module._stale_voice_gate_hold(take) is True
    take.qc = farm_module._pre_camera_hold_report(take, "Voice audio failed locked identity or studio-audio QC")
    assert farm_module.VOICE_GATE_VERSION in take.qc.notes
    assert farm_module._stale_voice_gate_hold(take) is False, "judged under today's gates: a real hold"
    assert farm_module._voice_hold_message(take.qc.notes)
    take.qc = old.model_copy(update={"notes": "Voice audio failed: voiced-speech presence 0.10 is below 0.20.", "speech_backend": "wan2.2-s2v-native-tts", "speech_presence_score": 0.1})
    assert farm_module._stale_voice_gate_hold(take) is False, "a measured negative is not a pre-camera hold"


def test_passed_clips_finish_at_1080_while_a_shot_waits_for_a_decision(tmp_path, monkeypatch) -> None:
    from diony_producer import advance as advance_module
    from diony_producer import farm as farm_module

    project = demo_project()
    shots = project.shots[:3]
    project.shots = shots
    takes = {shot.id: project.takes_for(shot.id)[0] for shot in shots}
    for i, shot in enumerate(shots):
        take = takes[shot.id]
        shot.selected_take_id = take.id
        take.qc = QCReport(
            id=f"qc-{i}", take_id=take.id, shot_id=shot.id,
            verdict=QCVerdict.PASS if i < 2 else QCVerdict.FAIL,
            identity_ok=i < 2, morph_ok=True, flicker_ok=True, duration_ok=True, legal_ok=True,
            voice_ready_ok=None if i < 2 else False,
            notes="ok" if i < 2 else f"Voice audio failed locked identity or studio-audio QC {farm_module._VOICE_GATE_TAG}",
        )
    monkeypatch.setattr(advance_module, "live_finish", lambda: True)
    monkeypatch.setattr(advance_module, "should_proxy", lambda: False)
    monkeypatch.setattr(farm_module, "farm_status", lambda: {"busy": False})
    monkeypatch.setattr(farm_module, "_selected_missing_takes", lambda _p, **_k: [takes[shots[2].id]])
    monkeypatch.setattr(advance_module, "qc_contract_valid", lambda _p, _t: True)
    monkeypatch.setattr(advance_module, "take_has_live_negative", lambda _t, _p: True)
    import diony_producer.finish as finish_module

    monkeypatch.setattr(finish_module, "_take_1080", lambda _p, _t: None)
    monkeypatch.setattr(finish_module, "finish_status", lambda: {"busy": False, "error": ""})
    monkeypatch.setattr(finish_module, "enter_finish", lambda: None)
    started: list[list[str]] = []
    monkeypatch.setattr(finish_module, "start_finish", lambda _p, ts: started.append([t.id for t in ts]))
    lines: list[str] = []
    monkeypatch.setattr(advance_module, "record_activity", lambda _s, text, **_k: lines.append(text))

    assert advance_module.finish_ready_takes_early(project, live=True) == [takes[shots[0].id].id, takes[shots[1].id].id]
    assert started and len(started[0]) == 2
    assert any("2 passed clips at 1080 while 1 Shot waits" in line for line in lines)
    # A stale-gate hold is farm work: nothing finishes early until it has re-rolled.
    takes[shots[2].id].qc = takes[shots[2].id].qc.model_copy(update={"notes": "Voice audio failed locked identity or studio-audio QC"})
    assert advance_module.finish_ready_takes_early(project, live=True) == []


def test_pre_camera_voice_holds_never_spend_the_render_cap() -> None:
    from diony_producer import farm as farm_module

    project = demo_project()
    take = project.takes[0]
    take.qc = farm_module._pre_camera_hold_report(take, "Voice audio failed harshness 0.356 exceeds 0.250")
    assert farm_module._pre_camera_voice_hold(take) is True
    assert farm_module._auto_retake_quality_failure(take) is False, "no negative was rendered: no camera attempt"
    assert farm_module._measured_qc_hold(take) is True, "still a per-Shot hold, the rundown keeps moving"
    measured = take.qc.model_copy(update={
        "notes": "Voice audio failed: voiced-speech presence 0.10 is below 0.20.",
        "speech_backend": "wan2.2-s2v-native-tts", "speech_presence_score": 0.1, "voice_ready_ok": False,
    })
    take.qc = measured
    assert farm_module._pre_camera_voice_hold(take) is False
    assert farm_module._auto_retake_quality_failure(take) is True, "a measured render still retakes"


def test_reference_lock_is_judged_on_the_layout_proxy_with_full_frame_kept_as_evidence() -> None:
    """B200 2026-08-28 calibration: a faithful static wide shot with subject motion."""
    from diony_producer import farm as farm_module

    raw = {"first": 0.7795, "quarter": 0.6647, "middle": 0.6702, "three_quarter": 0.6482, "last": 0.6366}
    layout = {"first": 0.9120, "quarter": 0.7238, "middle": 0.7189, "three_quarter": 0.6337, "last": 0.6040}
    ok, detail = farm_module._reference_lock_verdict(raw, layout, first_floor=0.75, drift_floor=0.55, legacy_floor=0.75)
    assert ok is True
    assert "layout SSIM first 0.9120" in detail and "later minimum 0.6040 at last" in detail
    assert "full-frame minimum 0.6366" in detail

    # An unrelated scene in the same medium: recognisable nowhere.
    unrelated = {"first": 0.4906, "quarter": 0.4887, "middle": 0.4915, "three_quarter": 0.4947, "last": 0.4980}
    ok, detail = farm_module._reference_lock_verdict({"first": 0.5664}, unrelated, first_floor=0.75, drift_floor=0.55, legacy_floor=0.75)
    assert ok is False
    assert "required first ≥ 0.75" in detail

    # A faithful start with walking subjects (sh-010-t3, static camera): later checkpoints are advisory.
    walking = {"first": 0.8201, "quarter": 0.5976, "middle": 0.5729, "three_quarter": 0.5607, "last": 0.5464}
    ok, detail = farm_module._reference_lock_verdict({}, walking, first_floor=0.75, drift_floor=0.55, legacy_floor=0.75)
    assert ok is True
    assert "later minimum 0.5464 at last" in detail and "advisory" in detail

    # A steadicam push-in from the wide still (sh-040-t1): faithful frames, layout later 0.54.
    push_in = {"first": 0.9195, "quarter": 0.5905, "middle": 0.5473, "three_quarter": 0.5402, "last": 0.5451}
    ok, detail = farm_module._reference_lock_verdict(
        {"first": 0.831, "last": 0.6279}, push_in, first_floor=0.75, drift_floor=0.55, legacy_floor=0.75, camera_move="steadicam"
    )
    assert ok is True
    assert "advisory (camera steadicam)" in detail
    # A moving camera still needs the still recognisable in the first frame.
    ok, _ = farm_module._reference_lock_verdict(
        {}, {"first": 0.52, "last": 0.50}, first_floor=0.75, drift_floor=0.55, legacy_floor=0.75, camera_move="dolly_in"
    )
    assert ok is False

    # Without the layout measure the legacy full-frame floor still judges.
    ok, detail = farm_module._reference_lock_verdict(raw, {}, first_floor=0.75, drift_floor=0.55, legacy_floor=0.75)
    assert ok is False and "minimum sampled SSIM 0.6366; required 0.75" in detail
    ok, _ = farm_module._reference_lock_verdict({"first": 0.99}, {}, first_floor=0.75, drift_floor=0.55, legacy_floor=0.75)
    assert ok is True
    assert farm_module._reference_lock_verdict({}, {}, first_floor=0.75, drift_floor=0.55, legacy_floor=0.75) == (None, "missing")


def test_reference_lock_floors_can_only_tighten(monkeypatch) -> None:
    from diony_producer.farm import REFERENCE_LOCK_DRIFT_FLOOR, _quality_ssim_floor

    monkeypatch.setenv("DIONY_REFERENCE_DRIFT_SSIM_MIN", "0.10")
    assert _quality_ssim_floor("DIONY_REFERENCE_DRIFT_SSIM_MIN", REFERENCE_LOCK_DRIFT_FLOOR) == 0.55
    monkeypatch.setenv("DIONY_REFERENCE_DRIFT_SSIM_MIN", "0.70")
    assert _quality_ssim_floor("DIONY_REFERENCE_DRIFT_SSIM_MIN", REFERENCE_LOCK_DRIFT_FLOOR) == 0.70


def test_layout_ssim_softens_and_downscales_both_frames(monkeypatch, tmp_path) -> None:
    from diony_producer import farm as farm_module

    seen: list[list[str]] = []

    def fake_run(command, **kwargs):
        seen.append(list(command))
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": "SSIM Y:0.9 U:0.9 V:0.9 All:0.912 (10.5)"})()

    monkeypatch.setattr(farm_module, "resolve_media_tool", lambda name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(farm_module.subprocess, "run", fake_run)
    assert farm_module._layout_ssim_at(tmp_path / "n.mp4", tmp_path / "r.png", seconds=1.25) == 0.912
    graph = seen[-1][seen[-1].index("-filter_complex") + 1]
    assert graph.count("scale=320:180,gblur=sigma=1.5") == 2
    assert farm_module._ssim_at(tmp_path / "n.mp4", tmp_path / "r.png", seconds=0.0) == 0.912
    graph = seen[-1][seen[-1].index("-filter_complex") + 1]
    assert graph.count("scale=1280:720") == 2 and "gblur" not in graph


def test_identical_speech_on_a_second_attempt_settles_instead_of_retaking() -> None:
    """sh-030 2026-08-28: four renders, byte-identical dialogue, presence 0.108 every time."""
    from diony_producer import farm as farm_module

    project = demo_project()
    shot = project.shots[0]
    base = project.takes_for(shot.id)[0]
    project.takes = [take for take in project.takes if take.shot_id != shot.id]
    same_audio = "e5981236cded" + "0" * 52
    for attempt in (1, 2):
        take = base.model_copy(update={"id": f"{shot.id}-t{attempt}", "attempt": attempt})
        take.qc = QCReport(
            id=f"qc-{take.id}",
            take_id=take.id,
            shot_id=shot.id,
            verdict=QCVerdict.FAIL,
            identity_ok=False,
            morph_ok=True,
            flicker_ok=True,
            duration_ok=True,
            legal_ok=True,
            production_clean_ok=True,
            voice_ready_ok=False,
            speech_backend="wan2.2-s2v-native-tts",
            speech_presence_score=0.108,
            speech_presence_min=0.20,
            speech_dialogue_sha256=same_audio,
            speech_request_sha256="req-1",
            speech_tail_ms=2873.0,
            reference_similarity=0.88 + attempt * 0.01,
            temporal_residual_p95=0.03,
            notes="Voice audio failed: voiced-speech presence 0.11 is below 0.20.",
        )
        project.takes.append(take)
    first, second = project.take(f"{shot.id}-t1"), project.take(f"{shot.id}-t2")
    shot.selected_take_id = second.id
    assert farm_module.futile_voice_retake(project, first) is None
    assert farm_module.futile_voice_retake(project, second) is first

    # A different speech request (directed retake rewrote the line) is not futile.
    second.qc.speech_request_sha256 = "req-2"
    assert farm_module.futile_voice_retake(project, second) is None
    second.qc.speech_request_sha256 = "req-1"
    # A picture failure alongside the voice failure is still worth a retake.
    second.qc.production_clean_ok = False
    assert farm_module.futile_voice_retake(project, second) is None
    second.qc.production_clean_ok = True

    activity: list[str] = []
    farm_module.record_activity, kept = (lambda _src, text, **_kw: activity.append(text)), farm_module.record_activity
    farm_module.take_has_live_negative, kept_live = (lambda take, project: True), farm_module.take_has_live_negative
    farm_module.persist_project, kept_persist = (lambda project: None), farm_module.persist_project
    try:
        retry = farm_module.auto_retake_for_qc_failure(project, second)
    finally:
        farm_module.record_activity = kept
        farm_module.take_has_live_negative = kept_live
        farm_module.persist_project = kept_persist
    assert retry is None
    assert len([t for t in project.takes if t.shot_id == shot.id]) == 2
    settled = farm_module.settled_print(project, shot.id)
    assert settled is second and settled.qc.verdict == QCVerdict.WARN
    assert settled.qc.settled_best_of == 2
    assert "identical speech on sh-010-t1" in settled.qc.notes or f"identical speech on {first.id}" in settled.qc.notes
    assert any("a retake cannot change it" in line for line in activity)


def test_stale_speech_receipt_is_re_measured_under_the_current_floor(tmp_path, monkeypatch) -> None:
    """sh-030's settled print: v1 floor 0.20 rejected presence 0.108; v2 measures the line's own floor."""
    from diony_contracts.enums import ArtifactKind
    from diony_contracts.voice import SpeechQCReceipt
    from diony_producer import farm as farm_module
    from diony_producer import voice as voice_module

    project = demo_project()
    shot = next(s for s in project.shots if s.dialogue_turns or s.audio_line)
    take = project.takes_for(shot.id)[0]
    negative = tmp_path / "negative_720.mp4"
    negative.write_bytes(b"x" * 64)
    monkeypatch.setattr(farm_module, "vault_root", lambda: tmp_path)
    take.artifacts = [a for a in take.artifacts if a.kind != ArtifactKind.NEGATIVE_720]
    from diony_contracts.models import Artifact

    take.artifacts.append(
        Artifact(id="art-neg", take_id=take.id, kind=ArtifactKind.NEGATIVE_720, path="negative_720.mp4", sha256="0" * 64, bytes=64)
    )
    old_receipt = SpeechQCReceipt(
        measurement_version="diony-speech-qc-v1", audio_present=True, duration_ms=3541, window_ms=3541,
        speech_presence_score=0.108, speech_presence_min=0.20, speech_tail_ms=2873.0,
        integrated_lufs=-24.03, true_peak_dbtp=-11.05, passed=False,
    )
    from diony_contracts.enums import VoiceRenderState
    from diony_contracts.voice import SpeechClipPackage, WanSpeechRequest

    request = WanSpeechRequest(
        character_id="char-robot", casting_id="cast-char-robot-v1", tts_text="QUACK!",
        tts_prompt_audio_sha256="a" * 64, prompt_source="library:cast-00.wav", window_ms=3541,
    )
    take.voice_package = SpeechClipPackage(
        id=f"speech-{take.id}", project_id=project.id, shot_id=take.shot_id, take_id=take.id,
        character_id="char-robot", casting_id="cast-char-robot-v1", request=request,
        video_sha256="b" * 64, state=VoiceRenderState.FAILED, qc=old_receipt,
    )
    take.qc = QCReport(
        id=f"qc-{take.id}", take_id=take.id, shot_id=shot.id, verdict=QCVerdict.WARN, identity_ok=True,
        morph_ok=True, flicker_ok=True, duration_ok=True, legal_ok=True, production_clean_ok=True,
        voice_ready_ok=False, speech_backend="wan2.2-s2v-native-tts", speech_presence_score=0.108,
        settled_best_of=4, settled_score=0.60,
        notes="Best of 4 attempts selected by policy (score 0.60) · Voice audio failed: voiced-speech presence 0.11 is below 0.20.",
    )
    new_receipt = old_receipt.model_copy(update={
        "measurement_version": voice_module.SPEECH_QC_VERSION, "speech_presence_min": 0.0847,
        "line_word_count": 1, "expected_speech_ms": 600.0, "dialogue_sha256": "e" * 64, "request_sha256": "req",
        "passed": True,
    })
    seen: list[str] = []

    def fake_verify(project_, take_, video):
        seen.append(str(video))
        updated = take_.voice_package.model_copy(update={"qc": new_receipt, "state": VoiceRenderState.VERIFIED})
        take_.voice_package = updated
        return updated

    monkeypatch.setattr(voice_module, "verify_voice_animation", fake_verify)
    monkeypatch.setattr(farm_module, "persist_project", lambda project: None)
    activity: list[str] = []
    monkeypatch.setattr(farm_module, "record_activity", lambda _src, text, **_kw: activity.append(text))

    assert farm_module.refresh_voice_measurement(project, take) is True
    assert seen == [str(negative)]
    assert take.qc.verdict == QCVerdict.WARN  # the policy decision stands
    assert take.qc.voice_ready_ok is True
    assert take.qc.speech_presence_min == 0.0847
    assert take.qc.speech_dialogue_sha256 == "e" * 64
    assert take.qc.notes.startswith("Best of 4 attempts selected by policy (score 0.60) · Speech re-measured at diony-speech-qc-v2: passes")
    assert "previously: Voice audio failed" in take.qc.notes
    assert any("against floor 0.08 for a 1-word line; passes" in line for line in activity)
    # Current version: nothing to do.
    assert farm_module.refresh_voice_measurement(project, take) is False
    assert seen == [str(negative)]


def test_pending_queue_follows_the_rundown_and_a_parked_canary_is_recognised() -> None:
    from diony_producer import farm as farm_module

    project = demo_project()
    shots = project.shots
    late_retake = farm_module.new_generation_take(project, shots[0], attempt=3, live=True)
    queue = [project.takes_for(shots[2].id)[0], project.takes_for(shots[1].id)[0], late_retake]
    queue.sort(key=farm_module._rundown_position(project))
    assert [t.shot_id for t in queue] == [shots[0].id, shots[1].id, shots[2].id]

    project.takes.append(late_retake)
    parked = farm_module._parked_canary_take(project, late_retake.id)
    assert parked is late_retake
    assert farm_module._parked_canary_take(project, "sh-999-t9") is None
    from diony_contracts.enums import ArtifactKind
    from diony_contracts.models import Artifact

    late_retake.artifacts.append(
        Artifact(id="art-x", take_id=late_retake.id, kind=ArtifactKind.NEGATIVE_720, path="x.mp4", sha256="0" * 64, bytes=64)
    )
    assert farm_module._parked_canary_take(project, late_retake.id) is None


def test_stale_previous_frame_chain_re_rolls_or_waives_instead_of_deadlocking(monkeypatch) -> None:
    """sh-020-t1 chained to sh-010-t1; sh-010 printed on t4 two hours later (2026-08-28)."""
    from diony_contracts.enums import ContinuityMode
    from diony_producer import advance as adv

    project = demo_project()
    first, second = project.shots[0], project.shots[1]
    second.continuity_mode = ContinuityMode.PREVIOUS_FRAME
    second.character_ref_ids = list(first.character_ref_ids) or ["ast-still"]
    first.character_ref_ids = list(second.character_ref_ids)
    prior = project.takes_for(first.id)[0]
    prior.qc = QCReport(id="qc-a", take_id=prior.id, shot_id=first.id, verdict=QCVerdict.PASS, identity_ok=True,
                        morph_ok=True, flicker_ok=True, duration_ok=True, legal_ok=True)
    first.selected_take_id = prior.id
    following = project.takes_for(second.id)[0]
    following.qc = QCReport(id="qc-b", take_id=following.id, shot_id=second.id, verdict=QCVerdict.PASS, identity_ok=True,
                            morph_ok=True, flicker_ok=True, duration_ok=True, legal_ok=True,
                            continuity_from_take_id="stale-print", continuity_verified=True, continuity_similarity=0.9)
    second.selected_take_id = following.id
    assert [(s.id, t.id, e) for s, t, e in adv.stale_continuity(project)] == [(second.id, following.id, prior.id)]

    activity: list[str] = []
    started: list[list[str]] = []
    monkeypatch.setattr(adv, "record_activity", lambda _src, text, **_kw: activity.append(text))
    monkeypatch.setattr(adv, "persist_project", lambda project: None)
    monkeypatch.setattr(adv, "record_decision", lambda *a, **k: None)
    monkeypatch.setattr(adv, "enter_farm", lambda: None)
    monkeypatch.setattr(adv, "start_farm", lambda project, takes: started.append([t.id for t in takes]))

    # Budget remains: a chained retake is queued and becomes the selected take.
    assert adv.refresh_stale_continuity(project, live=True) is True
    assert started and started[0] == [second.selected_take_id]
    assert second.selected_take_id != following.id
    assert any("continuity refresh" in line for line in activity)
    assert adv.stale_continuity(project) == []  # the new take has no chain evidence yet

    # Budget spent: the print is kept with a recorded waiver and the contract accepts it.
    second.selected_take_id = following.id
    from diony_contracts.cluster import RETAKE_CAP
    from diony_contracts.enums import TakeOrigin

    while len([t for t in project.takes_for(second.id) if t.origin == TakeOrigin.GENERATION]) < RETAKE_CAP:
        project.takes.append(following.model_copy(update={"id": f"{second.id}-x{len(project.takes)}", "attempt": 9}))
    started.clear()
    assert adv.refresh_stale_continuity(project, live=True) is False
    assert started == []
    assert "replaced by" in following.qc.continuity_waived and "budget spent" in following.qc.continuity_waived
    assert any("continuity waived" in line for line in activity)
    from diony_producer.farm import qc_contract_valid

    monkeypatch.setattr("diony_producer.farm.measured_qc_required_default", lambda: False)
    assert qc_contract_valid(project, following) is True


def test_a_refused_lock_is_reported_once_on_the_feed(monkeypatch) -> None:
    from diony_producer import advance as adv

    activity: list[str] = []
    monkeypatch.setattr(adv, "record_activity", lambda _src, text, **_kw: activity.append(text))
    monkeypatch.setattr(adv, "_last_lock_refusal", "")
    adv._report_lock_refusal("Picture", ValueError("picture lock refuses selected Takes without current reference/continuity QC: sh-020/sh-020-t1"))
    adv._report_lock_refusal("Picture", ValueError("picture lock refuses selected Takes without current reference/continuity QC: sh-020/sh-020-t1"))
    assert activity == ["Picture lock is waiting: picture lock refuses selected Takes without current reference/continuity QC: sh-020/sh-020-t1"]


def test_hero_selection_prefers_the_print_whose_chain_is_current(monkeypatch) -> None:
    """sh-020 2026-08-28: t3/t4 chained to the replacement print lost to stale t1 on raw SSIM."""
    from diony_contracts.enums import ContinuityMode
    from diony_producer import farm as farm_module

    project = demo_project()
    first, second = project.shots[0], project.shots[1]
    second.continuity_mode = ContinuityMode.PREVIOUS_FRAME
    first.selected_take_id = "sh-010-t4"
    monkeypatch.setattr(farm_module, "measured_qc_required_default", lambda: False)

    def qc(take_id, *, from_take, raw, layout):
        return QCReport(
            id=f"qc-{take_id}", take_id=take_id, shot_id=second.id, verdict=QCVerdict.PASS, identity_ok=True,
            morph_ok=True, flicker_ok=True, duration_ok=True, legal_ok=True,
            reference_asset_ids=list(second.keyframe_asset_ids), reference_verified=True,
            reference_similarity=raw, reference_similarities={k: raw for k in ("first", "quarter", "middle", "three_quarter", "last")},
            reference_layout_similarity=layout, continuity_from_take_id=from_take, continuity_verified=True, continuity_similarity=0.9,
            temporal_residual_p95=0.03, flicker_luma_p95=0.001,
        )

    base = project.takes_for(second.id)[0]
    project.takes = [t for t in project.takes if t.shot_id != second.id]
    stale = base.model_copy(update={"id": f"{second.id}-t1", "attempt": 1}); stale.qc = qc(stale.id, from_take="sh-010-t1", raw=0.6992, layout=0.83)
    fresh = base.model_copy(update={"id": f"{second.id}-t3", "attempt": 3}); fresh.qc = qc(fresh.id, from_take="sh-010-t4", raw=0.6100, layout=0.79)
    project.takes.extend([stale, fresh])
    assert farm_module._candidate_score(fresh, project) > farm_module._candidate_score(stale, project)
    # Without a project the legacy ranking still orders by verdict then fidelity.
    assert farm_module._candidate_score(stale) > farm_module._candidate_score(fresh)
    second.selected_take_id = stale.id
    monkeypatch.setattr(farm_module, "persist_project", lambda project: None)
    monkeypatch.setattr(farm_module, "record_activity", lambda *a, **k: None)
    monkeypatch.setattr(farm_module, "record_decision", lambda *a, **k: None)
    monkeypatch.setattr(farm_module, "assemble_program", lambda project: None)
    assert farm_module.select_hero_print(project, second) is fresh
    assert second.selected_take_id == fresh.id
