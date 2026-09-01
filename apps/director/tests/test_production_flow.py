"""End-to-end scripted production: produce → lock → generate → review → revise → assemble → receipt."""

import hashlib
import importlib
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from diony_director import app as app_module
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


def _fake_adapters(monkeypatch, tmp_path: Path, *, fail_external: set[str] | None = None):
    """Both adapters succeed instantly, fetching a real clip sized to the booking."""

    counter = {"n": 0}
    fail_external = fail_external or set()

    def make_submit(provider_id):
        def _submit(op):
            counter["n"] += 1
            return base.CloudJobHandle(
                provider_id=provider_id,
                external_id=f"{provider_id}-{counter['n']}",
                model=op.model,
                duration_s=int(op.duration_s),
            )

        return _submit

    def _poll(handle):
        if handle.external_id in fail_external:
            return base.CloudJobStatus(state="failed", detail="provider exploded")
        return base.CloudJobStatus(
            state="done",
            result_url=f"https://cdn/{handle.external_id}.mp4",
            metadata={"duration_s": handle.duration_s, "media_generation_id": f"user:{handle.external_id}"},
        )

    def make_submit_image(provider_id):
        def _submit_image(prompt, *, model, references=None, aspect_ratio="landscape", resolution=""):
            counter["n"] += 1
            return base.CloudJobHandle(
                provider_id=provider_id,
                external_id=f"{provider_id}-img-{counter['n']}",
                model=model,
                duration_s=0,
                metadata={"kind": "image"},
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
            path=dest,
            sha256=hashlib.sha256(payload).hexdigest(),
            bytes=len(payload),
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
        time.sleep(0.05)  # let the submitter worker take its wave
    raise AssertionError(f"batch never settled: {last}")


def _start(client) -> str:
    return client.post("/api/director/sessions").json()["session_id"]


def test_produce_drafts_a_script_without_spending() -> None:
    client = _client()
    sid = _start(client)
    body = client.post(
        f"/api/director/sessions/{sid}/produce",
        json={"idea": "a getaway driver decides to leave town from a neon diner"},
    ).json()
    assert body["script"]["scenes"]
    assert body["warnings"]
    ledger = client.get("/api/director/ledger").json()
    assert ledger["totals"]["cost_usd_micros"] == 0


def test_lock_script_splits_prices_and_proposes_ops() -> None:
    client = _client()
    sid = _start(client)
    client.post(
        f"/api/director/sessions/{sid}/produce",
        json={"idea": "a getaway driver decides to leave town from a neon diner"},
    )
    body = client.post(f"/api/director/sessions/{sid}/lock_script").json()
    assert body["package"]["script"]["locked"] is True
    ops = body["ops"]
    assert ops and all(op["estimate"]["total_usd_micros"] > 0 for op in ops)
    project = body["package"]["estimate"]
    assert project["revision_allowance_usd_micros"] > 0
    assert "revision allowance" in body["reply"]
    assert "clip(s)" not in body["reply"]


def test_autopilot_needs_a_budget_to_arm() -> None:
    client = _client()
    sid = _start(client)
    res = client.post(
        f"/api/director/sessions/{sid}/produce",
        json={"idea": "a chase", "autopilot": True},
    )
    assert res.status_code == 409
    assert "budget" in res.json()["detail"]


def test_autopilot_submits_within_budget_and_holds_at_the_ceiling(monkeypatch, tmp_path) -> None:
    client = _client()
    _fake_adapters(monkeypatch, tmp_path)
    sid = _start(client)
    client.post(
        f"/api/director/sessions/{sid}/produce",
        json={"idea": "a getaway driver leaves a neon diner", "autopilot": True, "max_usd": 1.0},
    )
    body = client.post(f"/api/director/sessions/{sid}/lock_script").json()
    assert body["targeted"], "autopilot should target the batch inside the budget"
    held = list(body["held"])
    import time as _time

    for _ in range(60):
        tick = client.post(f"/api/director/sessions/{sid}/advance").json()
        held += tick.get("held", [])
        if (tick["pending"] + tick.get("queued", 0)) == 0 and not tick.get("submitting"):
            break
        _time.sleep(0.05)
    held += client.post(f"/api/director/sessions/{sid}/advance").json().get("held", [])
    assert held, "a $1 ceiling cannot cover the whole plan — some ops must hold"
    assert any("budget ceiling" in h["reason"] for h in held)


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not installed")
def test_full_loop_generate_review_assemble_receipt(monkeypatch, tmp_path) -> None:
    client = _client()
    _fake_adapters(monkeypatch, tmp_path)
    sid = _start(client)
    client.post(
        f"/api/director/sessions/{sid}/produce",
        json={"idea": "a getaway driver decides to leave town from a neon diner"},
    )
    ops = client.post(f"/api/director/sessions/{sid}/lock_script").json()["ops"]

    for op in ops[:3]:
        run = client.post(f"/api/director/ops/{op['id']}/run")
        assert run.status_code == 200, run.text
        state = client.get(f"/api/director/ops/{op['id']}").json()
        assert state["op"]["status"] == "completed"
        assert state["review"] is not None
        assert state["review"]["verdict"] in {"pass", "warn"}
        assert state["review"]["families"]["duration"] == "pass"

    # only 3 of the scripted clips exist: the default cut refuses the gaps…
    refused = client.post(f"/api/director/sessions/{sid}/assemble", json={})
    assert refused.status_code == 409
    assert "missing shots" in refused.json()["detail"]
    # …and an explicit allow_gaps ships what exists
    film = client.post(
        f"/api/director/sessions/{sid}/assemble", json={"allow_gaps": True}
    ).json()
    assert film["clip_count"] == 3
    assert film["duration_s"] > 3

    receipt = client.get(f"/api/director/sessions/{sid}/receipt").json()
    completed_lines = [l for l in receipt["lines"] if l["status"] == "completed"]
    assert len(completed_lines) == 3
    assert receipt["totals"]["actual_usd_micros"] > 0
    assert all(l["actual_usd_micros"] > 0 for l in completed_lines)


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not installed")
def test_retry_missing_shots_fills_the_gaps_and_cuts_clean(monkeypatch, tmp_path) -> None:
    """The functional 'Retry' beside 'Assemble anyway': run_retakes rolls the
    drafted ops for every missing shot, and the default cut then succeeds."""
    client = _client()
    _fake_adapters(monkeypatch, tmp_path)
    sid = _start(client)
    client.post(
        f"/api/director/sessions/{sid}/produce",
        json={"idea": "a getaway driver decides to leave town from a neon diner"},
    )
    ops = client.post(f"/api/director/sessions/{sid}/lock_script").json()["ops"]

    for op in ops[:3]:
        assert client.post(f"/api/director/ops/{op['id']}/run").status_code == 200
        state = client.get(f"/api/director/ops/{op['id']}").json()
        assert state["op"]["status"] == "completed"

    refused = client.post(f"/api/director/sessions/{sid}/assemble", json={})
    assert refused.status_code == 409

    res = client.post(f"/api/director/sessions/{sid}/run_retakes").json()
    assert res["missing"], "the unrun clips are the gaps"
    assert res["targeted"], "each gap has a drafted op to roll"
    assert res["undrafted"] == []
    assert res["rolling"] == []

    _drive(client, sid)
    film = client.post(f"/api/director/sessions/{sid}/assemble", json={}).json()
    assert film["clip_count"] == len(ops)


def test_a_failed_job_drafts_the_next_revision(monkeypatch, tmp_path) -> None:
    client = _client()
    _fake_adapters(monkeypatch, tmp_path, fail_external={"grok-1"})
    sid = _start(client)
    client.post(
        f"/api/director/sessions/{sid}/produce",
        json={"idea": "a getaway driver decides to leave town from a neon diner"},
    )
    ops = client.post(f"/api/director/sessions/{sid}/lock_script").json()["ops"]
    grok_op = next(o for o in ops if o["provider"] == "grok")
    client.post(f"/api/director/ops/{grok_op['id']}/run")
    state = client.get(f"/api/director/ops/{grok_op['id']}").json()
    assert state["op"]["status"] == "failed"
    assert state["next_op"] is not None
    assert state["next_op"]["prompt_version"].startswith("v2-")
    assert "This pass adds about $" in state["narration"]


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not installed")
def test_upscale_proposal_on_a_completed_flow_clip(monkeypatch, tmp_path) -> None:
    client = _client()
    _fake_adapters(monkeypatch, tmp_path)
    sid = _start(client)
    client.post(
        f"/api/director/sessions/{sid}/produce",
        json={"idea": "a getaway driver decides to leave town from a neon diner"},
    )
    ops = client.post(f"/api/director/sessions/{sid}/lock_script").json()["ops"]
    flow_op = next(o for o in ops if o["provider"] == "google-flow")
    client.post(f"/api/director/ops/{flow_op['id']}/run")
    client.get(f"/api/director/ops/{flow_op['id']}")

    proposal = client.post(f"/api/director/ops/{flow_op['id']}/upscale")
    assert proposal.status_code == 200, proposal.text
    body = proposal.json()
    assert body["op"]["op"] == "upscale"
    assert body["op"]["estimate"]["total_usd_micros"] == 20 * 19_990
    assert "$" in body["reply"]


def test_upscale_refuses_an_incomplete_op(monkeypatch, tmp_path) -> None:
    client = _client()
    sid = _start(client)
    proposal = client.post(
        f"/api/director/sessions/{sid}/message", json={"text": "a truck", "duration_s": 6}
    ).json()
    res = client.post(f"/api/director/ops/{proposal['op']['id']}/upscale")
    assert res.status_code == 409


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not installed")
def test_run_batch_advance_and_assemble_all(monkeypatch, tmp_path) -> None:
    client = _client()
    _fake_adapters(monkeypatch, tmp_path)
    sid = _start(client)
    client.post(
        f"/api/director/sessions/{sid}/produce",
        json={"idea": "a getaway driver decides to leave town from a neon diner"},
    )
    locked = client.post(f"/api/director/sessions/{sid}/lock_script").json()
    ops = locked["ops"]
    bible = locked["bible_ops"]
    assert bible, "locking must plan plates and character sheets"

    batch = client.post(f"/api/director/sessions/{sid}/run_batch", json={}).json()
    # async directed contract: the request returns instantly; the submitter
    # worker rolls bible stills first, then chains scene clips wave by wave.
    assert batch["queued"] > 0
    assert len(batch["targeted"]) == len(ops) + len(bible)
    assert batch["held"] == []

    tick = _drive(client, sid)
    assert tick["completed"] == len(ops) + len(bible)
    assert tick["failed"] == 0

    film = client.post(f"/api/director/sessions/{sid}/assemble", json={}).json()
    assert film["clip_count"] == len(ops)


def test_run_batch_selected_subset_only(monkeypatch, tmp_path) -> None:
    client = _client()
    _fake_adapters(monkeypatch, tmp_path)
    sid = _start(client)
    client.post(
        f"/api/director/sessions/{sid}/produce",
        json={"idea": "a getaway driver decides to leave town from a neon diner"},
    )
    ops = client.post(f"/api/director/sessions/{sid}/lock_script").json()["ops"]
    chosen = [ops[0]["id"], ops[2]["id"]]
    client.post(f"/api/director/sessions/{sid}/run_batch", json={"op_ids": chosen}).json()
    _drive(client, sid)
    states = {o["id"]: o["status"] for o in
              [client.get(f"/api/director/ops/{o['id']}").json()["op"] for o in ops]}
    assert all(states[i] == "completed" for i in chosen)
    untouched = [o["id"] for o in ops if o["id"] not in chosen]
    assert all(states[i] == "proposed" for i in untouched)


def test_run_batch_reports_holds_instead_of_dying(monkeypatch, tmp_path) -> None:
    client = _client()
    _fake_adapters(monkeypatch, tmp_path)
    sid = _start(client)
    client.post(
        f"/api/director/sessions/{sid}/produce",
        json={"idea": "a getaway driver decides to leave town from a neon diner"},
    )
    ops = client.post(f"/api/director/sessions/{sid}/lock_script").json()["ops"]

    def boom(op):
        raise __import__("diony_director.providers.base", fromlist=["CloudAdapterError"]).CloudAdapterError("provider down")

    monkeypatch.setattr(grok, "submit", boom)
    batch = client.post(f"/api/director/sessions/{sid}/run_batch", json={}).json()
    held = list(batch["held"])
    import time as _time

    for _ in range(60):
        tick = client.post(f"/api/director/sessions/{sid}/advance").json()
        held += tick.get("held", [])
        if (tick["pending"] + tick.get("queued", 0)) == 0 and not tick.get("submitting"):
            break
        _time.sleep(0.05)
    held += client.post(f"/api/director/sessions/{sid}/advance").json().get("held", [])
    grok_ops = {o["id"] for o in ops if o["provider"] == "grok"}
    held_ids = {h["op_id"] for h in held}
    assert grok_ops <= held_ids, "every grok clip must hold with its reason"
    assert all("provider down" in h["reason"] for h in held if h["op_id"] in grok_ops)


def test_run_batch_survives_raw_http_errors_and_names_them(monkeypatch, tmp_path) -> None:
    client = _client()
    _fake_adapters(monkeypatch, tmp_path)
    sid = _start(client)
    client.post(
        f"/api/director/sessions/{sid}/produce",
        json={"idea": "a getaway driver decides to leave town from a neon diner"},
    )
    client.post(f"/api/director/sessions/{sid}/lock_script").json()

    import httpx as _httpx

    def reject(op):
        raise _httpx.HTTPStatusError("Client error '400 Bad Request'", request=None, response=None)

    monkeypatch.setattr(grok, "submit", reject)
    res = client.post(f"/api/director/sessions/{sid}/run_batch", json={})
    assert res.status_code == 200, "one bad op must never 500 the batch"
    held = list(res.json()["held"])
    import time as _time

    for _ in range(60):
        tick = client.post(f"/api/director/sessions/{sid}/advance").json()
        held += tick.get("held", [])
        if (tick["pending"] + tick.get("queued", 0)) == 0 and not tick.get("submitting"):
            break
        _time.sleep(0.05)
    held += client.post(f"/api/director/sessions/{sid}/advance").json().get("held", [])
    assert held
    assert all("400" in h["reason"] for h in held)


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not installed")
def test_directed_batch_chains_bridge_frames_and_plates(monkeypatch, tmp_path) -> None:
    """The continuity doctrine, wired: plates open scenes, last frames chain clips."""
    client = _client()
    _fake_adapters(monkeypatch, tmp_path)

    seen_refs = {}
    from diony_director import app as _app

    original = _app._dispatch_submit

    def spy(op):
        seen_refs[op.id] = dict(op.artifact_refs) if op.artifact_refs else {}
        handle = original(op)
        seen_refs[op.id] = dict(op.artifact_refs)  # after late binding
        return handle

    monkeypatch.setattr(_app, "_dispatch_submit", spy)

    sid = _start(client)
    client.post(
        f"/api/director/sessions/{sid}/produce",
        json={"idea": "a getaway driver decides to leave town from a neon diner"},
    )
    locked = client.post(f"/api/director/sessions/{sid}/lock_script").json()
    client.post(f"/api/director/sessions/{sid}/run_batch", json={})
    _drive(client, sid)

    from diony_director import session as _session

    seq = _session.sequence_for(sid)
    assert seq is not None
    bridged = 0
    plated = 0
    for chain in seq["scenes"].values():
        for position, op_id in enumerate(chain):
            refs = seen_refs.get(op_id, {})
            start = str(refs.get("start_image", ""))
            references = [str(r) for r in (refs.get("references") or [])]
            if position == 0:
                # a peopled opener carries the plate FIRST in its references
                # (composing the cast into the master room); an empty opener
                # may still start from the plate frame itself
                anchored = (start and "frames" not in start) or (
                    references and "frames" not in references[0]
                )
                if anchored:
                    plated += 1  # scene opener locked to its setting plate
            elif (start.endswith(".jpg") and "frames" in start) or any(
                "frames" in r for r in references
            ):
                # successor carries the previous clip's join frame — as its
                # first frame on odd links, or riding the re-anchor pack
                bridged += 1
    assert plated > 0, "scene openers must lock to setting plates"
    assert bridged > 0, "successor clips must bridge from the previous clip's last frame"


def test_grok_image_ops_reconcile_on_image_rates(monkeypatch, tmp_path) -> None:
    """The live wedge: a grok-lane bible still must reconcile as an image
    generation, not as video seconds (RateCardMissing spun forever)."""
    monkeypatch.delenv("USEAPI_TOKEN", raising=False)  # grok-only: sheets route grok
    client = _client()
    _fake_adapters(monkeypatch, tmp_path)
    sid = _start(client)
    client.post(
        f"/api/director/sessions/{sid}/produce",
        json={"idea": "a getaway driver decides to leave town from a neon diner"},
    )
    locked = client.post(f"/api/director/sessions/{sid}/lock_script").json()
    bible_ids = [b["id"] for b in locked["bible_ops"]]
    assert bible_ids
    client.post(f"/api/director/sessions/{sid}/run_batch", json={"op_ids": []})
    last = _drive(client, sid)
    assert last["stalled"] == []
    states = [client.get(f"/api/director/ops/{i}").json()["op"]["status"] for i in bible_ids]
    assert all(s == "completed" for s in states), states
    ledger = client.get("/api/director/ledger").json()
    assert ledger["totals"]["cost_usd_micros"] == 80_000 * len(bible_ids)  # $0.08/2K still


def test_locked_estimate_covers_clips_bible_and_allowance(monkeypatch, tmp_path) -> None:
    """The totals discrepancy fix: ONE composed estimate — clips + plates &
    sheets + allowance — and the reply itemizes exactly those parts."""
    client = _client()
    _fake_adapters(monkeypatch, tmp_path)
    sid = _start(client)
    client.post(
        f"/api/director/sessions/{sid}/produce",
        json={"idea": "a getaway driver decides to leave town from a neon diner"},
    )
    locked = client.post(f"/api/director/sessions/{sid}/lock_script").json()
    ops, bible = locked["ops"], locked["bible_ops"]
    est = locked["package"]["estimate"]
    clips_cost = sum(o["estimate"]["total_usd_micros"] for o in ops)
    bible_cost = sum(b["estimate"]["total_usd_micros"] for b in bible)
    allowance = est["revision_allowance_usd_micros"]
    assert est["total_usd_micros"] == clips_cost + bible_cost + allowance
    assert any("plates & character sheets" in line["label"] for line in est["lines"])
    reply = locked["reply"]
    assert "plate & character sheet still" in reply
    assert "revision allowance" in reply
    assert "Locked estimate" in reply
    assert "Session total becomes" not in reply  # redundant at zero session spend
    assert "clip(s)" not in reply  # real plurals, never the lazy (s)
