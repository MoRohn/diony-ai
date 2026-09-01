"""The Director service end-to-end: propose → run → poll → persisted clip → reconciled ledger."""

import importlib

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


def test_full_guided_loop_persists_the_clip_and_reconciles_the_ledger(monkeypatch, tmp_path) -> None:
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

        return base.FetchedClip(path=dest, sha256=hashlib.sha256(payload).hexdigest(), bytes=len(payload), media_url=url)

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
    assert ledger["committed_usd_micros"] == 0


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

    from diony_director import screenwriter, session as session_mod

    client = _client()
    sid = client.post("/api/director/sessions").json()["session_id"]

    monkeypatch.setenv("DIONY_DIRECTOR_BRAIN_URL", "https://api.x.ai")
    gate = threading.Event()
    real_template = screenwriter._template

    def slow_write(idea, runtime_target_s=60, cast_size=2, **kwargs):
        gate.wait(timeout=10)
        return real_template(idea, runtime_target_s, cast_size)

    monkeypatch.setattr(session_mod.screenwriter, "write_script", slow_write)

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
