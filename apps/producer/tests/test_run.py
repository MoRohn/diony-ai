from datetime import datetime, timedelta, timezone

from diony_contracts.enums import CameraMove, Engine, FinishIntent, QCVerdict
from diony_contracts.models import (
    Bible,
    Character,
    GenerationExecutionReceipt,
    MixLock,
    Project,
    QCReport,
    Shot,
    Take,
)
from diony_producer.run import format_elapsed, production_clock, production_standard


def _bible() -> Bible:
    return Bible(
        title="Pilot — The Night Press",
        logline="A late-shift typesetter discovers tomorrow's edition already printed.",
        visual_grammar="Tungsten interiors, 35mm spherical, wet streets, restrained camera.",
        world="1962 metropolitan daily. Lead type, rain, one newsroom.",
        characters=[
            Character(
                id="char-mara",
                name="Mara Chen",
                still_asset_ids=["still-mara-wide"],
                wardrobe="Ink-stained oxford, dark wool vest",
            )
        ],
    )


def test_format_elapsed() -> None:
    assert format_elapsed(0) == "0:00"
    assert format_elapsed(75) == "1:15"
    assert format_elapsed(3661) == "1:01:01"


def test_production_clock_elapsed_then_total() -> None:
    started = datetime.now(timezone.utc) - timedelta(seconds=75)
    project = Project(id="ep-clock", bible=_bible(), production_started_at=started)
    live = production_clock(project, rolling=True, complete=False)
    assert live["label"] == "Elapsed"
    assert live["running"] is True
    assert live["complete"] is False
    assert live["elapsed_s"] >= 75
    assert live["display"]
    waiting = production_clock(project, rolling=False, complete=False)
    assert waiting["running"] is True
    assert waiting["label"] == "Elapsed"
    project.mark_production_end()
    done = production_clock(project, rolling=False, complete=True)
    assert done["label"] == "Total"
    assert done["running"] is False
    assert done["complete"] is True
    assert done["ended_at"]


def test_production_clock_farm_fail_stays_elapsed() -> None:
    started = datetime.now(timezone.utc) - timedelta(seconds=12)
    project = Project(id="ep-clock-fail", bible=_bible(), production_started_at=started)
    project.mark_production_end()
    clock = production_clock(project, rolling=False, complete=False)
    assert clock["label"] == "Elapsed"
    assert clock["running"] is False
    assert clock["complete"] is False
    assert clock["ended_at"]


def test_production_clock_falls_back_to_first_take() -> None:
    take = Take(
        id="sh-010-t1",
        shot_id="sh-010",
        attempt=1,
        engine=Engine.I2V_A14B,
        seed=10401,
        created_at=datetime.now(timezone.utc) - timedelta(seconds=40),
        qc=QCReport(
            id="qc-sh-010-t1",
            take_id="sh-010-t1",
            shot_id="sh-010",
            verdict=QCVerdict.PASS,
            identity_ok=True,
            morph_ok=True,
            flicker_ok=True,
            duration_ok=True,
            legal_ok=True,
        ),
    )
    shot = Shot(
        id="sh-010",
        scene="1",
        slug="INT NEWSROOM — MARA AT THE CASE",
        duration_frames=120,
        fps=23.976,
        focal_mm=40,
        t_stop=2.8,
        lighting_ratio="4:1 key/fill",
        camera_move=CameraMove.DOLLY_IN,
        prompt="Mara Chen at a California job case.",
        seed=10401,
        engine=Engine.I2V_A14B,
        finish=FinishIntent.DELIVERABLE,
    )
    project = Project(id="ep-clock-takes", bible=_bible(), shots=[shot], takes=[take])
    clock = production_clock(project, rolling=True, complete=False)
    assert clock["started_at"]
    assert clock["running"] is True
    assert clock["elapsed_s"] >= 40


def test_humanize_fail_does_not_surface_tqdm() -> None:
    from diony_producer.run import _humanize_fail

    note = (
        "Loading checkpoint shards: 100%\n"
        "There are modules in WanModel that should be kept in float32: []. Casting directly with `to`\n"
        "  0%|          | 0/40 [0"
    )
    msg = _humanize_fail(note)
    assert "Loading ch" not in msg
    assert "0/40" not in msg
    assert "float32" not in msg.lower() or "720p" in msg


def test_production_standard_separates_negative_from_verified_master() -> None:
    project = Project(id="ep-standard", bible=_bible())
    standard = production_standard(project, clips=[], camera="stub")

    assert standard["negative"] == {
        "label": "1280×720",
        "model": "Wan2.2 A14B",
        "role": "camera negative · not a master",
    }
    assert standard["final"] == {
        "label": "1920×1080",
        "aspect": "16:9",
        "fps": "23.976",
        "color": "Rec.709 legal",
    }
    assert standard["finish"] == {
        "model": "diony_sr_v1",
        "method": "temporal 720→1080",
        "lanczos_deliverable": False,
    }
    assert standard["audio"] == {
        "target": "−24 LUFS broadcast",
        "true_peak": "−1 dBTP",
    }
    assert standard["status"] == "PLANNING"
    assert standard["material_label"] == "DRY RUN · NO MASTER"
    assert standard["master_ready"] is False
    assert "Stub camera" in " ".join(standard["blockers"])


def test_production_standard_reports_failed_autoqc_as_a_master_blocker() -> None:
    project = Project(id="ep-standard-fail", bible=_bible())
    standard = production_standard(
        project,
        clips=[{"state": "failed"}, {"state": "live"}, {"state": "failed"}],
        camera="live",
    )

    assert "2 selected Takes failed AutoQC." in standard["blockers"]
    assert standard["status"] == "QC HOLD"


def test_production_standard_labels_missing_live_mix_evidence_as_hold() -> None:
    project = Project(id="ep-mix-hold", bible=_bible(), mix=MixLock(id="mix-hold"))
    standard = production_standard(project, clips=[], camera="live", phase="mix")

    assert standard["status"] == "MIX HOLD"
    assert standard["material_label"] == "MIX INCOMPLETE · NOT MASTER"
    assert "Audio stems are missing." in standard["blockers"]


def test_failed_autoqc_outranks_a_landed_negative(monkeypatch) -> None:
    import diony_producer.run as run_module
    from diony_producer.run import _clip_rows

    shot = Shot(
        id="sh-qc-hold",
        scene="1",
        slug="INT. STAGE — QC HOLD",
        duration_frames=120,
        fps=23.976,
        focal_mm=40,
        t_stop=2.8,
        lighting_ratio="4:1",
        camera_move=CameraMove.STATIC,
        prompt="A landed negative with identity drift.",
        seed=8,
        engine=Engine.I2V_A14B,
        finish=FinishIntent.DELIVERABLE,
        selected_take_id="take-qc-hold",
    )
    take = Take(
        id="take-qc-hold",
        shot_id=shot.id,
        attempt=1,
        engine=Engine.I2V_A14B,
        seed=8,
        qc=QCReport(
            id="qc-hold",
            take_id="take-qc-hold",
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
    project = Project(id="ep-qc-hold", bible=_bible(), shots=[shot], takes=[take])
    monkeypatch.setattr("diony_producer.run.take_has_live_negative", lambda _take, _project: True)
    monkeypatch.setattr(run_module, "gpu_monitor", lambda: {"heartbeat": "", "journal": [], "log": ""})

    assert _clip_rows(project, {"busy": False})[0]["state"] == "failed"
    board = run_module.run_payload(
        project,
        farm={"busy": False, "error": "Identity drift", "total": 1},
        finish={"busy": False, "live": True, "error": ""},
        mix={"busy": False, "error": ""},
        live=True,
    )
    assert board["phase"] == "farm"
    assert board["failed"] is True
    assert board["steps"][1]["state"] == "failed"
    assert board["steps"][2]["state"] == "next"


def test_clip_monitor_exposes_actual_lane_and_fallback_reason(monkeypatch) -> None:
    from diony_producer.run import _clip_rows

    shot = Shot(
        id="sh-lane",
        scene="1",
        slug="INT. STAGE — LANE RECEIPT",
        duration_frames=120,
        fps=23.976,
        focal_mm=40,
        t_stop=2.8,
        lighting_ratio="4:1",
        camera_move=CameraMove.STATIC,
        prompt="A verified camera negative.",
        seed=42,
        engine=Engine.I2V_A14B,
        finish=FinishIntent.DELIVERABLE,
        selected_take_id="take-lane",
    )
    receipt = GenerationExecutionReceipt(
        status="succeeded",
        backend="native",
        selected_lane="wan22_quality_cli",
        task="i2v-A14B",
        native_width=1280,
        native_height=720,
        frame_num=81,
        sample_steps=24,
        sample_solver="unipc",
        precision="bfloat16",
        negative_conditioning=True,
        quality_contract_sha256="a" * 64,
        quality_contract_matched=True,
        fallback_reason="Resident Wan validation receipt is not configured.",
        elapsed_seconds=901.0,
        output_sha256="b" * 64,
        output_bytes=1024,
    )
    assert receipt.denoise_passes == 1
    assert receipt.effective_denoise_steps == 24

    with __import__("pytest").raises(ValueError, match="generation fields"):
        GenerationExecutionReceipt(
            **(
                receipt.model_dump(mode="python")
                | {
                    "task": "s2v-14B",
                    "inference_frames_per_pass": 84,
                    "denoise_passes": 2,
                    "effective_denoise_steps": 48,
                }
            )
        )
    take = Take(
        id="take-lane",
        shot_id=shot.id,
        attempt=1,
        engine=Engine.I2V_A14B,
        seed=42,
        generation_receipt=receipt,
        qc=QCReport(
            id="qc-lane",
            take_id="take-lane",
            shot_id=shot.id,
            verdict=QCVerdict.PASS,
            identity_ok=True,
            morph_ok=True,
            flicker_ok=True,
            duration_ok=True,
            legal_ok=True,
        ),
    )
    project = Project(id="ep-lane", bible=_bible(), shots=[shot], takes=[take])
    monkeypatch.setattr("diony_producer.run.take_has_live_negative", lambda *_args: True)

    row = _clip_rows(project, {"busy": False})[0]

    assert row["generation_lane"] == "wan22_quality_cli"
    assert row["generation_lane_label"] == "Quality CLI fallback"
    assert "receipt" in row["generation_fallback"].casefold()
    assert row["generation_elapsed_seconds"] == 901.0
    assert row["inference_frames_per_pass"] == 81
    assert row["denoise_passes"] == 1
    assert row["effective_denoise_steps"] == 24


def test_clip_monitor_tracks_finish_progress_as_structured_milestones(monkeypatch) -> None:
    from diony_producer.run import _clip_rows

    shot = Shot(
        id="sh-finish",
        scene="2",
        slug="INT. LAB — 1080 FINISH",
        duration_frames=120,
        fps=23.976,
        focal_mm=50,
        t_stop=2.8,
        lighting_ratio="3:1",
        camera_move=CameraMove.STATIC,
        prompt="A finished frame settles into registration.",
        seed=12,
        engine=Engine.I2V_A14B,
        finish=FinishIntent.DELIVERABLE,
        selected_take_id="take-finish",
    )
    take = Take(
        id="take-finish",
        shot_id=shot.id,
        attempt=1,
        engine=Engine.I2V_A14B,
        seed=12,
        qc=QCReport(
            id="qc-finish",
            take_id="take-finish",
            shot_id=shot.id,
            verdict=QCVerdict.PASS,
            identity_ok=True,
            morph_ok=True,
            flicker_ok=True,
            duration_ok=True,
            legal_ok=True,
        ),
    )
    project = Project(id="ep-clip-monitor", bible=_bible(), shots=[shot], takes=[take])
    monkeypatch.setattr("diony_producer.run.take_has_live_negative", lambda _take, _project: True)
    monkeypatch.setattr("diony_producer.run._verified_master", lambda _take: False)

    rows = _clip_rows(
        project,
        {"busy": False},
        {
            "busy": True,
            "current_take_id": "take-finish",
            "progress": {"done": 24, "total": 120, "pct": 20},
        },
    )

    assert rows[0]["stage"] == "finish"
    assert rows[0]["state"] == "rolling"
    assert rows[0]["status"] == "Finishing at 1080p"
    assert rows[0]["progress"] == {"done": 24, "total": 120, "pct": 20, "label": "Frame 24 of 120"}
    assert [item["state"] for item in rows[0]["milestones"]] == ["done", "done", "active", "next"]


def test_activity_translates_gpu_weight_loading_without_losing_evidence() -> None:
    from diony_producer.run import _activity_lines

    rows = _activity_lines(
        [],
        {"busy": True, "shot_id": "sh-030", "detail": "Generating 720p"},
        {"busy": False},
        "Generating 720p",
        {
            "journal": [
                {
                    "src": "gpu",
                    "level": "live",
                    "text": "B200 · 0% · 4 / 183359 MiB · loading weights",
                    "at": "06:30:18",
                }
            ]
        },
    )

    weight_row = next(row for row in rows if "loading weights" in row["technical"])
    assert weight_row["title"] == "GPU is loading the camera model"
    assert weight_row["detail"] == "The first clip starts after the model is in GPU memory."
    assert weight_row["at"] == "06:30:18"
    assert weight_row["progress"] == {}


def test_activity_translates_zero_denoise_without_unknown_placeholders() -> None:
    from diony_producer.run import _activity_row

    row = _activity_row(
        "wan",
        "Denoise 0/40 · ?it/s · ? left · sh-020",
        level="live",
        shot_id="sh-020",
    )

    assert row["title"] == "Building sh-020 at 720p"
    assert row["detail"] == "Denoise is starting."
    assert "?" not in row["detail"]
    assert "?it/s" in row["technical"]


def test_activity_exposes_structured_work_context_without_losing_the_raw_message() -> None:
    from diony_producer.run import _activity_row

    row = _activity_row(
        "wan",
        "sh-010-t2 · Denoise 3/24 · 1.42s/it · 00:29 left",
        level="live",
        shot_id="sh-010",
        at="09:46:10",
    )

    assert row["status"] == "working"
    assert row["shot_id"] == "sh-010"
    assert row["take_id"] == "sh-010-t2"
    assert row["progress"] == {
        "done": 3,
        "total": 24,
        "label": "Denoise 3 / 24",
        "rate": "1.42s/it",
        "eta": "00:29 left",
    }
    assert row["technical"] == "sh-010-t2 · Denoise 3/24 · 1.42s/it · 00:29 left"
    assert row["at"] == "09:46:10"


def test_activity_places_current_work_before_history() -> None:
    from diony_producer.run import _activity_lines

    rows = _activity_lines(
        [
            {"shot_id": "sh-010", "state": "live", "note": "ready"},
            {"shot_id": "sh-020", "state": "rolling", "note": "working"},
        ],
        {"busy": True, "shot_id": "sh-020", "detail": "Denoise 14/40"},
        {"busy": False},
        "Denoise 14/40",
        {
            "generate": "generate.py · sh-020-t1 · 09:57",
            "heartbeat": "B200 · 100% · 106566 / 183359 MiB",
            "journal": [
                {"src": "wan", "level": "live", "text": "Farming 6 Wan2.2 clips", "at": "10:48:46"}
            ],
        },
    )

    assert rows[0]["shot_id"] == "sh-020"
    assert rows[0]["level"] == "live"
    assert rows[-1]["technical"] == "Farming 6 Wan2.2 clips"


def test_activity_labels_finish_frames_and_does_not_report_stale_camera_work() -> None:
    from diony_producer.run import _activity_lines

    rows = _activity_lines(
        [
            {
                "shot_id": "sh-010",
                "take_id": "sh-010-t1",
                "state": "rolling",
                "stage": "finish",
                "note": "Frame 38 of 81",
            },
            {"shot_id": "sh-020", "state": "live", "stage": "qc", "note": "ready"},
        ],
        {"busy": False, "shot_id": "", "detail": ""},
        {
            "busy": True,
            "current_take_id": "sh-010-t1",
            "current_shot_id": "sh-010",
            "detail": "diony_sr_v1 sh-010-t1 · frame 38/81",
        },
        "diony_sr_v1 sh-010-t1 · frame 38/81",
        {"journal": []},
    )

    assert rows[0]["src"] == "sr"
    assert rows[0]["level"] == "live"
    assert rows[0]["shot_id"] == "sh-010"
    assert rows[0]["take_id"] == "sh-010-t1"
    assert rows[0]["progress"]["label"] == "Frame 38 / 81"
    assert not any("generating 720p negative" in row["technical"] for row in rows)


def test_activity_keeps_resolved_history_without_calling_it_current_work() -> None:
    from diony_producer.run import _activity_lines

    rows = _activity_lines(
        [{"shot_id": "sh-010", "state": "ready", "stage": "ready", "note": "master"}],
        {"busy": False, "error": "", "shot_id": "", "detail": ""},
        {"busy": False, "error": "", "detail": ""},
        "Waiting on you: distribution (Gallery).",
        {
            "journal": [
                {
                    "at": "15:14:10",
                    "src": "wan",
                    "level": "info",
                    "text": "sh-010 · 720p negative landed",
                    "shot_id": "sh-010",
                },
                {
                    "at": "15:16:14",
                    "src": "sr",
                    "level": "live",
                    "text": "sh-010 · 1080 finish",
                    "shot_id": "sh-010",
                },
                {
                    "at": "15:26:43",
                    "src": "mix",
                    "level": "fail",
                    "text": "1080 program master failed visual QC",
                    "shot_id": "",
                },
                {
                    "at": "15:41:06",
                    "src": "mix",
                    "level": "info",
                    "text": "Mix measured and verified",
                    "shot_id": "",
                },
            ]
        },
        mix={"busy": False, "error": ""},
    )

    assert rows[0]["technical"] == "Mix measured and verified"
    assert all(row["level"] not in {"live", "fail"} for row in rows)
    assert any("failed visual QC" in row["technical"] for row in rows)


def test_camera_tally_uses_landed_negatives_when_worker_counter_restarts(monkeypatch) -> None:
    from diony_contracts.fixture import demo_project
    import diony_producer.run as run_module

    project = demo_project()
    landed = {project.shots[0].id, project.shots[1].id}
    current = project.shots[2].id
    monkeypatch.setattr(
        run_module,
        "take_has_live_negative",
        lambda take, _project: take.shot_id in landed,
    )
    monkeypatch.setattr(run_module, "gpu_monitor", lambda: {"heartbeat": "", "journal": [], "log": ""})

    board = run_module.run_payload(
        project,
        farm={
            "busy": True,
            "live": True,
            "done": 0,
            # A newly adopted/restarted worker only knows about its current
            # one-Take queue. The Production board must retain the durable
            # rundown total from the project instead of rendering 2 / 1.
            "total": 1,
            "detail": f"Denoise 10/40 · {current}",
            "shot_id": current,
            "progress": {"step": 10, "total": 40, "pct": 25},
        },
        finish={"busy": False, "live": True, "detail": ""},
        live=True,
    )

    assert board["tally"] == f"2 / {len(project.shots)}"
    assert f"clip 3 of {len(project.shots)}" in board["now"]
    assert board["progress"]["completed"] == 2
    assert board["progress"]["pct"] == round((2.25 / len(project.shots)) * 100)


def test_qc_hold_tally_never_counts_a_rejected_negative_as_completed(monkeypatch) -> None:
    from diony_contracts.fixture import demo_project
    import diony_producer.run as run_module

    project = demo_project()
    shot = project.shots[0]
    take = project.takes_for(shot.id)[0]
    project.takes = [item for item in project.takes if item.shot_id != shot.id]
    project.takes.insert(0, take)
    shot.selected_take_id = take.id
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
        audiovisual_backend="diony-local-av-motion-sync-v1",
        audiovisual_measurement_qualified=False,
        notes=(
            "Voice performance failed: qualified mouth-sync evidence is unavailable; "
            "diagnostic only, so Diony preserved this Take."
        ),
    )
    monkeypatch.setattr(run_module, "take_has_live_negative", lambda current, _project: current is take)
    monkeypatch.setattr(run_module, "gpu_monitor", lambda: {"heartbeat": "", "journal": [], "log": ""})
    monkeypatch.setattr(run_module, "signed_gpu_site", lambda: False)
    monkeypatch.setattr(run_module, "wizard_pack_on_gpu", lambda: False)

    board = run_module.run_payload(
        project,
        farm={"busy": False, "live": True, "done": 1, "total": 3, "detail": "", "error": ""},
        finish={"busy": False, "live": True, "done": 0, "total": 0, "detail": "", "error": ""},
        mix={"busy": False, "detail": "", "error": ""},
        live=True,
    )

    assert board["tally"] == f"0 passed · 1 failed / {len(project.shots)}"
    assert board["tally_note"] == "QC hold · Take preserved"
    assert board["clips"][0]["attempt_label"] == "attempt 1 of 4"
    assert board["clips"][0]["av_evidence_label"] == "Diagnostic AV only"


def test_run_payload_exposes_mix_busy_as_protected_work(monkeypatch) -> None:
    from diony_contracts.fixture import demo_project
    import diony_producer.run as run_module

    project = demo_project()
    monkeypatch.setattr(run_module, "gpu_monitor", lambda: {"heartbeat": "", "journal": [], "log": ""})

    board = run_module.run_payload(
        project,
        farm={"busy": False, "live": True, "done": 0, "total": 0, "detail": "", "error": ""},
        finish={"busy": False, "live": True, "done": 0, "total": 0, "detail": "", "error": ""},
        mix={"busy": True, "detail": "Measuring mix", "error": ""},
        live=True,
    )

    assert board["mix"]["busy"] is True
    assert board["rolling"] is True


def test_monitor_hides_unmeasured_denoise_placeholders(monkeypatch) -> None:
    from diony_contracts.fixture import demo_project
    import diony_producer.run as run_module

    project = demo_project()
    current = project.shots[1].id
    monkeypatch.setattr(
        run_module,
        "take_has_live_negative",
        lambda take, _project: take == project.takes[0],
    )
    monkeypatch.setattr(run_module, "gpu_monitor", lambda: {"heartbeat": "", "journal": [], "log": ""})

    board = run_module.run_payload(
        project,
        farm={
            "busy": True,
            "live": True,
            "done": 0,
            "total": len(project.shots),
            "detail": f"Denoise 0/40 · ?it/s · ? left · {current}",
            "shot_id": current,
            "progress": {"step": 0, "total": 40, "pct": 0, "rate": "?it/s", "eta": "?"},
        },
        finish={"busy": False, "live": True, "detail": ""},
        live=True,
    )

    assert "?" not in board["now"]
    assert board["now"].startswith("Denoise starting")
    assert "?" not in board["clips"][1]["note"]
    assert board["monitoring"]["signal"]["detail"] == "Denoise is starting"
