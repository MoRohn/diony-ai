"""The Director service end-to-end: propose → run → poll → persisted clip → reconciled ledger."""

import importlib
import re
from datetime import UTC

from fastapi.testclient import TestClient

from diony_director import app as app_module
from diony_director.providers import base, grok


class _DoneStatus:
    pass


def _client() -> TestClient:
    importlib.reload(app_module)
    return TestClient(app_module.app, raise_server_exceptions=False)


def test_status_reports_cloud_engine_and_configured_providers() -> None:
    client = _client()
    body = client.get("/api/director/status").json()
    assert body["engine"] == "cloud"
    assert body["providers"]["grok"] is True
    assert body["providers"]["google-flow"] is True
    assert body["flow_plan"] == "pro"
    assert body["engine_choice"] == "grok"  # the conftest's Studio choice


def test_full_guided_loop_persists_the_clip_and_reconciles_the_ledger(
    monkeypatch, tmp_path
) -> None:
    client = _client()
    sid = client.post("/api/director/sessions").json()["session_id"]
    proposal = client.post(
        f"/api/director/sessions/{sid}/message",
        json={"text": "a dusty truck at dusk", "duration_s": 6},
    ).json()
    op = proposal["op"]
    assert op["estimate"]["total_usd_micros"] == 840_000

    handle = base.CloudJobHandle(
        provider_id="grok", external_id="req-1", model="grok-imagine-video-1.5", duration_s=6
    )
    monkeypatch.setattr(grok, "submit", lambda _op: handle)
    monkeypatch.setattr(
        grok,
        "poll",
        lambda _h: base.CloudJobStatus(
            state="done", result_url="https://cdn.x.ai/clip.mp4", metadata={"duration_s": 6}
        ),
    )
    payload = b"grok-clip-bytes"

    def fake_download(url, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(payload)
        import hashlib

        return base.FetchedClip(
            path=dest, sha256=hashlib.sha256(payload).hexdigest(), bytes=len(payload), media_url=url
        )

    monkeypatch.setattr(base, "download", fake_download)

    run = client.post(f"/api/director/ops/{op['id']}/run")
    assert run.status_code == 200, run.text
    job_id = run.json()["job_id"]
    assert job_id

    state = client.get(f"/api/director/ops/{op['id']}").json()
    assert state["op"]["status"] == "completed"
    assert state["clip"]["sha256"]

    clip = client.get(f"/api/director/clips/{op['id']}")
    assert clip.status_code == 200
    assert clip.content == payload

    ledger = client.get("/api/director/ledger").json()
    assert ledger["totals"]["cost_usd_micros"] == 840_000
    assert ledger["today_usd_micros"] == 840_000  # the op just completed, so it counts today
    assert ledger["committed_usd_micros"] == 0


def test_daily_spend_counts_only_todays_usage() -> None:
    from datetime import datetime, timedelta

    from diony_contracts.launch import CostBreakdown, ProviderCapability
    from diony_director import ledger
    from diony_producer.costs import CostAnalyticsService, UsageSample

    client = _client()
    client.get("/api/director/status")
    plane, ctx = app_module._boot()
    run = ledger.ensure_run(plane, ctx.workspace.id, ctx.user.id, "s-daily")

    def sample(sample_id: str, cost: int, occurred_at: datetime) -> UsageSample:
        return UsageSample(
            id=sample_id,
            workspace_id=ctx.workspace.id,
            project_id=ledger.DIRECTOR_PROJECT_ID,
            run_id=run.id,
            job_id=f"job-{sample_id}",
            capability=ProviderCapability.VIDEO,
            provider_id="grok",
            model="grok-imagine-video-1.5",
            quantity_microunits=1_000_000,
            unit="clip",
            actual_internal_credits=0,
            actual_cost_breakdown=CostBreakdown(compute_usd_micros=cost),
            occurred_at=occurred_at,
        )

    service = CostAnalyticsService(plane)
    now = datetime.now(UTC)
    service.record(sample("usage-today", 300_000, now))
    # two days back is before local midnight in every timezone — never flaky
    service.record(sample("usage-past", 500_000, now - timedelta(days=2)))

    body = client.get("/api/director/ledger").json()
    assert body["totals"]["cost_usd_micros"] == 800_000
    assert body["today_usd_micros"] == 300_000


def test_transport_errors_are_retried_then_raised_as_transient(monkeypatch) -> None:
    import httpx

    monkeypatch.setattr(base, "TRANSIENT_BACKOFF_S", 0)
    calls = {"n": 0}

    class _Ok:
        status_code = 200

    def flaky(url, **kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ReadError("[SSL: SSLV3_ALERT_BAD_RECORD_MAC] ssl/tls alert bad record mac")
        return _Ok()

    monkeypatch.setattr(base.httpx, "post", flaky)
    assert base.post_with_retries("https://api.x.ai/x", what="test").status_code == 200
    assert calls["n"] == 3

    def always_down(url, **kwargs):
        raise httpx.ReadError("[SSL: SSLV3_ALERT_BAD_RECORD_MAC] ssl/tls alert bad record mac")

    monkeypatch.setattr(base.httpx, "post", always_down)
    try:
        base.post_with_retries("https://api.x.ai/x", what="test")
        raise AssertionError("expected CloudAdapterTransientError")
    except base.CloudAdapterTransientError as err:
        assert "after 3 attempts" in str(err)


def test_transient_network_error_keeps_the_op_runnable(monkeypatch) -> None:
    client = _client()
    sid = client.post("/api/director/sessions").json()["session_id"]
    proposal = client.post(
        f"/api/director/sessions/{sid}/message", json={"text": "a truck", "duration_s": 6}
    ).json()
    op_id = proposal["op"]["id"]

    def boom(_op):
        raise base.CloudAdapterTransientError(
            "xAI video submit failed after 3 attempts — ReadError"
        )

    monkeypatch.setattr(grok, "submit", boom)
    run = client.post(f"/api/director/ops/{op_id}/run")
    assert run.status_code == 503
    state = client.get(f"/api/director/ops/{op_id}").json()
    assert state["op"]["status"] == "proposed"  # nothing spent, still runnable


def test_provider_quota_429_pauses_the_wave_and_resumes_after_the_window(monkeypatch) -> None:
    """The 2026-09-02 live failure: one Flow quota 429 must never fail an op or
    stop the run — it pauses, stays targeted, and retries once the window passes."""
    import time as time_mod

    client = _client()
    monkeypatch.setattr(app_module, "_kick_submitter", lambda sid: None)
    sid = client.post("/api/director/sessions").json()["session_id"]
    op_id = client.post(
        f"/api/director/sessions/{sid}/message", json={"text": "a truck", "duration_s": 6}
    ).json()["op"]["id"]

    calls = {"n": 0}

    def quota(_op):
        calls["n"] += 1
        raise base.CloudAdapterQuotaError(
            "Flow request rejected (429): PUBLIC_ERROR_USER_QUOTA_REACHED",
            retry_at=time_mod.time() + 1200,
        )

    monkeypatch.setattr(grok, "submit", quota)
    client.post(f"/api/director/sessions/{sid}/run_batch", json={})

    from diony_director import session as session_mod

    submitted, held = app_module._submit_ready(sid)
    op = session_mod.get_op(op_id)
    assert submitted == []
    assert op.status == "proposed"  # a quota pause is never a failure
    assert held and "resumes automatically" in held[0]["reason"]
    # the entry is structured for the room's pause card: kind, provider, a
    # reset epoch, and a 12-hour local clock in the line (1:14 PM EDT, never 13:14)
    entry = held[0]
    assert entry["kind"] == "quota" and entry["provider"] in {"grok", "google-flow"}
    assert entry["detail"] == "PUBLIC_ERROR_USER_QUOTA_REACHED", "the card gets the bare reason"
    assert entry["resume_at"] > __import__("time").time()
    assert re.search(r"\b(1[0-2]|[1-9]):[0-5]\d [AP]M [A-Z]{2,5}\b", entry["reason"]), entry[
        "reason"
    ]
    assert "{" not in entry["reason"]
    assert session_mod.runtime(op_id)["quota_hold_until"] > time_mod.time() + 600
    assert op_id in session_mod.batch_targets(sid)  # still queued for the run

    # while the window is open the wave skips it quietly — no resubmit attempt
    submitted, held = app_module._submit_ready(sid)
    assert calls["n"] == 1
    assert submitted == [] and held == []

    # window over: the next wave retries on its own and the hold clears
    session_mod.runtime(op_id)["quota_hold_until"] = time_mod.time() - 1
    handle = base.CloudJobHandle(
        provider_id="grok", external_id="req-q", model="grok-imagine-video-1.5", duration_s=6
    )
    monkeypatch.setattr(grok, "submit", lambda _op: handle)
    submitted, _ = app_module._submit_ready(sid)
    assert submitted == [op_id]
    assert "quota_hold_until" not in session_mod.runtime(op_id)


def test_manual_run_maps_quota_429_to_503_with_retry_after(monkeypatch) -> None:
    client = _client()
    sid = client.post("/api/director/sessions").json()["session_id"]
    op_id = client.post(
        f"/api/director/sessions/{sid}/message", json={"text": "a truck", "duration_s": 6}
    ).json()["op"]["id"]

    def quota(_op):
        raise base.CloudAdapterQuotaError("xAI video submit rejected (429): rate limited")

    monkeypatch.setattr(grok, "submit", quota)
    run = client.post(f"/api/director/ops/{op_id}/run")
    assert run.status_code == 503
    assert run.headers.get("retry-after") == str(app_module.QUOTA_HOLD_DEFAULT_S)
    state = client.get(f"/api/director/ops/{op_id}").json()
    assert state["op"]["status"] == "proposed"  # nothing spent, still runnable


def test_failed_op_retries_at_its_locked_price(monkeypatch) -> None:
    client = _client()
    # keep the retry synchronous-deterministic: no background submit wave
    monkeypatch.setattr(app_module, "_kick_submitter", lambda sid: None)
    sid = client.post("/api/director/sessions").json()["session_id"]
    proposal = client.post(
        f"/api/director/sessions/{sid}/message", json={"text": "a truck", "duration_s": 6}
    ).json()
    op_id = proposal["op"]["id"]

    from diony_director import session as session_mod

    op = session_mod.get_op(op_id)
    op.status = "failed"  # the stranded state the 2026-09-01 live run was left in
    op.job_id = "job-old"
    session_mod.runtime(op_id)["transient_holds"] = 3

    # while failed, the plain run path refuses it
    assert client.post(f"/api/director/ops/{op_id}/run").status_code == 409

    resumable = client.get("/api/director/resumable").json()["sessions"]
    assert [row["session_id"] for row in resumable] == [sid]
    assert resumable[0]["failed"] == 1

    retried = client.post(f"/api/director/ops/{op_id}/retry")
    assert retried.status_code == 200
    body = retried.json()["op"]
    assert body["status"] == "proposed"
    assert body["job_id"] == ""
    assert "transient_holds" not in session_mod.runtime(op_id)
    assert op_id in session_mod.batch_targets(sid)

    # only failed ops retry — a proposed (or printed) op never re-spends this way
    assert client.post(f"/api/director/ops/{op_id}/retry").status_code == 409


def test_run_is_refused_when_the_engine_is_legacy(monkeypatch) -> None:
    client = _client()
    sid = client.post("/api/director/sessions").json()["session_id"]
    proposal = client.post(
        f"/api/director/sessions/{sid}/message", json={"text": "a truck", "duration_s": 6}
    ).json()
    monkeypatch.setenv("DIONY_ENGINE", "local_legacy")
    run = client.post(f"/api/director/ops/{proposal['op']['id']}/run")
    assert run.status_code == 409
    assert "legacy" in run.json()["detail"].lower()


def test_a_failed_job_is_visible_not_hidden(monkeypatch) -> None:
    client = _client()
    sid = client.post("/api/director/sessions").json()["session_id"]
    proposal = client.post(
        f"/api/director/sessions/{sid}/message", json={"text": "a truck", "duration_s": 6}
    ).json()
    op = proposal["op"]
    handle = base.CloudJobHandle(
        provider_id="grok", external_id="req-9", model="grok-imagine-video-1.5", duration_s=6
    )
    monkeypatch.setattr(grok, "submit", lambda _op: handle)
    monkeypatch.setattr(
        grok, "poll", lambda _h: base.CloudJobStatus(state="failed", detail="GPU exploded")
    )
    run = client.post(f"/api/director/ops/{op['id']}/run")
    assert run.status_code == 200
    state = client.get(f"/api/director/ops/{op['id']}").json()
    assert state["op"]["status"] == "failed"
    assert "GPU exploded" in state["job"]["error_message"]


def test_drafting_is_async_when_the_brain_is_configured(monkeypatch) -> None:
    """A reasoning brain takes minutes; produce must return instantly and the
    draft must be pollable — a gateway timeout can never kill a screenplay."""
    import threading
    import time

    from diony_director import screenwriter
    from diony_director import session as session_mod

    client = _client()
    sid = client.post("/api/director/sessions").json()["session_id"]

    monkeypatch.setenv("DIONY_DIRECTOR_BRAIN_URL", "https://api.x.ai")
    gate = threading.Event()
    real_template = screenwriter._template

    def slow_write(idea, runtime_target_s=60, cast_size=2, **kwargs):
        gate.wait(timeout=10)
        return real_template(idea, runtime_target_s, cast_size)

    monkeypatch.setattr(session_mod.screenwriter, "write_script", slow_write)
    from master_brain_fakes import fast_master_derive

    monkeypatch.setattr(session_mod.master_mod, "derive", fast_master_derive)

    body = client.post(
        f"/api/director/sessions/{sid}/produce",
        json={"idea": "a slow-brained masterpiece", "runtime_s": 60},
    ).json()
    assert body["status"] == "drafting"
    assert "minute" in body["reply"]

    # locking while drafting is a named refusal, not a crash
    locked = client.post(f"/api/director/sessions/{sid}/lock_script")
    assert locked.status_code == 409
    assert "still being written" in locked.json()["detail"]

    assert client.get(f"/api/director/sessions/{sid}/draft").json()["status"] == "drafting"
    gate.set()
    for _ in range(50):
        state = client.get(f"/api/director/sessions/{sid}/draft").json()
        if state["status"] == "ready":
            break
        time.sleep(0.1)
    assert state["status"] == "ready"
    assert state["script"]["scenes"]
    assert client.post(f"/api/director/sessions/{sid}/lock_script").status_code == 200


def test_a_failed_draft_is_a_named_state(monkeypatch) -> None:
    from diony_director import session as session_mod

    client = _client()
    sid = client.post("/api/director/sessions").json()["session_id"]
    monkeypatch.setenv("DIONY_DIRECTOR_BRAIN_URL", "https://api.x.ai")

    def boom(idea, runtime_target_s=60, cast_size=2, **kwargs):
        raise RuntimeError("brain melted")

    monkeypatch.setattr(session_mod.screenwriter, "write_script", boom)
    from master_brain_fakes import fast_master_derive

    monkeypatch.setattr(session_mod.master_mod, "derive", fast_master_derive)
    client.post(
        f"/api/director/sessions/{sid}/produce",
        json={"idea": "doomed", "runtime_s": 60},
    )
    import time

    for _ in range(50):
        state = client.get(f"/api/director/sessions/{sid}/draft").json()
        if state["status"] == "failed":
            break
        time.sleep(0.1)
    assert state["status"] == "failed"
    assert "brain melted" in state["error"]


def test_the_speech_guard_mutes_dirty_and_unverifiable_spoken_clips(monkeypatch) -> None:
    """Blueprint 28's hard guarantee at the cut: a location track that carries
    a talking voice — or a line-carrying clip whose track cannot be verified —
    is muted before assembly, so a provider-invented voice can never stack
    under the post narrator."""
    from pathlib import Path

    from diony_director import review as review_mod

    class _Speech:
        def __init__(self, transcript, word_count, speech_s):
            self.transcript = transcript
            self.word_count = word_count
            self.speech_s = speech_s

    detections = {
        "dirty.mp4": _Speech("get out of here now", 5, 2.0),
        "clean.mp4": _Speech("", 0, 0.0),
        "spoken-unchecked.mp4": None,
        "picture-unchecked.mp4": None,
    }
    monkeypatch.setattr(review_mod, "location_speech", lambda p: detections[p.name])
    paths = [Path(name) for name in detections]
    labels = ["cl-1", "cl-2", "cl-3", "cl-4"]
    carries_line = [False, True, True, False]
    specs: list[dict] = [{}, {}, {}, {}]
    report = app_module._speech_guard(paths, labels, carries_line, specs)
    assert specs[0].get("mute") is True, "a measured voice is muted even on a picture clip"
    assert "mute" not in specs[1], "a measured-clean location track survives"
    assert specs[2].get("mute") is True, "an unverifiable spoken clip is presumed dirty"
    assert "mute" not in specs[3], "an unverifiable picture-only clip is left alone"
    muted = {entry["clip"] for entry in report["muted"]}
    assert muted == {"cl-1", "cl-3"}


def test_the_ledger_reads_each_provider_in_its_own_currency() -> None:
    """Grok is metered in dollars, Google Flow spends plan credits; the header
    meter needs both, lifetime and today, per provider."""
    from datetime import datetime, timedelta

    from diony_contracts.launch import CostBreakdown, ProviderCapability
    from diony_director import ledger
    from diony_producer.costs import CostAnalyticsService, UsageSample

    client = _client()
    client.get("/api/director/status")
    plane, ctx = app_module._boot()
    run = ledger.ensure_run(plane, ctx.workspace.id, ctx.user.id, "s-currency")

    def sample(sample_id: str, provider: str, credits: int, cost: int, occurred_at: datetime):
        return UsageSample(
            id=sample_id,
            workspace_id=ctx.workspace.id,
            project_id=ledger.DIRECTOR_PROJECT_ID,
            run_id=run.id,
            job_id=f"job-{sample_id}",
            capability=ProviderCapability.VIDEO,
            provider_id=provider,
            model="omni-flash" if provider == "google-flow" else "grok-imagine-video-1.5",
            quantity_microunits=1_000_000,
            unit="clip",
            actual_internal_credits=credits,
            actual_cost_breakdown=CostBreakdown(compute_usd_micros=cost),
            occurred_at=occurred_at,
        )

    service = CostAnalyticsService(plane)
    now = datetime.now(UTC)
    service.record(sample("usage-grok-today", "grok", 0, 840_000, now))
    service.record(sample("usage-flow-today", "google-flow", 15, 299_850, now))
    service.record(sample("usage-flow-past", "google-flow", 12, 239_880, now - timedelta(days=2)))

    body = client.get("/api/director/ledger").json()
    grok = body["by_provider"]["grok"]
    flow = body["by_provider"]["google-flow"]
    assert grok["usd_micros"] == 840_000 and grok["credits"] == 0
    assert grok["today_usd_micros"] == 840_000
    assert flow["credits"] == 27 and flow["today_credits"] == 15
    assert flow["usd_micros"] == 299_850 + 239_880 and flow["today_usd_micros"] == 299_850
    assert body["today_usd_micros"] == 840_000 + 299_850
    assert body["flow_plan"] == "pro"


def test_the_header_meter_reads_in_the_routes_currency() -> None:
    """The console shows credits under Google Flow, dollars under Grok, and
    both under Director's choice — and re-renders when the route changes."""
    from pathlib import Path

    static = Path(app_module.__file__).resolve().parent / "static"
    js = (static / "app.js").read_text()
    html = (static / "index.html").read_text()
    assert "by_provider" in js and "function renderMeter" in js
    assert "credits(flow.credits)" in js and "usd(grok.usd_micros" in js
    assert 'route === "google-flow"' in js and 'route === "grok"' in js
    provider_pill = html.split('id="provider"', 1)[1].split("</select>", 1)[0]
    assert "Director's choice" not in js and 'value="auto"' not in provider_pill
    assert "renderMeter(); // the meter reads in the route's currency" in js
    # the pill mirrors Studio at boot and the meter re-renders right then; the
    # ledger carries the choice so the meter never defaults to dollars
    assert "renderMeter(); // the meter reads in the camera's currency" in js
    assert "lastLedger.engine_choice" in js
    assert "plan credits for Google Flow" in html


def test_the_console_and_its_api_are_never_browser_cached() -> None:
    """A console index cached across a desktop rebuild showed the previous UI."""
    client = _client()
    for path in ("/", "/api/director/status", "/static/app.js?v=p54"):
        res = client.get(path)
        assert res.status_code == 200, path
        assert res.headers["cache-control"] == "no-store, max-age=0", path


def test_the_console_starts_at_zero_duration_and_refuses_a_run_without_one() -> None:
    from pathlib import Path

    static = Path(app_module.__file__).resolve().parent / "static"
    html = (static / "index.html").read_text()
    js = (static / "app.js").read_text()
    css = (static / "app.css").read_text()
    # the Budget pill is gone from the console, its CSS and its request bodies
    assert 'id="budget"' not in html and "Budget $" not in html
    assert "#budget" not in css and "max_usd" not in js and '$("budget")' not in js
    # duration starts at 0m 0s, on first paint and on a fresh session
    assert 'id="dur-min" type="number" min="0" max="180" step="1" value="0"' in html
    assert 'id="dur-sec" type="number" min="0" max="59" step="1" value="0"' in html
    assert "setDuration(0);\n      runReset();" in js and "setDuration(8)" not in js
    # both run starters ask for a duration first and never guess one
    assert "function requireDuration" in js
    assert js.count("if (!requireDuration()) return;") == 2
    assert "durationSeconds() || 8" not in js and "durationSeconds() || 60" not in js
    assert "nothing rolls at 0m 0s" in js and "Set a duration before the run" in js
