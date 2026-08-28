import importlib

from fastapi.testclient import TestClient
import pytest

from diony_producer.app import app


def test_studio_new_is_blank_not_night_press() -> None:
    client = TestClient(app)
    client.post("/api/reset")
    demo = client.get("/api/project").json()
    assert demo["id"] == "ep-pilot-night-press"
    fresh = client.post("/api/studio/new")
    assert fresh.status_code == 200
    body = fresh.json()
    assert body["id"] != "ep-pilot-night-press"
    assert body["bible"]["title"] == "Untitled"
    assert body["shots"] == []
    assert body["takes"] == []
    farm = client.get("/api/farm").json()
    assert farm["busy"] is False
    assert farm["queued"] == 0
    client = TestClient(app)
    client.post("/api/reset")
    project = client.get("/api/project").json()
    assert project["id"] == "ep-pilot-night-press"
    assert project["bible"]["width"] == 1920


def test_catalog_lists_length_allotments() -> None:
    client = TestClient(app)
    cat = client.get("/api/catalog").json()
    labels = [item["label"] for item in cat["allotments"]]
    assert labels == ["30s", "1m", "3m", "5m", "10m", "15m", "20m", "30m", "40m", "1hr"]
    assert cat["allotments"][0]["minutes"] == 0.5
    assert cat["allotments"][-1]["minutes"] == 60


def test_note_requires_shot() -> None:
    client = TestClient(app)
    res = client.post("/api/notes", json={"body": "orphan"})
    assert res.status_code == 400


def test_mix_before_picture_refused() -> None:
    client = TestClient(app)
    client.post("/api/reset")
    res = client.post("/api/gates/mix/lock", json={"locked_by": "test"})
    assert res.status_code == 400


def test_picture_lock_refuses_spoken_selects_without_voice_performance_qc() -> None:
    client = TestClient(app)
    client.post("/api/reset")
    client.post("/api/shots/sh-020/select/sh-020-t1")
    client.post("/api/shots/sh-030/select/sh-030-t1")
    res = client.post("/api/gates/picture/lock", json={"locked_by": "test"})
    assert res.status_code == 400
    assert "without current reference/continuity QC" in res.json()["detail"]


def test_picture_lock_refuses_a_selected_take_that_failed_autoqc(monkeypatch) -> None:
    from diony_contracts.enums import CameraMove, Engine, FinishIntent, QCVerdict
    from diony_contracts.models import Bible, Project, QCReport, Shot, Take
    from diony_producer.advance import apply_picture_lock

    shot = Shot(
        id="sh-fail",
        scene="1",
        slug="INT. STAGE — FAILED TAKE",
        duration_frames=120,
        fps=23.976,
        focal_mm=40,
        t_stop=2.8,
        lighting_ratio="4:1",
        camera_move=CameraMove.STATIC,
        prompt="A continuity-locked subject crosses frame.",
        seed=7,
        engine=Engine.I2V_A14B,
        finish=FinishIntent.DELIVERABLE,
        selected_take_id="take-fail",
    )
    take = Take(
        id="take-fail",
        shot_id=shot.id,
        attempt=1,
        engine=Engine.I2V_A14B,
        seed=7,
        qc=QCReport(
            id="qc-fail",
            take_id="take-fail",
            shot_id=shot.id,
            verdict=QCVerdict.FAIL,
            identity_ok=False,
            morph_ok=True,
            flicker_ok=True,
            duration_ok=True,
            legal_ok=True,
            notes="Identity drift",
        ),
    )
    project = Project(
        id="ep-fail-lock",
        bible=Bible(
            title="Failed lock",
            logline="A selected take cannot bypass hard QC.",
            visual_grammar="Controlled studio grammar.",
            world="One stage.",
        ),
        shots=[shot],
        takes=[take],
    )
    monkeypatch.setattr("diony_producer.advance.persist_project", lambda _project: None)

    with pytest.raises(ValueError, match="failed AutoQC.*sh-fail.*take-fail"):
        apply_picture_lock(project, "test")


def test_finish_gate_rejects_legacy_first_frame_only_reference_receipts(monkeypatch) -> None:
    from diony_contracts.enums import AssetKind, CustomerKind, QCVerdict
    from diony_contracts.models import Asset, Brief, QCReport, Take
    from diony_contracts.studio import expand_idea
    from diony_producer import advance

    still = Asset(
        id="ast-team",
        kind=AssetKind.IMAGE,
        filename="Team.png",
        mime="image/png",
        bytes=12,
        path="assets/ast-team/Team.png",
    )
    project = expand_idea(
        Brief(
            idea=(
                "Using this attached team of characters and same background. Illustrate the "
                "characters collaborating on brainstorming and directing a scene."
            ),
            customer_kind=CustomerKind.SERIES,
            target_minutes=10 / 60,
            production_kind="sample",
            sample_seconds=10,
            attachment_ids=[still.id],
        ),
        assets=[still],
    )
    for index, shot in enumerate(project.shots, start=1):
        take_id = f"{shot.id}-t1"
        shot.selected_take_id = take_id
        project.takes.append(
            Take(
                id=take_id,
                shot_id=shot.id,
                attempt=1,
                engine=shot.engine,
                seed=shot.seed,
                qc=QCReport(
                    id=f"qc-{take_id}",
                    take_id=take_id,
                    shot_id=shot.id,
                    verdict=QCVerdict.PASS,
                    identity_ok=True,
                    morph_ok=True,
                    flicker_ok=True,
                    duration_ok=True,
                    legal_ok=True,
                    reference_asset_ids=[still.id],
                    reference_similarity=0.99,
                    reference_similarities={"first": 0.99},
                    reference_verified=True,
                    continuity_verified=True,
                ),
            )
        )
    monkeypatch.setattr(advance, "take_has_live_negative", lambda *_args: True)

    assert advance.farm_ready_for_finish(project, live=True) is False


def test_studio_open_from_idea() -> None:
    client = TestClient(app)
    res = client.post(
        "/api/studio/open",
        json={
            "idea": "A lighthouse keeper paints the fog the color of warnings.",
            "customer_kind": "feature",
            "target_minutes": 5,
        },
    )
    assert res.status_code == 200
    project = res.json()
    assert project["customer_kind"] == "feature"
    assert project["brief"]["target_minutes"] == 5
    assert len(project["shots"]) == 99
    assert project["bible"]["width"] == 1920
    rolled = client.post("/api/studio/roll")
    assert rolled.status_code == 200
    assert len(rolled.json()["takes"]) == len(project["shots"])


def test_studio_open_length_goal_is_a_goal_not_trt() -> None:
    client = TestClient(app)
    short = client.post(
        "/api/studio/open",
        json={
            "idea": "A lighthouse keeper paints the fog the color of warnings.",
            "customer_kind": "feature",
            "target_minutes": 0.5,
        },
    )
    assert short.status_code == 200
    assert short.json()["brief"]["target_minutes"] == 0.5
    assert len(short.json()["shots"]) == 10
    hour = client.post(
        "/api/studio/open",
        json={
            "idea": "A season of a newsroom that prints the future.",
            "customer_kind": "series",
            "target_minutes": 60,
        },
    )
    assert hour.status_code == 200
    assert hour.json()["brief"]["target_minutes"] == 60
    assert len(hour.json()["shots"]) == 12
    assert any("First reel" in n["body"] for n in hour.json()["notes"])


def test_program_stitch_after_roll() -> None:
    client = TestClient(app)
    client.post("/api/reset")
    opened = client.post(
        "/api/studio/open",
        json={
            "idea": "A lighthouse keeper paints the fog the color of warnings.",
            "customer_kind": "feature",
            "target_minutes": 3,
        },
    )
    assert opened.status_code == 200
    rolled = client.post("/api/studio/roll")
    assert rolled.status_code == 200
    body = rolled.json()
    assert body["timeline"]["stitched"] is True
    assert body["timeline"]["total_frames"] > 0
    i2v = next(s for s in body["shots"] if s["engine"] == "i2v-A14B")
    event = next(e for e in body["timeline"]["events"] if e["shot_id"] == i2v["id"])
    assert event["keyframe_from_take_id"]
    view = client.get("/api/program").json()
    assert view["clips"]
    assert view["reel"] == "short"
    again = client.post("/api/program/assemble")
    assert again.status_code == 200
    assert again.json()["program"]["stitched"] is True


def test_reroll_preserves_the_prompt_receipt_for_existing_takes(monkeypatch) -> None:
    module = importlib.import_module("diony_producer.app")
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
    monkeypatch.setattr(module, "expand_on_site", lambda shot, bible: f"expanded-once:{shot.id}")
    first = client.post("/api/studio/roll")
    assert first.status_code == 200
    prompts = [shot["prompt"] for shot in first.json()["shots"]]

    monkeypatch.setattr(module, "expand_on_site", lambda shot, bible: f"changed-on-reroll:{shot.id}")
    second = client.post("/api/studio/roll")

    assert second.status_code == 200
    assert [shot["prompt"] for shot in second.json()["shots"]] == prompts


def test_gallery_empty_until_a_program_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DIONY_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path / "vault"))
    client = TestClient(app)
    opened = client.post(
        "/api/studio/open",
        json={
            "idea": "A lighthouse keeper paints the fog the color of warnings.",
            "customer_kind": "feature",
            "target_minutes": 0.5,
        },
    )
    assert opened.status_code == 200
    client.post("/api/studio/roll")
    gallery = client.get("/api/gallery").json()
    assert gallery["items"] == []
    catalog = client.get("/api/catalog").json()
    assert catalog["floors"] == ["idea", "production", "gallery"]
    assert "edit_engine" not in catalog


def test_run_status_after_produce_auto_locks_picture_and_mix(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DIONY_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path / "vault"))
    client = TestClient(app)
    opened = client.post(
        "/api/studio/open",
        json={
            "idea": "A lighthouse keeper paints the fog the color of warnings.",
            "customer_kind": "social",
            "target_minutes": 1,
        },
    )
    assert opened.status_code == 200
    rolled = client.post("/api/studio/roll")
    assert rolled.status_code == 200
    gates = {gate["name"]: gate for gate in rolled.json()["gates"]}
    assert gates["picture"]["open"] is False
    assert gates["mix"]["open"] is False
    assert gates["distribution"]["open"] is True
    run = client.get("/api/run").json()
    assert run["camera"] == "stub"
    assert run["phase"] == "distribution"
    assert run["waiting"] == "distribution"
    assert "distribution" in run["now"].lower()
    farm = next(step for step in run["steps"] if step["id"] == "farm")
    assert farm["state"] == "stub"
    picture = next(step for step in run["steps"] if step["id"] == "picture")
    assert picture["state"] == "done"
    mix = next(step for step in run["steps"] if step["id"] == "mix")
    assert mix["state"] == "done"
    assert run["shots"] == len(rolled.json()["shots"])
    clock = run["clock"]
    assert clock["complete"] is True
    assert clock["running"] is False
    assert clock["label"] == "Total"
    assert clock["started_at"]
    assert clock["ended_at"]
    assert clock["display"]


def test_signed_b200_run_does_not_say_wan_cannot_start(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DIONY_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path / "vault"))
    from diony_contracts.cluster import write_site_lock

    write_site_lock(
        {
            "sku": "b200-v1",
            "compute_path": "b200",
            "gpu_ready": True,
            "nodes": ["198.13.252.68"],
            "connect": {
                "host": "198.13.252.68",
                "ssh_user": "root",
                "ssh_port": "32843",
            },
            "compute": {
                "path": "b200",
                "profile": "b200-v1",
                "gpu_class": "1×B200",
                "exception_by": "Rohn",
            },
        }
    )
    client = TestClient(app)
    opened = client.post(
        "/api/studio/open",
        json={
            "idea": "A lighthouse keeper paints the fog the color of warnings.",
            "customer_kind": "social",
            "target_minutes": 1,
        },
    )
    assert opened.status_code == 200
    rolled = client.post("/api/studio/roll")
    assert rolled.status_code == 200
    # pytest never SSHes. The desk still must not call this a stub camera.
    run = client.get("/api/run").json()
    assert run["camera"] == "site"
    assert run["phase"] == "gpu"
    assert run["waiting"] is None
    assert run["reach"]["host"] == "198.13.252.68"
    assert run["reach"]["port"] == "32843"
    assert "Studio Connect" in run["now"]
    farm = next(step for step in run["steps"] if step["id"] == "farm")
    assert farm["state"] == "blocked"
    assert "cannot start" not in farm["note"]
    assert "signed GPU" in farm["note"] or "not the camera" in farm["note"]
    assert "diony_sr_v1" not in run["now"]
    assert "on this site" not in run["now"]


def test_desk_run_uses_remote_run_state_instead_of_rechecking_remote_media_locally(monkeypatch) -> None:
    module = importlib.import_module("diony_producer.app")
    remote_run = {
        "source": "gpu-run",
        "now": "Denoise 1/24 · sh-040 · clip 4 of 6",
        "tally": "3 / 6",
        "monitoring": {
            "connection": "live",
            "gpu": {},
            "signal": {"updated_at": ""},
        },
    }

    class Result:
        def __init__(self, payload):
            self.status_code = 200
            self._payload = payload

        def json(self):
            return self._payload

    def remote_get(url, **_kwargs):
        if url.endswith("/api/run"):
            return Result(remote_run)
        if url.endswith("/api/project"):
            return Result(module._dump())
        if url.endswith("/api/farm"):
            return Result(
                {
                    "busy": True,
                    "detail": "Denoise 1/24 · sh-040",
                    "error": "",
                    "shot_id": "sh-040",
                    "done": 3,
                    "total": 6,
                    "queued": 2,
                    "progress": {"step": 1, "total": 24, "pct": 4},
                    "live": True,
                }
            )
        if url.endswith("/api/finish"):
            return Result({"busy": False, "detail": "", "error": "", "live": True})
        if url.endswith("/api/mix"):
            return Result({"busy": False, "detail": "", "error": ""})
        raise AssertionError(url)

    monkeypatch.setattr(module, "should_proxy", lambda: True)
    monkeypatch.setattr(
        module,
        "ensure_gpu_producer",
        lambda: (_ for _ in ()).throw(AssertionError("GET /api/run reconciled the GPU")),
    )
    monkeypatch.setattr(module, "gpu_producer_ready_base", lambda: "http://gpu")
    monkeypatch.setattr(
        module,
        "drive_remote_advance",
        lambda _base: (_ for _ in ()).throw(AssertionError("GET /api/run mutated production")),
    )
    monkeypatch.setattr(
        module,
        "gpu_monitor",
        lambda: (_ for _ in ()).throw(AssertionError("GET /api/run opened an SSH probe")),
    )
    monkeypatch.setattr(module.httpx, "get", remote_get)

    run = module.get_run()

    assert run["source"] == "gpu-run"
    assert run["tally"] == "3 / 6"
    assert "clip 4 of 6" in run["now"]


def test_wizard_b200_pack_is_not_a_desk_sr_gap(tmp_path, monkeypatch) -> None:
    """Studio Sign + Test GPU: 1080 is on that GPU. Incomplete farm is not 'Wan 720p is up'."""
    monkeypatch.setenv("DIONY_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path / "vault"))
    from diony_contracts.cluster import write_site_lock
    from diony_contracts.models import Project
    from diony_producer.run import run_payload

    write_site_lock(
        {
            "sku": "b200-v1",
            "compute_path": "b200",
            "gpu_ready": True,
            "nodes": ["198.13.252.68"],
            "connect": {
                "host": "198.13.252.68",
                "ssh_user": "root",
                "ssh_port": "32843",
            },
            "compute": {
                "path": "b200",
                "profile": "b200-v1",
                "gpu_class": "1×B200",
                "exception_by": "Rohn",
            },
        }
    )
    client = TestClient(app)
    opened = client.post(
        "/api/studio/open",
        json={
            "idea": "A lighthouse keeper paints the fog the color of warnings.",
            "customer_kind": "social",
            "target_minutes": 1,
        },
    )
    assert opened.status_code == 200
    rolled = client.post("/api/studio/roll")
    assert rolled.status_code == 200
    project = Project.model_validate(rolled.json())
    shots = len(project.shots)
    board = run_payload(
        project,
        farm={"busy": False, "live": True, "done": 0, "total": shots, "detail": "", "shot_id": ""},
        finish={"busy": False, "live": False, "detail": ""},
        live=True,
    )
    assert "on this site" not in board["now"]
    assert "needs diony_sr_v1" not in board["now"]
    assert "Wan2.2" in board["now"]
    assert board["phase"] == "farm"
    picture = next(step for step in board["steps"] if step["id"] == "picture")
    assert picture["state"] != "blocked"
    farm = next(step for step in board["steps"] if step["id"] == "farm")
    assert farm["state"] == "next"


def test_run_clock_ticks_while_farm_is_busy(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DIONY_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path / "vault"))
    from diony_contracts.models import Project
    from diony_producer.run import run_payload

    client = TestClient(app)
    opened = client.post(
        "/api/studio/open",
        json={
            "idea": "A lighthouse keeper paints the fog the color of warnings.",
            "customer_kind": "social",
            "target_minutes": 1,
        },
    )
    assert opened.status_code == 200
    rolled = client.post("/api/studio/roll")
    assert rolled.status_code == 200
    project = Project.model_validate(rolled.json())
    project.production_ended_at = None
    board = run_payload(
        project,
        farm={
            "busy": True,
            "live": True,
            "done": 1,
            "total": len(project.shots),
            "detail": "Generating 720p",
            "shot_id": "sh-010",
        },
        finish={"busy": False, "live": True, "detail": ""},
        live=True,
    )
    clock = board["clock"]
    assert clock["running"] is True
    assert clock["complete"] is False
    assert clock["label"] == "Elapsed"
    assert clock["started_at"]
    assert clock["elapsed_s"] >= 0


def test_mix_waits_while_1080_finish_reports_frame_progress(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DIONY_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path / "vault"))
    from diony_contracts.models import Project
    from diony_producer.run import run_payload

    client = TestClient(app)
    opened = client.post(
        "/api/studio/open",
        json={
            "idea": "A lighthouse keeper paints the fog the color of warnings.",
            "customer_kind": "social",
            "target_minutes": 1,
        },
    )
    assert opened.status_code == 200
    project = Project.model_validate(client.post("/api/studio/roll").json())
    next(gate for gate in project.gates if gate.name.value == "picture").open = False

    board = run_payload(
        project,
        farm={"busy": False, "live": True, "done": len(project.shots), "total": len(project.shots), "detail": ""},
        finish={"busy": True, "live": True, "detail": "diony_sr_v1 sh-010-t1 · frame 24/120", "error": ""},
        live=True,
    )
    mix = next(step for step in board["steps"] if step["id"] == "mix")
    assert mix["state"] == "next"
    assert mix["note"] == "after 1080 finish"
    assert "frame 24/120" in board["now"]


def test_run_activity_drops_structured_http_access_rows(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DIONY_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path / "vault"))
    from diony_contracts.models import Project
    import diony_producer.run as run_module

    client = TestClient(app)
    opened = client.post(
        "/api/studio/open",
        json={
            "idea": "A lighthouse keeper paints the fog the color of warnings.",
            "customer_kind": "social",
            "target_minutes": 1,
        },
    )
    assert opened.status_code == 200
    rolled = client.post("/api/studio/roll")
    assert rolled.status_code == 200
    project = Project.model_validate(rolled.json())
    monkeypatch.setattr(
        run_module,
        "gpu_monitor",
        lambda: {
            "heartbeat": "GPU line is up",
            "nvidia": "B200, 93%, 8192 MiB, 192000 MiB",
            "log": "",
            "generate": "Wan live",
            "journal": [
                {
                    "src": "gpu",
                    "level": "info",
                    "text": '{"event":"http.request.completed","method":"GET","path":"/api/run","status_code":200}',
                },
                {"src": "wan", "level": "live", "text": "weights loaded", "shot_id": ""},
            ],
        },
    )

    board = run_module.run_payload(
        project,
        farm={"busy": True, "live": True, "done": 0, "total": len(project.shots), "detail": "Wan live", "shot_id": ""},
        finish={"busy": False, "live": True, "detail": ""},
        live=True,
    )
    texts = [row.get("text") or "" for row in board["activity"]]
    assert any("weights loaded" in text for text in texts)
    assert all("http.request.completed" not in text for text in texts)
    assert all('"path":"/api/run"' not in text for text in texts)


def test_run_board_shows_wan_qc_failure(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DIONY_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path / "vault"))
    from diony_contracts.cluster import write_site_lock
    from diony_contracts.enums import QCVerdict
    from diony_contracts.models import Project
    from diony_producer.run import run_payload

    write_site_lock(
        {
            "sku": "b200-v1",
            "compute_path": "b200",
            "gpu_ready": True,
            "compute": {
                "path": "b200",
                "profile": "b200-v1",
                "gpu_class": "1×B200",
                "exception_by": "Rohn",
            },
        }
    )
    client = TestClient(app)
    opened = client.post(
        "/api/studio/open",
        json={
            "idea": "A lighthouse keeper paints the fog the color of warnings.",
            "customer_kind": "social",
            "target_minutes": 0.5,
        },
    )
    assert opened.status_code == 200
    rolled = client.post("/api/studio/roll")
    assert rolled.status_code == 200
    project = Project.model_validate(rolled.json())
    for take in project.takes:
        if take.qc:
            take.qc.verdict = QCVerdict.FAIL
            take.qc.notes = "ModuleNotFoundError: No module named 'torch'"
    board = run_payload(
        project,
        farm={"busy": False, "live": True, "done": 0, "total": len(project.shots), "detail": "", "shot_id": ""},
        finish={"busy": False, "live": True, "detail": ""},
        live=True,
    )
    assert board["phase"] == "farm"
    assert "torch" in board["now"]
    assert "next ·" not in board["now"]
    farm = next(step for step in board["steps"] if step["id"] == "farm")
    assert farm["state"] == "failed"
    stitch = next(step for step in board["steps"] if step["id"] == "stitch")
    assert stitch["state"] != "done"
    assert board["clips"]
    assert board["clips"][0]["state"] == "failed"
    assert any("torch" in (row.get("text") or "") for row in board["activity"])
    assert all("GET /api/" not in (row.get("text") or "") for row in board["activity"])
    assert any(row.get("level") == "fail" for row in board["activity"])
    assert any(row.get("src") == "qc" for row in board["activity"])


def test_site_lock_stamps_dual_and_retake() -> None:
    from diony_contracts.cluster import write_site_lock

    write_site_lock(
        {
            "sku": "spark-2x-v1",
            "compute_path": "dual",
            "compute": {
                "path": "dual",
                "profile": "spark-2x-v1",
                "gpu_class": "2×GB10",
                "exception_by": "Rohn Springfield",
            },
            "nodes": ["spark-01", "spark-02"],
            "vault": "nfs://vault",
            "recipe": "spark-2x-v1 TP=2",
            "sequential": True,
            "producer_url": "http://producer.diony.local",
            "exception_by": "Rohn Springfield",
        }
    )
    client = TestClient(app)
    client.post("/api/reset")
    site = client.get("/api/site").json()
    assert site["path"] == "dual"
    assert site["sequential"] is True
    assert site["label"] == "Leased Dual-Spark Cluster"
    catalog = client.get("/api/catalog").json()
    assert catalog["site"]["path"] == "dual"
    assert "Do not quote" in catalog["clock"]
    project = client.get("/api/project").json()
    assert project["compute"]["path"] == "dual"
    opened = client.post(
        "/api/studio/open",
        json={
            "idea": "A lighthouse keeper paints the fog the color of warnings.",
            "customer_kind": "feature",
            "target_minutes": 2,
        },
    )
    assert opened.status_code == 200
    assert opened.json()["compute"]["path"] == "dual"
    rolled = client.post("/api/studio/roll")
    assert rolled.status_code == 200
    shot_id = opened.json()["shots"][0]["id"]
    retake = client.post(f"/api/shots/{shot_id}/retake")
    assert retake.status_code == 200
    gens = [
        t
        for t in retake.json()["takes"]
        if t["shot_id"] == shot_id and t["origin"] == "generation"
    ]
    assert len(gens) == 2
    from diony_producer.vault import load_current_project

    loaded = load_current_project()
    assert loaded is not None
    assert loaded.id == retake.json()["id"]
    assert loaded.compute.path.value == "dual"


def test_retake_cap_and_picture_lock() -> None:
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
    client.post("/api/studio/roll")
    shot_id = opened.json()["shots"][0]["id"]
    for _ in range(3):
        assert client.post(f"/api/shots/{shot_id}/retake").status_code == 200
    capped = client.post(f"/api/shots/{shot_id}/retake")
    assert capped.status_code == 400
    project = client.get("/api/project").json()
    for shot in project["shots"]:
        client.post(f"/api/shots/{shot['id']}/select/{shot['selected_take_id']}")
    locked = client.post("/api/gates/picture/lock", json={"locked_by": "test"})
    assert locked.status_code == 200
    refused = client.post(f"/api/shots/{shot_id}/retake")
    assert refused.status_code == 400


def test_reference_locked_retake_refreshes_strict_camera_direction() -> None:
    client = TestClient(app)
    client.post("/api/reset")
    uploaded = client.post(
        "/api/assets",
        files={"file": ("character.png", b"reference-image", "image/png")},
        data={"role": "idea_ref"},
    )
    asset_id = uploaded.json()["id"]
    opened = client.post(
        "/api/studio/open",
        json={
            "idea": "Using this attached character and same background, animate the character directly.",
            "customer_kind": "series",
            "production_kind": "sample",
            "sample_seconds": 10,
            "attachment_ids": [asset_id],
        },
    ).json()
    client.post("/api/studio/roll")
    module = importlib.import_module("diony_producer.app")
    shot = module._project.shot(opened["shots"][1]["id"])
    shot.prompt = "SAMPLE PAYOFF. Television coverage: masters, overs, inserts."
    shot.negative_prompt = ""

    response = client.post(f"/api/shots/{shot.id}/retake")

    assert response.status_code == 200
    refreshed = next(item for item in response.json()["shots"] if item["id"] == shot.id)
    assert "previous final frame" in refreshed["prompt"].casefold()
    assert "television coverage" not in refreshed["prompt"].casefold()
    assert refreshed["camera_move"] == "static"
    assert "title card" in refreshed["negative_prompt"].casefold()


def test_studio_open_reports_prompt_budget_rejection_as_client_error(monkeypatch) -> None:
    module = importlib.import_module("diony_producer.app")

    def reject_prompt(*_args, **_kwargs):
        raise ValueError("Wan prompt compiler produced an invalid plan: prompt exceeds the 140-word attention budget")

    monkeypatch.setattr(module, "expand_idea", reject_prompt)
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(
        "/api/studio/open",
        json={
            "idea": "A tightly authored cinematic sample.",
            "customer_kind": "feature",
            "production_kind": "sample",
            "sample_seconds": 5,
        },
    )

    assert response.status_code == 400
    assert "attention budget" in response.json()["detail"]


def test_signed_gpu_mutation_fails_closed_when_remote_control_plane_is_not_ready(
    monkeypatch,
) -> None:
    module = importlib.import_module("diony_producer.app")
    monkeypatch.setattr(module, "should_proxy", lambda: True)
    monkeypatch.setattr(module, "gpu_producer_ready_base", lambda: None)
    # Mutations wait for a restarting GPU Producer (GPU_MUTATION_WAIT_SECONDS);
    # this test covers what happens once that wait is exhausted.
    monkeypatch.setattr(module, "GPU_MUTATION_WAIT_SECONDS", 0.0)
    monkeypatch.setattr(module, "_start_gpu_reconcile", lambda: None)
    client = TestClient(app)

    response = client.post(
        "/api/assets",
        files={"file": ("reference.png", b"reference", "image/png")},
        data={"role": "idea_ref"},
    )

    assert response.status_code == 503
    assert "GPU control plane" in response.json()["detail"]


def test_mutations_wait_for_a_restarting_gpu_producer_instead_of_failing(monkeypatch) -> None:
    """2026-08-27: an image dropped on Idea during the GPU Producer's restart got
    'GPU control plane did not accept the request' — the desk gave up on the first refusal."""
    import sys

    app_module = sys.modules["diony_producer.app"]
    monkeypatch.setattr(app_module, "should_proxy", lambda: True)
    monkeypatch.setattr(app_module, "GPU_MUTATION_WAIT_SECONDS", 30.0)
    monkeypatch.setattr(app_module, "GPU_MUTATION_POLL_SECONDS", 0.0)
    readiness = iter([None, None, "http://gpu"])
    monkeypatch.setattr(app_module, "gpu_producer_ready_base", lambda: next(readiness, "http://gpu"))
    monkeypatch.setattr(app_module, "_start_gpu_reconcile", lambda: None)
    attempts: list[str] = []

    async def forward(request, body, base):
        attempts.append(base)
        if len(attempts) == 1:
            raise app_module.httpx.ConnectError("connection refused")
        return app_module.Response(content=b'{"ok": true}', status_code=200, media_type="application/json")

    monkeypatch.setattr(app_module, "_forward_to_gpu", forward)
    client = TestClient(app)
    res = client.post("/api/notes", json={"body": "x", "shot_id": "sh-010"})
    assert res.status_code == 200 and res.json() == {"ok": True}
    assert attempts == ["http://gpu", "http://gpu"], "first refusal is retried, not surfaced"


def test_mutations_give_up_with_a_plain_explanation_after_the_wait(monkeypatch) -> None:
    import sys

    app_module = sys.modules["diony_producer.app"]
    monkeypatch.setattr(app_module, "should_proxy", lambda: True)
    monkeypatch.setattr(app_module, "GPU_MUTATION_WAIT_SECONDS", 0.0)
    monkeypatch.setattr(app_module, "GPU_MUTATION_POLL_SECONDS", 0.0)
    monkeypatch.setattr(app_module, "gpu_producer_ready_base", lambda: None)
    monkeypatch.setattr(app_module, "_start_gpu_reconcile", lambda: None)
    monkeypatch.setattr(app_module, "last_gpu_error", lambda: "GPU Producer is starting; the desk will connect when it answers.")
    client = TestClient(app)
    res = client.post("/api/notes", json={"body": "x", "shot_id": "sh-010"})
    assert res.status_code == 503
    detail = res.json()["detail"]
    assert "restarting after an update" in detail and "try the same action again" in detail
    assert "GPU Producer is starting" in detail
    assert res.headers.get("retry-after") == "30"
    # Reads never wait: they fall back to the local project immediately.
    assert client.get("/api/project").status_code == 200
