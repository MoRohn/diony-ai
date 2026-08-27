import shutil
import subprocess
import time
import importlib
import threading

from fastapi.testclient import TestClient

from diony_contracts.enums import ArtifactKind, GateName, MixLockState
from diony_contracts.fixture import demo_project
from diony_producer.app import app
from diony_producer.finish import _attach_1080, _take_1080, finish_rel, selected_masters_ready


def test_picture_lock_writes_1080_master(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DIONY_DRY_RUN", raising=False)
    monkeypatch.setenv("DIONY_LIVE", "1")
    monkeypatch.setenv("DIONY_FINISH_FAKE", "1")
    monkeypatch.delenv("DIONY_FINISH_URL", raising=False)
    monkeypatch.setenv("DIONY_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path / "vault"))
    client = TestClient(app)
    client.post("/api/reset")
    module = importlib.import_module("diony_producer.app")
    for shot in module._project.shots:
        shot.audio_line = None
        shot.dialogue_turns = []
    sample = client.get("/api/project").json()
    module = importlib.import_module("diony_producer.app")
    # This test owns picture/finish lifecycle only. Required dialogue has its own
    # Mix coverage and must never silently fall back when CosyVoice is absent.
    for shot in module._project.shots:
        shot.audio_line = None
    from diony_producer import farm as farm_module

    monkeypatch.setattr(farm_module, "_reference_similarity", lambda *args: 1.0)
    monkeypatch.setattr(
        farm_module,
        "probe_media",
        lambda path, **kwargs: {
            "width": 1280,
            "height": 720,
            "fps": 23.976,
            "frames": 120,
            "codec": "h264",
            "has_audio": False,
        },
    )
    sample_negative = tmp_path / "sample-negative.mp4"
    subprocess.run(
        [
            shutil.which("ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=1280x720:r=24:d=0.5",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(sample_negative),
        ],
        check=True,
    )
    for take in sample["takes"]:
        dest = (
            tmp_path
            / "vault"
            / "projects"
            / sample["id"]
            / "takes"
            / take["id"]
            / "negative_720.mp4"
        )
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(sample_negative.read_bytes())
    for take in module._project.takes:
        rel = f"projects/{module._project.id}/takes/{take.id}/negative_720.mp4"
        farm_module.attach_negative(
            module._project,
            take,
            rel,
            module._project.shot(take.shot_id).duration_frames,
            live=True,
        )
    for shot in sample["shots"]:
        if shot.get("selected_take_id"):
            client.post(f"/api/shots/{shot['id']}/select/{shot['selected_take_id']}")
    client.post("/api/shots/sh-020/select/sh-020-t1")
    client.post("/api/shots/sh-030/select/sh-030-t1")
    locked = client.post("/api/gates/picture/lock", json={"locked_by": "test"})
    assert locked.status_code == 200
    fin = client.get("/api/finish").json()
    assert fin["live"] is True
    deadline = time.time() + 20
    found = None
    while time.time() < deadline:
        project = client.get("/api/project").json()
        for take in project["takes"]:
            for art in take["artifacts"]:
                if "finish_1080" in art["path"] and art.get("width") == 1920:
                    path = tmp_path / "vault" / art["path"]
                    if path.is_file() and path.stat().st_size > 32:
                        found = path
                        break
        if found:
            break
        time.sleep(0.2)
    assert found is not None, "picture lock should write a 1080 master"
    master_file = None
    deadline = time.time() + 20
    while time.time() < deadline:
        project = client.get("/api/project").json()
        master = project["timeline"]["preview_path"]
        if "master_1080" in (master or ""):
            dest = tmp_path / "vault" / master
            if dest.is_file() and dest.stat().st_size > 32:
                master_file = dest
                break
        time.sleep(0.2)
    assert master_file is not None, "picture lock should stitch a 1080 program master"
    # No dialogue is authored and no cues are planned: mute clips are allowed,
    # so the mix delivers a picture-only master with the policy on the receipt
    # instead of refusing.
    deadline = time.time() + 20
    project = client.get("/api/project").json()
    status = client.get("/api/mix").json()
    while time.time() < deadline and not project["mix"].get("picture_only") and not status["error"]:
        time.sleep(0.2)
        project = client.get("/api/project").json()
        status = client.get("/api/mix").json()
    assert status["error"] == ""
    assert project["mix"]["picture_only"] is True
    assert project["mix"]["policy"].startswith("mute:")
    assert len(project["mix"]["stem_asset_ids"]) == 1
    assert len(project["mix"]["stem_asset_ids"]) == 1, "the mute program stem is the only stem"


def test_finish_never_accepts_a_legacy_cross_project_master(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DIONY_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path / "vault"))
    project = demo_project()
    take = project.takes[0]
    scoped = finish_rel(project, take)
    assert scoped == f"projects/{project.id}/takes/{take.id}/finish_1080.mov"

    legacy = tmp_path / "vault" / "takes" / take.id / "finish_1080.mov"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_bytes(b"\x00\x00\x00\x18ftypqt  " + b"old-run" * 32)
    for row in project.takes:
        row.artifacts = [art for art in row.artifacts if art.kind.value != "finish_1080"]

    assert selected_masters_ready(project) is False


def test_finish_never_accepts_a_flat_green_existing_master(tmp_path, monkeypatch) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return
    monkeypatch.delenv("DIONY_DRY_RUN", raising=False)
    monkeypatch.delenv("DIONY_FINISH_FAKE", raising=False)
    monkeypatch.setenv("DIONY_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path / "vault"))
    project = demo_project()
    take = project.takes[0]
    shot = project.shot(take.shot_id)
    negative = next(art for art in take.artifacts if art.kind == ArtifactKind.NEGATIVE_720)
    negative.path = f"projects/{project.id}/takes/{take.id}/negative_720.mp4"
    negative.project_id = project.id
    negative.shot_id = shot.id
    negative.sha256 = "source-negative"
    destination = tmp_path / "vault" / finish_rel(project, take)
    destination.parent.mkdir(parents=True, exist_ok=True)
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
            str(destination),
        ],
        check=True,
    )
    _attach_1080(
        project,
        take,
        finish_rel(project, take),
        destination.stat().st_size,
        source_artifact=negative,
        digest="green-finish",
    )

    assert _take_1080(project, take) is None


def test_stale_live_mix_lock_reopens_without_audio_evidence(tmp_path, monkeypatch) -> None:
    from diony_producer import advance

    monkeypatch.setenv("DIONY_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path / "vault"))
    monkeypatch.setattr(advance, "finish_status", lambda: {"busy": False, "error": ""})
    monkeypatch.setattr(advance, "farm_status", lambda: {"busy": False})
    monkeypatch.setattr(advance, "live_camera", lambda: True)
    monkeypatch.setattr(advance, "signed_gpu_site", lambda: False)
    monkeypatch.setattr(advance, "farm_ready_for_finish", lambda project, live: True)
    monkeypatch.setattr(advance, "live_finish", lambda: True)
    monkeypatch.setattr(advance, "should_proxy", lambda: False)
    monkeypatch.setattr(advance, "selected_masters_ready", lambda project: True)

    project = demo_project()
    picture = next(gate for gate in project.gates if gate.name == GateName.PICTURE)
    mix_gate = next(gate for gate in project.gates if gate.name == GateName.MIX)
    picture.open = False
    mix_gate.open = False
    mix_gate.locked_by = "producer"
    project.mix.state = MixLockState.LOCKED

    advance.maybe_advance_run(project)

    assert mix_gate.open is True
    assert mix_gate.locked_by is None
    assert project.mix.state == MixLockState.OPEN


def test_picture_lock_refuses_live_without_sr(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DIONY_DRY_RUN", raising=False)
    monkeypatch.setenv("DIONY_LIVE", "1")
    monkeypatch.delenv("DIONY_FINISH_FAKE", raising=False)
    monkeypatch.setenv("DIONY_SR_CKPT", str(tmp_path / "missing-sr"))
    monkeypatch.setenv("DIONY_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path / "vault"))
    client = TestClient(app)
    client.post("/api/reset")
    module = importlib.import_module("diony_producer.app")
    for shot in module._project.shots:
        shot.audio_line = None
        shot.dialogue_turns = []
    sample = client.get("/api/project").json()
    for shot in sample["shots"]:
        if shot.get("selected_take_id"):
            client.post(f"/api/shots/{shot['id']}/select/{shot['selected_take_id']}")
    client.post("/api/shots/sh-020/select/sh-020-t1")
    client.post("/api/shots/sh-030/select/sh-030-t1")
    locked = client.post("/api/gates/picture/lock", json={"locked_by": "test"})
    assert locked.status_code == 400
    assert "diony_sr_v1" in locked.json()["detail"]


def test_finish_model_failure_stays_visible_and_blocks_mix(tmp_path, monkeypatch) -> None:
    from diony_finish.pipeline import FinishError
    from diony_producer import finish as finish_module

    monkeypatch.delenv("DIONY_DRY_RUN", raising=False)
    monkeypatch.setenv("DIONY_LIVE", "1")
    monkeypatch.setenv("DIONY_FINISH_FAKE", "1")
    monkeypatch.delenv("DIONY_FINISH_URL", raising=False)
    monkeypatch.setenv("DIONY_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path / "vault"))
    message = (
        "diony_sr_v1 checkpoint architecture mismatch: conv_first.weight has 3 input "
        "channels; expected 12 for x2 pixel-unshuffle"
    )

    def fail_finish(*args, **kwargs):
        raise FinishError(message)

    monkeypatch.setattr(finish_module, "finish_clip", fail_finish)
    monkeypatch.setattr("diony_producer.advance.maybe_advance_run", lambda project: None)
    client = TestClient(app)
    client.post("/api/reset")
    sample = client.get("/api/project").json()
    module = importlib.import_module("diony_producer.app")
    for shot in module._project.shots:
        shot.audio_line = None
        shot.dialogue_turns = []
    from diony_producer import farm as farm_module

    monkeypatch.setattr(farm_module, "_reference_similarity", lambda *args: 1.0)
    monkeypatch.setattr(
        farm_module,
        "probe_media",
        lambda path, **kwargs: {
            "width": 1280,
            "height": 720,
            "fps": 23.976,
            "frames": 120,
            "codec": "h264",
            "has_audio": False,
        },
    )
    for take in sample["takes"]:
        dest = (
            tmp_path
            / "vault"
            / "projects"
            / sample["id"]
            / "takes"
            / take["id"]
            / "negative_720.mp4"
        )
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 200)
    for take in module._project.takes:
        rel = f"projects/{module._project.id}/takes/{take.id}/negative_720.mp4"
        farm_module.attach_negative(
            module._project,
            take,
            rel,
            module._project.shot(take.shot_id).duration_frames,
            live=True,
        )
    for shot in sample["shots"]:
        selected = shot.get("selected_take_id") or f"{shot['id']}-t1"
        client.post(f"/api/shots/{shot['id']}/select/{selected}")

    locked = client.post("/api/gates/picture/lock", json={"locked_by": "test"})
    assert locked.status_code == 200
    deadline = time.time() + 5
    status = client.get("/api/finish").json()
    while status["busy"] and time.time() < deadline:
        time.sleep(0.05)
        status = client.get("/api/finish").json()

    assert status["busy"] is False
    assert status["error"] == message
    assert status["detail"] == message
    project = client.get("/api/project").json()
    mix = next(gate for gate in project["gates"] if gate["name"] == "mix")
    assert mix["open"] is True
    run = client.get("/api/run").json()
    assert run["phase"] == "finish"
    assert message in run["now"]
    assert next(step for step in run["steps"] if step["id"] == "picture")["state"] == "failed"
    assert next(step for step in run["steps"] if step["id"] == "mix")["state"] == "blocked"


def test_finish_status_exposes_current_clip_frame_progress() -> None:
    from diony_producer import finish as finish_module

    with finish_module._lock:
        finish_module._busy = True
        finish_module._current_take_id = "sh-010-t1"
        finish_module._frame_done = 24
        finish_module._frame_total = 120
        finish_module._done = 2
        finish_module._total = 6
        finish_module._pending = [object(), object(), object()]
    try:
        status = finish_module.finish_status()
        assert status["current_take_id"] == "sh-010-t1"
        assert status["current_shot_id"] == "sh-010"
        assert status["progress"] == {"done": 24, "total": 120, "pct": 20}
        assert status["done"] == 2
        assert status["total"] == 6
        assert status["queued"] == 3
    finally:
        finish_module.reset_finish()


def test_finish_status_names_program_master_verification(monkeypatch) -> None:
    from diony_producer import advance as advance_module
    from diony_producer import finish as finish_module

    project = demo_project()
    entered = threading.Event()
    release = threading.Event()

    def hold_stitch(_project) -> None:
        entered.set()
        release.wait(timeout=5)

    finish_module.reset_finish()
    monkeypatch.setattr(finish_module, "_finish_one", lambda _project, _take: None)
    monkeypatch.setattr(finish_module, "_stitch_program", hold_stitch)
    monkeypatch.setattr(advance_module, "maybe_advance_run", lambda _project: None)
    try:
        finish_module.start_finish(project, [project.takes[0]])
        assert entered.wait(timeout=2)
        status = finish_module.finish_status()
        assert status["busy"] is True
        assert status["current_take_id"] == ""
        assert status["detail"] == "Verifying 1080 program master"
    finally:
        release.set()
        deadline = time.time() + 2
        while finish_module.finish_status()["busy"] and time.time() < deadline:
            time.sleep(0.01)
        finish_module.reset_finish()
