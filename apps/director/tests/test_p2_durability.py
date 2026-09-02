"""P2 durability and finish: state survives a restart, orphans get reaped,
drafts are editable, the board feeds the director, and films ship with
subtitles, an EDL, and a QC packet."""

import hashlib
import importlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from diony_director import app as app_module
from diony_director import session
from diony_director.providers import base, flow, grok

HAVE_FFMPEG = shutil.which("ffmpeg") is not None


def _client() -> TestClient:
    importlib.reload(app_module)
    return TestClient(app_module.app, raise_server_exceptions=False)


def _real_clip(path: Path, duration: int) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc2=size=320x180:duration={duration}",
            "-f", "lavfi", "-i", f"sine=frequency=330:duration={duration}",
            "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(path),
        ],
        check=True, capture_output=True,
    )


def _fake_adapters(monkeypatch):
    counter = {"n": 0}

    def make_submit(provider_id):
        def _submit(op):
            counter["n"] += 1
            return base.CloudJobHandle(
                provider_id=provider_id, external_id=f"{provider_id}-{counter['n']}",
                model=op.model, duration_s=int(op.duration_s),
            )
        return _submit

    def _poll(handle):
        return base.CloudJobStatus(
            state="done", result_url=f"https://cdn/{handle.external_id}.mp4",
            metadata={"duration_s": handle.duration_s, "media_generation_id": f"user:{handle.external_id}"},
        )

    def make_submit_image(provider_id):
        def _submit_image(prompt, *, model, references=None, aspect_ratio="landscape", resolution=""):
            counter["n"] += 1
            return base.CloudJobHandle(
                provider_id=provider_id, external_id=f"{provider_id}-img-{counter['n']}",
                model=model, duration_s=0, metadata={"kind": "image"},
            )
        return _submit_image

    def _fetch(handle, status, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        if str(dest).endswith(".jpg"):
            dest.write_bytes(b"jpgdata" * 512)
        elif HAVE_FFMPEG:
            _real_clip(dest, max(1, handle.duration_s))
        else:
            dest.write_bytes(b"clip" * 1024)
        payload = dest.read_bytes()
        clip = base.FetchedClip(
            path=dest, sha256=hashlib.sha256(payload).hexdigest(), bytes=len(payload),
            media_url=status.result_url,
        )
        if handle.provider_id == "google-flow":
            clip.metadata["credits_used"] = 12
        return clip

    for adapter, pid in ((grok, "grok"), (flow, "google-flow")):
        monkeypatch.setattr(adapter, "submit", make_submit(pid))
        monkeypatch.setattr(adapter, "submit_image", make_submit_image(pid))
        monkeypatch.setattr(adapter, "poll", _poll)
        monkeypatch.setattr(adapter, "fetch", _fetch)


def _drive(client, sid, max_ticks=60):
    import time

    last = None
    for _ in range(max_ticks):
        last = client.post(f"/api/director/sessions/{sid}/advance").json()
        if (last["pending"] + last.get("queued", 0)) == 0 and not last.get("submitting"):
            return last
        time.sleep(0.05)
    raise AssertionError(f"batch never settled: {last}")


def _locked(client) -> tuple[str, dict]:
    sid = client.post("/api/director/sessions").json()["session_id"]
    client.post(
        f"/api/director/sessions/{sid}/produce",
        json={"idea": "a getaway driver decides to leave town from a neon diner"},
    )
    return sid, client.post(f"/api/director/sessions/{sid}/lock_script").json()


# --- A6: persistence and rehydration ----------------------------------------


def test_session_state_survives_a_restart(tmp_path) -> None:
    client = _client()
    sid, locked = _locked(client)
    op = session.get_op(locked["ops"][0]["id"])
    op.status = "submitted"
    take = tmp_path / "take.mp4"
    take.write_bytes(b"printed")
    rt = session.runtime(op.id)
    rt["handle"] = base.CloudJobHandle(
        provider_id="grok", external_id="req-77", model=op.model, duration_s=op.duration_s
    )
    rt["clip"] = base.FetchedClip(path=take, sha256="x", bytes=7)
    session.mark_accepted(op.id)
    from diony_contracts.production import ClipReview

    session.register_review(
        op.id,
        ClipReview(id="r-p2", clip_id=op.clip_id, op_id=op.id, verdict="warn",
                   families={"identity": "unmeasured"}, issues=["identity unmeasured"]),
    )
    session.persist(sid)
    package_clip_count = len(session.package_for(sid).clips)

    session.reset()  # the process dies
    assert session.package_for(sid) is None
    restored = session.rehydrate_from_vault()
    assert restored >= 1

    revived = session.get_op(op.id)
    assert revived.status == "submitted"
    assert session.package_for(sid) is not None
    assert len(session.package_for(sid).clips) == package_clip_count
    handle = session.runtime(op.id)["handle"]
    assert handle.external_id == "req-77"
    assert session.runtime(op.id)["clip"].path == take
    assert session.is_accepted(op.id)
    review = session.review_for(op.id)
    assert review is not None and review.verdict == "warn"
    assert session.sequence_for(sid) is not None
    assert session.batch_targets(sid) is not None


def test_a_mid_submit_claim_is_released_on_rehydrate() -> None:
    client = _client()
    sid, locked = _locked(client)
    op = session.get_op(locked["ops"][0]["id"])
    op.status = "submitting"  # the submit thread died with the process
    session.persist(sid)
    session.reset()
    session.rehydrate_from_vault()
    assert session.get_op(op.id).status == "proposed", "a stale claim never wedges an op"


def test_restart_mid_batch_resumes_polls_and_finishes(monkeypatch) -> None:
    client = _client()
    _fake_adapters(monkeypatch)
    sid, locked = _locked(client)
    chosen = locked["ops"][0]["id"]
    run = client.post(f"/api/director/ops/{chosen}/run")
    assert run.status_code == 200, run.text
    assert session.get_op(chosen).status == "submitted"

    # The process dies between submit and the first poll.
    session.reset()
    app_module._STATE.clear()

    state = client.get(f"/api/director/ops/{chosen}").json()
    assert state["op"]["status"] == "completed", "a rehydrated op resumes its own polling"
    assert state["clip"] is not None


# --- The reaper --------------------------------------------------------------


def test_orphaned_running_jobs_are_reaped_at_boot() -> None:
    client = _client()
    plane, ctx = app_module._boot()
    from diony_contracts.launch import (
        GenerationJob,
        JobStatus,
        ProviderCapability,
        WorkflowStage,
    )
    from diony_director import ledger

    run = ledger.ensure_run(plane, ctx.workspace.id, ctx.user.id, "ghost-session")
    job = plane.save_job(
        GenerationJob(
            id="job-ghost-1", run_id=run.id, workspace_id=ctx.workspace.id,
            project_id="director", stage=WorkflowStage.VIDEO,
            capability=ProviderCapability.VIDEO, provider_id="grok",
            model="grok-imagine-video-1.5", status=JobStatus.RUNNING,
            idempotency_key="op-ghost", external_job_id="req-ghost",
            metadata={"op_id": "op-ghost"},
        )
    )
    reaped = app_module._reap_orphaned_jobs(plane, ctx)
    assert reaped == 1
    closed = plane.job(ctx.workspace.id, job.id)
    assert closed.status is JobStatus.FAILED
    assert "reaped" in closed.error_message


# --- The explicit Director dry-run flag --------------------------------------


def test_director_dry_run_refuses_to_spend(monkeypatch) -> None:
    client = _client()
    sid, locked = _locked(client)
    monkeypatch.setenv("DIONY_DIRECTOR_DRY_RUN", "1")
    res = client.post(f"/api/director/ops/{locked['ops'][0]['id']}/run")
    assert res.status_code == 409
    assert "DIONY_DIRECTOR_DRY_RUN" in res.json()["detail"]
    assert session.get_op(locked["ops"][0]["id"]).status == "proposed"


# --- Script editing and the board --------------------------------------------


def test_draft_lines_are_editable_until_the_lock() -> None:
    client = _client()
    sid = client.post("/api/director/sessions").json()["session_id"]
    client.post(
        f"/api/director/sessions/{sid}/produce",
        json={"idea": "a getaway driver decides to leave town from a neon diner"},
    )
    draft = client.get(f"/api/director/sessions/{sid}/draft").json()
    dialogue = next(l for l in draft["script"]["lines"] if l["kind"] == "dialogue")
    heading = next(l for l in draft["script"]["lines"] if l["kind"] == "heading")

    res = client.post(
        f"/api/director/sessions/{sid}/draft/line",
        json={"line_id": dialogue["line_id"], "text": "We drive at dawn, not a minute later."},
    )
    assert res.status_code == 200
    edited = next(
        l for l in res.json()["script"]["lines"] if l["line_id"] == dialogue["line_id"]
    )
    assert edited["text"] == "We drive at dawn, not a minute later."

    res = client.post(
        f"/api/director/sessions/{sid}/draft/line",
        json={"line_id": heading["line_id"], "text": "INT. NOWHERE - NEVER"},
    )
    assert res.status_code == 422, "structure lines stay fixed"

    client.post(f"/api/director/sessions/{sid}/lock_script")
    res = client.post(
        f"/api/director/sessions/{sid}/draft/line",
        json={"line_id": dialogue["line_id"], "text": "too late"},
    )
    assert res.status_code == 409, "nothing changes after the lock"


def test_the_board_feeds_script_bibles_and_shots() -> None:
    client = _client()
    sid, locked = _locked(client)
    board = client.get(f"/api/director/sessions/{sid}/board").json()
    assert board["locked"] is True
    assert board["clips"], "the shot board fills at lock"
    first = board["clips"][0]
    assert first["camera"] and first["join_next"] in {"bridge", "reaction_cut", "extend", "cut"}
    assert board["characters"] and all(c["wardrobe"] for c in board["characters"])
    assert board["settings"] and all("plate_ready" in s for s in board["settings"])
    assert board["film_ready"] is False


# --- The finish lane: deliverables --------------------------------------------


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not installed")
def test_the_film_ships_with_subtitles_edl_and_qc_packet(monkeypatch) -> None:
    from diony_contracts.cluster import cluster_root

    client = _client()
    _fake_adapters(monkeypatch)
    sid, locked = _locked(client)
    client.post(f"/api/director/sessions/{sid}/run_batch", json={})
    _drive(client, sid)
    film = client.post(f"/api/director/sessions/{sid}/assemble", json={}).json()
    assert set(film["deliverables"]) == {
        "film.srt", "film.vtt", "edl.json", "assembly-map.json", "qc-packet.json"
    }
    assert film["cue_count"] >= 1
    root = cluster_root() / "director" / sid

    srt = (root / "film.srt").read_text()
    package = session.package_for(sid)
    spoken = next(
        c.audio_packet.script_excerpt[0]["line"]
        for c in package.clips
        if c.clip_class == "sync-dialogue"
    )
    assert spoken in srt, "subtitles carry the authored words at their cut times"

    edl = json.loads((root / "edl.json").read_text())
    assert len(edl["events"]) == film["clip_count"]
    assert all("record_in_s" in e and "join_next" in e for e in edl["events"])

    qc = json.loads((root / "qc-packet.json").read_text())
    assert qc["clips"], "every reviewed clip is in the packet"
    assert qc["master"]["verdict"] in {"pass", "warn"}

    amap = json.loads((root / "assembly-map.json").read_text())
    assert len(amap["events"]) == film["clip_count"]

    served = client.get(f"/api/director/sessions/{sid}/deliverables/film.srt")
    assert served.status_code == 200
    assert client.get(f"/api/director/sessions/{sid}/deliverables/evil.txt").status_code == 404


# --- Housekeeping ------------------------------------------------------------


def test_engine_is_cloud_has_one_shared_definition(monkeypatch) -> None:
    from diony_contracts.cluster import engine_is_cloud

    monkeypatch.delenv("DIONY_ENGINE", raising=False)
    assert engine_is_cloud() is True
    monkeypatch.setenv("DIONY_ENGINE", "local_legacy")
    assert engine_is_cloud() is False
    from diony_director import engine as director_engine
    from diony_contracts.director import EngineMode

    assert director_engine.engine_mode() is EngineMode.LOCAL_LEGACY


def test_v2v_edits_never_send_a_duration(monkeypatch) -> None:
    """useapi rejects the duration parameter on v2v — output length comes from
    the source's trim window, not a booking."""
    from diony_contracts.director import (
        CloudGenerationMode,
        CloudProvider,
        CostEstimate,
        CostEstimateLine,
        DirectorOp,
        DirectorOpType,
    )

    seen = {}

    class _Response:
        status_code = 200

        def json(self):
            return {"jobId": "j-v2v"}

    monkeypatch.setattr(flow.httpx, "post", lambda url, **kw: seen.update(kw) or _Response())
    op = DirectorOp(
        id="op-v2v", session_id="s-v", op=DirectorOpType.EDIT,
        provider=CloudProvider.GOOGLE_FLOW, model="omni-flash",
        mode=CloudGenerationMode.V2V_EDIT, prompt="fix the audio, keep the picture",
        duration_s=6, resolution="720p",
        artifact_refs={"source_video": "user:vid9"},
        estimate=CostEstimate(
            id="est-v", workspace_id="local-workspace",
            lines=[CostEstimateLine(
                label="v2v", provider_id="google-flow", model="omni-flash",
                unit="v2v:720p", unit_rate_usd_micros=1,
                quantity_microunits=1_000_000, usd_micros=1,
            )],
        ),
    )
    flow.submit(op)
    assert "duration" not in seen["json"], "v2v output length comes from the trim window"
    assert seen["json"]["referenceVideo_1"] == "user:vid9"
    assert seen["json"]["resolution"] == "720p"


# --- Final-review hardening ---------------------------------------------------


def test_a_second_lock_is_refused_not_duplicated() -> None:
    """Locking twice used to re-split the whole script into a duplicate set of
    priced ops — and under autopilot, pay for the film twice."""
    client = _client()
    sid, locked = _locked(client)
    op_count = len(session.ops_for(sid))
    res = client.post(f"/api/director/sessions/{sid}/lock_script")
    assert res.status_code == 409
    assert "already locked" in res.json()["detail"]
    assert len(session.ops_for(sid)) == op_count, "no duplicate ops"


def test_a_broken_draft_is_refused_before_the_lock(monkeypatch) -> None:
    """A brain that names a setting by display name instead of id must produce
    a named 422 — and leave the draft editable and re-lockable, never bricked."""
    import json as _json

    from diony_director import screenwriter

    monkeypatch.setenv("DIONY_DIRECTOR_BRAIN_URL", "http://127.0.0.1:9999")
    body = {
        "logline": "A keeper answers.",
        "beats": [{"beat": "b", "story_time": "Night 1"}],
        "settings": [{"setting_id": "set-lh", "name": "Lighthouse", "kind": "INT",
                      "room_tone": "wind", "time_weather": "night", "grade_language": "brass",
                      "camera_kit": "32mm"}],
        "characters": [{"char_id": "chr-el", "name": "ELIAS", "wardrobe": "grey peacoat"}],
        "scenes": [{
            "heading": "INT. LIGHTHOUSE - NIGHT",
            "setting_ref": "Lighthouse",  # the classic LLM slip: name, not id
            "story_time": "Night 1", "cast": ["chr-el"],
            "lines": [{"kind": "dialogue", "text": "I know that knock.", "speaker": "ELIAS"}],
        }],
    }

    from master_brain_fakes import is_master_call, master_brain_body

    class _Response:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            reply = master_brain_body() if is_master_call(self._payload) else body
            return {"choices": [{"message": {"content": _json.dumps(reply)}}]}

    monkeypatch.setattr(screenwriter.httpx, "post", lambda *a, **k: _Response(k.get("json")))
    client = _client()
    sid = client.post("/api/director/sessions").json()["session_id"]
    client.post(f"/api/director/sessions/{sid}/produce", json={"idea": "a keeper"})
    res = client.post(f"/api/director/sessions/{sid}/lock_script")
    assert res.status_code == 422
    assert "sc-1" in res.json()["detail"] and "Lighthouse" in res.json()["detail"]
    # the session is NOT bricked: the draft is still editable
    draft = client.get(f"/api/director/sessions/{sid}/draft").json()
    assert draft["script"]["locked"] is False
    line = next(l for l in draft["script"]["lines"] if l["kind"] == "dialogue")
    edit = client.post(
        f"/api/director/sessions/{sid}/draft/line",
        json={"line_id": line["line_id"], "text": "Still mine to rewrite."},
    )
    assert edit.status_code == 200


def test_provider_swap_requantizes_to_the_new_grid() -> None:
    """A Grok 6s clip swapped to Veo must book a legal Veo duration (8s on a
    non-Ultra plan) — the old swap emitted an op no provider would accept."""
    from diony_contracts.production import ClipReview
    from diony_director import capabilities, packagebuilder, revisions, screenwriter
    from diony_director.compiler import compile_clip_prompt

    draft = screenwriter.write_script("a chase through a desert town", runtime_target_s=60)
    package = packagebuilder.build_package("s-swap", "a chase", draft)
    clip = next(
        c for c in package.clips
        if c.provider.value == "grok" and not c.audio_packet.voice_ref
    )
    clip.add_prompt(compile_clip_prompt(clip, package))
    review = ClipReview(
        id="r-sw", clip_id=clip.clip_id, verdict="fail",
        families={"delivery": "fail"}, issues=["empty media"],
    )
    first, _ = revisions.plan_revision(
        review, clip, package, session_id="s-swap", revision_count=0, failure_memory=set()
    )
    memory = {revisions.failure_key(first)}
    swapped, _ = revisions.plan_revision(
        review, clip, package, session_id="s-swap", revision_count=1, failure_memory=memory
    )
    assert swapped is not None
    assert swapped.provider.value == "google-flow"
    assert swapped.duration_s == 8, "re-quantized to the non-Ultra Veo grid"
    assert capabilities.violations(swapped) == []


def test_extend_revisions_pass_the_capability_gate() -> None:
    """The gate used to count reference images an extension never transmits —
    killing the whole extend rung once character sheets existed."""
    from diony_contracts.production import ClipReview
    from diony_director import capabilities, packagebuilder, revisions, screenwriter
    from diony_director.compiler import compile_clip_prompt

    draft = screenwriter.write_script("a chase through a desert town", runtime_target_s=60)
    package = packagebuilder.build_package("s-ext", "a chase", draft)
    clip = next(c for c in package.clips if c.provider.value == "grok")
    clip.add_prompt(compile_clip_prompt(clip, package))
    review = ClipReview(
        id="r-ex", clip_id=clip.clip_id, verdict="fail",
        families={"duration": "fail"}, issues=["measured 2.0s vs booked 6s"],
    )
    op, _ = revisions.plan_revision(
        review, clip, package, session_id="s-ext", revision_count=0, failure_memory=set()
    )
    assert op is not None and op.op.value == "extend"
    assert "references" not in op.artifact_refs
    assert capabilities.violations(op) == []


def test_a_held_bible_op_still_gates_its_scenes() -> None:
    """A budget-held plate leaves the batch targets — but its scenes must stay
    gated exactly like a failed plate's; clips never roll unplated."""
    client = _client()
    sid, locked = _locked(client)
    all_ids = {o["id"] for o in locked["ops"]} | {b["id"] for b in locked["bible_ops"]}
    session.set_batch_targets(sid, all_ids)
    # simulate the budget hold: a plate op dropped from targets, still proposed
    plate = next(
        b for b in locked["bible_ops"] if b["artifact_refs"]["bible"].get("setting_id")
    )
    session.batch_targets(sid).discard(plate["id"])
    for b in locked["bible_ops"]:
        if b["id"] != plate["id"]:
            session.get_op(b["id"]).status = "completed"
    ready = session.ready_ops(sid)
    clip_ids = {o["id"] for o in locked["ops"]}
    assert not any(op.id in clip_ids for op in ready), "no clip rolls while a plate is held"


def test_watchdog_surfaces_a_job_stuck_without_progress() -> None:
    """A job that polls "running" forever looks healthy to every tick — only
    the timestamp of its last state change exposes it. Half an hour without
    progress lands it in the advance report's stalled list, where the room
    already narrates it."""
    import time

    from test_continuity_music import _gen_op

    client = TestClient(app_module.app)
    sid = client.post("/api/director/sessions").json()["session_id"]
    op = _gen_op("op-stuck", sid, "cl-stuck", status="submitted")
    session._OPS[op.id] = op
    session._SESSIONS[sid].op_ids.append(op.id)
    rt = session.runtime(op.id)
    rt["last_poll_state"] = "running"
    rt["last_progress_ts"] = time.time() - app_module.STALL_WATCHDOG_S - 120

    counts = client.post(f"/api/director/sessions/{sid}/advance").json()
    assert any(
        s["op_id"] == op.id and "may be lost" in s["reason"] for s in counts["stalled"]
    )
    assert counts["pending"] == 1, "surfaced, never auto-failed — the job may still land"

    rt["last_progress_ts"] = time.time()  # fresh progress → the watchdog is quiet
    counts = client.post(f"/api/director/sessions/{sid}/advance").json()
    assert counts["stalled"] == []


def test_watchdog_starts_a_clock_for_a_pre_watchdog_submitted_op() -> None:
    """An op submitted before the watchdog landed (rehydrated old state) has
    no last_progress_ts. It must be stamped at first sighting and counted
    pending — never treated as healthy forever."""
    import time

    from test_continuity_music import _gen_op

    client = TestClient(app_module.app)
    sid = client.post("/api/director/sessions").json()["session_id"]
    op = _gen_op("op-nots", sid, "cl-nots", status="submitted")
    session._OPS[op.id] = op
    session._SESSIONS[sid].op_ids.append(op.id)
    session.runtime(op.id).clear()  # no last_progress_ts at all

    counts = client.post(f"/api/director/sessions/{sid}/advance").json()
    assert counts["stalled"] == [], "a freshly-stamped clock is not yet stalled"
    assert counts["pending"] == 1
    assert session.runtime(op.id).get("last_progress_ts") is not None, "the clock started"
    # push the stamped clock past the window → it now surfaces
    session.runtime(op.id)["last_progress_ts"] = time.time() - app_module.STALL_WATCHDOG_S - 60
    counts = client.post(f"/api/director/sessions/{sid}/advance").json()
    assert any(s["op_id"] == op.id for s in counts["stalled"])
