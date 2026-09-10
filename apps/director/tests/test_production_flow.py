"""End-to-end scripted production: produce → lock → generate → review → revise → assemble → receipt."""

import hashlib
import importlib
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from diony_director import app as app_module
from diony_director.narration import DEFAULT_ROSTER
from diony_director.providers import base, flow, grok

HAVE_FFMPEG = shutil.which("ffmpeg") is not None


def _client() -> TestClient:
    importlib.reload(app_module)
    return TestClient(app_module.app, raise_server_exceptions=False)


def _real_clip(path: Path, duration: int, size: str = "320x180") -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size={size}:duration={duration}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=330:duration={duration}",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


def _fake_adapters(
    monkeypatch,
    tmp_path: Path,
    *,
    fail_external: set[str] | None = None,
    finish_size: str = "1920x1080",
    fail_upscales: bool = False,
):
    """Both adapters succeed instantly, fetching a real clip sized to the booking.
    Flow upscales print at `finish_size` (a short finish fails its check)."""

    counter = {"n": 0}
    fail_external = fail_external or set()
    durations: dict[str, int] = {}

    def make_submit(provider_id):
        def _submit(op):
            counter["n"] += 1
            external = f"{provider_id}-{counter['n']}"
            durations[external] = int(op.duration_s)
            return base.CloudJobHandle(
                provider_id=provider_id,
                external_id=external,
                model=op.model,
                duration_s=int(op.duration_s),
            )

        return _submit

    def _submit_upscale(media_generation_id, *, resolution="1080p"):
        counter["n"] += 1
        source_external = str(media_generation_id).split("user:", 1)[-1]
        external = f"google-flow-up-{counter['n']}"
        if fail_upscales:
            fail_external.add(external)
        return base.CloudJobHandle(
            provider_id="google-flow",
            external_id=external,
            model=flow.UPSCALE_MODEL,
            resolution=resolution,
            duration_s=durations.get(source_external, 8),
            metadata={"kind": "upscale", "source_media_generation_id": str(media_generation_id)},
        )

    def _poll(handle):
        if handle.external_id in fail_external:
            return base.CloudJobStatus(state="failed", detail="provider exploded")
        return base.CloudJobStatus(
            state="done",
            result_url=f"https://cdn/{handle.external_id}.mp4",
            metadata={
                "duration_s": handle.duration_s,
                "media_generation_id": f"user:{handle.external_id}",
            },
        )

    def make_submit_image(provider_id):
        def _submit_image(
            prompt, *, model, references=None, aspect_ratio="landscape", resolution=""
        ):
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
            size = finish_size if handle.metadata.get("kind") == "upscale" else "320x180"
            _real_clip(dest, max(1, handle.duration_s), size)
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
    monkeypatch.setattr(flow, "submit_upscale", _submit_upscale)


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
def test_full_loop_generate_review_assemble_receipt(monkeypatch, tmp_path, fake_narrator) -> None:
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
    film = client.post(f"/api/director/sessions/{sid}/assemble", json={"allow_gaps": True}).json()
    assert film["clip_count"] == 3
    assert film["duration_s"] > 3

    receipt = client.get(f"/api/director/sessions/{sid}/receipt").json()
    completed_lines = [l for l in receipt["lines"] if l["status"] == "completed"]
    assert len(completed_lines) == 3
    assert receipt["totals"]["actual_usd_micros"] > 0
    assert all(l["actual_usd_micros"] > 0 for l in completed_lines)


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not installed")
def test_retry_missing_shots_fills_the_gaps_and_cuts_clean(
    monkeypatch, tmp_path, fake_narrator
) -> None:
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
    # this production rolls on Google Flow (the 10 s Omni grid, the Flow finish lane)
    from diony_contracts.cluster import write_engine_choice

    write_engine_choice("google-flow")
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
    # a 1080p finish is free on the documented plan — only the captcha solve bills
    assert body["op"]["estimate"]["total_usd_micros"] == 3000
    assert body["op"]["artifact_refs"]["source_op"] == flow_op["id"]
    assert body["op"]["duration_s"] == flow_op["duration_s"]
    assert "$" in body["reply"]
    # proposing twice never mints a second finish for the same take
    again = client.post(f"/api/director/ops/{flow_op['id']}/upscale")
    assert again.status_code == 409
    assert "already proposed" in again.text


def test_upscale_refuses_an_incomplete_op(monkeypatch, tmp_path) -> None:
    client = _client()
    sid = _start(client)
    proposal = client.post(
        f"/api/director/sessions/{sid}/message", json={"text": "a truck", "duration_s": 6}
    ).json()
    res = client.post(f"/api/director/ops/{proposal['op']['id']}/upscale")
    assert res.status_code == 409


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not installed")
def test_run_batch_advance_and_assemble_all(monkeypatch, tmp_path, fake_narrator) -> None:
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
    states = {
        o["id"]: o["status"]
        for o in [client.get(f"/api/director/ops/{o['id']}").json()["op"] for o in ops]
    }
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
        raise __import__(
            "diony_director.providers.base", fromlist=["CloudAdapterError"]
        ).CloudAdapterError("provider down")

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
    # this production rolls on Google Flow (the 10 s Omni grid, the Flow finish lane)
    from diony_contracts.cluster import write_engine_choice

    write_engine_choice("google-flow")
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
    client.post(f"/api/director/sessions/{sid}/lock_script").json()
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


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not installed")
@pytest.mark.parametrize("engine", ["grok", "google-flow"])
def test_narration_lands_on_every_studio_engine(
    engine, monkeypatch, tmp_path, fake_narrator
) -> None:
    """Blueprint 28 is engine-agnostic: whichever Studio option shot the
    clips — Grok or Google Flow — the assembled cut gets the
    same post-recorded narration: cues placed, mixed, and audited, with the
    mute picture-lock kept beside the narrated master."""
    import json

    from diony_contracts.cluster import cluster_root, write_engine_choice

    client = _client()
    _fake_adapters(monkeypatch, tmp_path)
    write_engine_choice(engine)
    sid = _start(client)
    client.post(
        f"/api/director/sessions/{sid}/produce",
        json={"idea": "a getaway driver decides to leave town from a neon diner"},
    )
    locked = client.post(f"/api/director/sessions/{sid}/lock_script").json()
    providers = {op["provider"] for op in locked["ops"]}
    if engine in {"grok", "google-flow"}:
        assert providers == {engine}, f"{engine} choice must route every clip there"
    # every route's dialogue prompt carries the mute-location doctrine —
    # the words themselves never ride any engine's prompt
    assert any("no voice-over" in op["prompt"] for op in locked["ops"])

    client.post(f"/api/director/sessions/{sid}/run_batch", json={})
    _drive(client, sid)
    film = client.post(f"/api/director/sessions/{sid}/assemble", json={}).json()
    assert film["narration"]["cue_count"] >= 1, film.get("narration")
    assert film["narration"]["overruns"] == []
    assert "film.srt" in film["deliverables"]

    root = cluster_root() / "director" / sid
    assert (root / "film.mp4").is_file()
    assert (root / "picture-lock.mp4").is_file(), "the mute cut survives re-narration"
    qc = json.loads((root / "qc-packet.json").read_text())
    assert qc["narration"]["recorded"] is True
    assert all(c["voice"] for c in qc["narration"]["cues"]), "every cue was cast"


# --- Blueprint 30: Run, Assemble & Upscale All --------------------------------


def _locked_production(client, monkeypatch, tmp_path, **fake_kwargs):
    _fake_adapters(monkeypatch, tmp_path, **fake_kwargs)
    sid = _start(client)
    client.post(
        f"/api/director/sessions/{sid}/produce",
        json={"idea": "a getaway driver decides to leave town from a neon diner"},
    )
    ops = client.post(f"/api/director/sessions/{sid}/lock_script").json()["ops"]
    flow_ids = {o["clip_id"] for o in ops if o["provider"] == "google-flow"}
    grok_ids = {o["clip_id"] for o in ops if o["provider"] == "grok"}
    assert flow_ids and not grok_ids, "one camera shoots the whole film: the Flow lane"
    client.post(f"/api/director/sessions/{sid}/run_batch", json={})
    tick = _drive(client, sid)
    assert tick["failed"] == 0
    return sid, ops, flow_ids, grok_ids


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not installed")
def test_upscale_all_finishes_the_flow_takes_and_cuts_a_1080p_film(
    monkeypatch, tmp_path, fake_narrator
) -> None:
    # this production rolls on Google Flow (the 10 s Omni grid, the Flow finish lane)
    from diony_contracts.cluster import write_engine_choice

    write_engine_choice("google-flow")
    from diony_director import review

    client = _client()
    sid, ops, flow_ids, grok_ids = _locked_production(client, monkeypatch, tmp_path)

    # a 1080p cut is refused, naming the takes, before their finishes exist
    early = client.post(f"/api/director/sessions/{sid}/assemble", json={"finish": "1080p"})
    assert early.status_code == 409, early.text
    assert "Upscale All" in early.text
    assert all(cid in early.text for cid in flow_ids)

    up = client.post(f"/api/director/sessions/{sid}/upscale_all", json={}).json()
    assert up["resolution"] == "1080p"
    assert len(up["targeted"]) == len(flow_ids)
    assert {s["clip_id"] for s in up["skipped"]} == grok_ids
    assert all("Grok" in s["reason"] for s in up["skipped"])
    assert up["already"] == [] and up["rolling"] == []
    assert up["est_usd_micros"] == 3000 * len(flow_ids)  # captcha solves only

    tick = _drive(client, sid)
    assert tick["failed"] == 0
    assert tick["completed"] >= len(ops) + len(flow_ids)

    board = client.get(f"/api/director/sessions/{sid}/board").json()
    rows = {c["clip_id"]: c for c in board["clips"]}
    for cid in flow_ids:
        assert rows[cid]["status"] == "completed", "the take stays the board's unit"
        assert rows[cid]["finish"]["usable"] is True
        assert rows[cid]["finish"]["verdict"] == "pass"
        assert rows[cid]["finish"]["resolution"] == "1080p"
        assert rows[cid]["can_upscale"] is False
    for cid in grok_ids:
        assert rows[cid]["finish"] is None
    for op_id in up["targeted"]:
        state = client.get(f"/api/director/ops/{op_id}").json()
        assert state["op"]["status"] == "completed"
        assert state["review"]["families"] == {"finish": "pass"}
        assert state["review"]["measured"]["height"] == 1080
        assert state["next_op"] is None
        assert "replaces the take" in state["narration"]

    film = client.post(f"/api/director/sessions/{sid}/assemble", json={"finish": "1080p"}).json()
    assert film["clip_count"] == len(ops)
    assert film["raster"] == "1920x1080"
    assert set(film["finish"]["finished"]) == flow_ids
    assert set(film["finish"]["native"]) == grok_ids
    assert set(film["finish"]["conformed_up"]) == grok_ids
    assert film["finish"]["resolution"] == "1080p"
    probe = review.probe(Path(film["film"]))
    assert (probe["width"], probe["height"]) == (1920, 1080)

    # idempotent: a second pass keeps every usable finish and mints nothing
    again = client.post(f"/api/director/sessions/{sid}/upscale_all", json={}).json()
    assert again["targeted"] == []
    assert set(again["already"]) == flow_ids
    receipt = client.get(f"/api/director/sessions/{sid}/receipt").json()
    assert sum(1 for line in receipt["lines"] if "video-upscale" in str(line)) == len(flow_ids)

    # a 720p cut ignores the finishes on request
    native = client.post(f"/api/director/sessions/{sid}/assemble", json={"finish": "720p"}).json()
    assert native["raster"] == "1280x720"
    assert native["finish"]["finished"] == []


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not installed")
def test_a_short_finish_fails_its_check_and_the_take_stays_in_the_cut(
    monkeypatch, tmp_path, fake_narrator
) -> None:
    # this production rolls on Google Flow (the 10 s Omni grid, the Flow finish lane)
    from diony_contracts.cluster import write_engine_choice

    write_engine_choice("google-flow")
    client = _client()
    sid, ops, flow_ids, grok_ids = _locked_production(
        client, monkeypatch, tmp_path, finish_size="640x360"
    )
    up = client.post(f"/api/director/sessions/{sid}/upscale_all", json={}).json()
    _drive(client, sid)
    board = client.get(f"/api/director/sessions/{sid}/board").json()
    rows = {c["clip_id"]: c for c in board["clips"]}
    for cid in flow_ids:
        assert rows[cid]["finish"]["verdict"] == "fail"
        assert rows[cid]["finish"]["usable"] is False
        assert "below the 1080-line target" in " ".join(rows[cid]["finish"]["issues"])
        assert rows[cid]["can_upscale"] is True, "a failed finish may be proposed again"
    for op_id in up["targeted"]:
        state = client.get(f"/api/director/ops/{op_id}").json()
        assert state["op"]["status"] == "completed"
        assert state["review"]["verdict"] == "fail"
        assert state["next_op"] is None, "a finish never rides the fix ladder"
        assert "stays in the cut" in state["narration"]

    strict = client.post(f"/api/director/sessions/{sid}/assemble", json={"finish": "1080p"})
    assert strict.status_code == 409
    assert "below the 1080-line target" in strict.text

    film = client.post(f"/api/director/sessions/{sid}/assemble", json={}).json()
    assert film["raster"] == "1280x720"
    assert film["finish"]["finished"] == []
    assert film["clip_count"] == len(ops)


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not installed")
def test_a_failed_finish_job_never_drafts_a_retake(monkeypatch, tmp_path, fake_narrator) -> None:
    # this production rolls on Google Flow (the 10 s Omni grid, the Flow finish lane)
    from diony_contracts.cluster import write_engine_choice

    write_engine_choice("google-flow")
    client = _client()
    sid, ops, flow_ids, grok_ids = _locked_production(
        client, monkeypatch, tmp_path, fail_upscales=True
    )
    up = client.post(f"/api/director/sessions/{sid}/upscale_all", json={}).json()
    tick = _drive(client, sid)
    assert tick["failed"] == len(flow_ids)
    for op_id in up["targeted"]:
        state = client.get(f"/api/director/ops/{op_id}").json()
        assert state["op"]["status"] == "failed"
        assert state["next_op"] is None
        assert state["review"] is None, "no synthetic fail review lands on the take's behalf"
        assert "nothing is re-shot" in state["narration"]
    board = client.get(f"/api/director/sessions/{sid}/board").json()
    rows = {c["clip_id"]: c for c in board["clips"]}
    for cid in flow_ids:
        assert rows[cid]["status"] == "completed", "the take itself is untouched"
        assert rows[cid]["finish"]["status"] == "failed"
        assert (
            "provider exploded" in rows[cid]["finish"]["reason"]
            or rows[cid]["finish"]["status"] == "failed"
        )
    strict = client.post(f"/api/director/sessions/{sid}/assemble", json={"finish": "1080p"})
    assert strict.status_code == 409
    assert "finish" in strict.text
    film = client.post(f"/api/director/sessions/{sid}/assemble", json={}).json()
    assert film["clip_count"] == len(ops)
    assert film["raster"] == "1280x720"
    # a failed finish is drafted anew on the next pass
    again = client.post(f"/api/director/sessions/{sid}/upscale_all", json={}).json()
    assert len(again["targeted"]) == len(flow_ids)


def test_a_link_pending_job_is_left_alone_for_the_providers_window(monkeypatch, tmp_path) -> None:
    # this production rolls on Google Flow (the 10 s Omni grid, the Flow finish lane)
    from diony_contracts.cluster import write_engine_choice

    write_engine_choice("google-flow")
    from diony_director import session as director_session

    client = _client()
    _fake_adapters(monkeypatch, tmp_path)
    polls = {"n": 0}

    def _pending(handle):
        polls["n"] += 1
        return base.CloudJobStatus(
            state="running",
            detail="completed — media link pending",
            metadata={"link_pending": True, "poll_after_s": 60},
        )

    monkeypatch.setattr(flow, "poll", _pending)
    sid = _start(client)
    client.post(
        f"/api/director/sessions/{sid}/produce",
        json={"idea": "a getaway driver decides to leave town from a neon diner"},
    )
    ops = client.post(f"/api/director/sessions/{sid}/lock_script").json()["ops"]
    flow_op = next(o for o in ops if o["provider"] == "google-flow")
    client.post(f"/api/director/ops/{flow_op['id']}/run")
    client.post(f"/api/director/sessions/{sid}/advance")
    assert polls["n"] == 1
    rt = director_session.runtime(flow_op["id"])
    assert rt["poll_not_before"] > __import__("time").time() + 30
    client.post(f"/api/director/sessions/{sid}/advance")
    client.post(f"/api/director/sessions/{sid}/advance")
    assert polls["n"] == 1, "a tight retry loop lengthens the provider's block"
    assert client.get(f"/api/director/ops/{flow_op['id']}").json()["op"]["status"] == "submitted"
    rt["poll_not_before"] = 0
    client.post(f"/api/director/sessions/{sid}/advance")
    assert polls["n"] == 2


def test_finish_waves_release_a_few_upscales_at_a_time(monkeypatch, tmp_path) -> None:
    from diony_contracts.director import (
        CloudGenerationMode,
        CloudProvider,
        DirectorOp,
        DirectorOpType,
    )
    from diony_director import estimate as estimate_mod
    from diony_director import session as director_session

    monkeypatch.setenv("DIONY_FLOW_UPSCALE_PARALLEL", "2")
    client = _client()
    sid = _start(client)
    est = estimate_mod.estimate_op(
        provider=CloudProvider.GOOGLE_FLOW,
        model=flow.UPSCALE_MODEL,
        mode=CloudGenerationMode.UPSCALE,
        duration_s=8,
        resolution="1080p",
    )
    finishes = [
        DirectorOp(
            id=f"op-up-{i}",
            session_id=sid,
            op=DirectorOpType.UPSCALE,
            clip_id=f"cl-{i}",
            provider=CloudProvider.GOOGLE_FLOW,
            model=flow.UPSCALE_MODEL,
            mode=CloudGenerationMode.UPSCALE,
            duration_s=8,
            resolution="1080p",
            artifact_refs={"source_video": f"user:v{i}", "source_op": f"op-src-{i}"},
            estimate=est,
        )
        for i in range(5)
    ]
    for op in finishes:
        director_session.register_op(sid, op)
        director_session.add_batch_target(sid, op.id)
    first = director_session.ready_ops(sid)
    assert [op.id for op in first] == ["op-up-0", "op-up-1"]
    finishes[0].status = "submitted"
    finishes[1].status = "submitted"
    assert director_session.ready_ops(sid) == []
    finishes[0].status = "completed"
    assert [op.id for op in director_session.ready_ops(sid)] == ["op-up-2"]
    assert director_session.queued_count(sid) == 3


# --- The film's narrator voice: a console setting, Auto by default ------------


def test_the_voice_setting_is_offered_validated_and_persisted(monkeypatch) -> None:
    from diony_director import session as director_session

    client = _client()
    catalog = client.get("/api/director/voices").json()
    assert catalog["default"] == ""
    keys = [v["key"] for v in catalog["voices"]]
    assert "atlas" in keys and "eve" in keys
    sid = _start(client)
    assert client.get(f"/api/director/sessions/{sid}/settings").json()["voice"] == ""

    bad = client.post(f"/api/director/sessions/{sid}/voice", json={"voice": "marvin"})
    assert bad.status_code == 422 and "unknown narrator voice" in bad.text
    assert (
        client.post(f"/api/director/sessions/{sid}/voice", json={"voice": "Atlas"}).json()["voice"]
        == "atlas"
    )
    assert client.get(f"/api/director/sessions/{sid}/settings").json()["voice"] == "atlas"
    # the message body carries it too, None keeps, "auto" releases
    client.post(f"/api/director/sessions/{sid}/message", json={"text": "a truck", "duration_s": 6})
    assert director_session.voice_for(sid) == "atlas"
    client.post(
        f"/api/director/sessions/{sid}/message",
        json={"text": "a truck at night", "duration_s": 6, "voice": "auto"},
    )
    assert director_session.voice_for(sid) == ""
    client.post(f"/api/director/sessions/{sid}/voice", json={"voice": "luna"})

    director_session.reset()  # the process dies
    director_session.rehydrate_from_vault()
    assert director_session.voice_for(sid) == "luna", "the choice survives a restart"


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not installed")
def test_the_cut_speaks_with_the_chosen_voice_and_reports_it(
    monkeypatch, tmp_path, fake_narrator
) -> None:
    # this production rolls on Google Flow (the 10 s Omni grid, the Flow finish lane)
    from diony_contracts.cluster import write_engine_choice

    write_engine_choice("google-flow")
    monkeypatch.delenv("DIONY_GROK_VOICE", raising=False)
    client = _client()
    sid, ops, flow_ids, grok_ids = _locked_production(client, monkeypatch, tmp_path)
    # Auto: the Director casts one roster voice for the film
    film = client.post(f"/api/director/sessions/{sid}/assemble", json={}).json()
    auto = film["narration"]["voice"]
    assert auto["chosen"] == ""
    assert auto["voice_id"] in DEFAULT_ROSTER, "the splitter's vox- identity ref is not a voice id"
    assert "cast for:" in auto["cast"]
    # a console choice speaks the next cut — and the whole film — in that voice
    client.post(f"/api/director/sessions/{sid}/voice", json={"voice": "atlas"})
    film = client.post(f"/api/director/sessions/{sid}/assemble", json={}).json()
    assert film["narration"]["voice"]["voice_id"] == "atlas"
    assert film["narration"]["voice"]["chosen"] == "atlas"
    assert "chosen in the Director console" in film["narration"]["voice"]["cast"]
    # the assemble body may pick one for this cut and it sticks to the film
    film = client.post(f"/api/director/sessions/{sid}/assemble", json={"voice": "Celeste"}).json()
    assert film["narration"]["voice"]["voice_id"] == "celeste"
    assert client.get(f"/api/director/sessions/{sid}/settings").json()["voice"] == "celeste"
