"""Producer under the cloud engine: monitoring + Gallery, with Director films."""

from pathlib import Path

from fastapi.testclient import TestClient

from diony_contracts.cluster import cluster_root
from diony_producer.app import app


def _client() -> TestClient:
    return TestClient(app)


def test_engine_state_reports_cloud_by_default() -> None:
    body = _client().get("/api/engine").json()
    assert body["cloud"] is True
    assert body["director_url"] == "http://director.diony.local"


def test_engine_state_reports_legacy_when_flagged(monkeypatch) -> None:
    monkeypatch.setenv("DIONY_ENGINE", "local_legacy")
    assert _client().get("/api/engine").json()["cloud"] is False


def test_director_films_lists_and_serves_vault_cuts() -> None:
    film_dir = cluster_root() / "director" / "s-abc123"
    film_dir.mkdir(parents=True, exist_ok=True)
    (film_dir / "film.mp4").write_bytes(b"mp4" * 2048)

    client = _client()
    films = client.get("/api/director-films").json()["films"]
    assert len(films) == 1
    assert films[0]["session"] == "s-abc123"
    assert films[0]["bytes"] == 3 * 2048

    served = client.get(films[0]["url"])
    assert served.status_code == 200
    assert served.content == b"mp4" * 2048


def test_director_film_refuses_path_tricks() -> None:
    assert _client().get("/api/director-films/..%2F..%2Fetc").status_code == 404
    assert _client().get("/api/director-films/no-such-session").status_code == 404


def test_empty_vault_lists_no_films() -> None:
    assert _client().get("/api/director-films").json()["films"] == []


def test_producer_masthead_links_back_to_the_director() -> None:
    html = _client().get("/").text
    assert 'id="director-link"' in html
    assert "director.diony.local" in html
    assert 'id="studio-surface-link"' in html


def test_cloud_production_reports_director_runs() -> None:
    from diony_contracts.launch import GenerationJob, JobStatus, ProviderCapability, WorkflowRun, WorkflowStage
    from diony_producer.control_plane import ControlPlane

    plane = ControlPlane()
    ctx = plane.bootstrap_local()
    run = WorkflowRun.production_default(
        id="director-s-test77", workspace_id=ctx.workspace.id,
        project_id="director", created_by=ctx.user.id,
    )
    plane.save_run(run)
    for i, status in enumerate([JobStatus.SUCCEEDED, JobStatus.SUCCEEDED, JobStatus.RUNNING, JobStatus.FAILED]):
        plane.save_job(GenerationJob(
            id=f"job-t{i}", run_id=run.id, workspace_id=ctx.workspace.id,
            project_id="director", stage=WorkflowStage.VIDEO,
            capability=ProviderCapability.VIDEO, provider_id="grok",
            model="grok-imagine-video-1.5", status=status,
            idempotency_key=f"op-t{i}",
            actual_cost_usd_micros=840_000 if status is JobStatus.SUCCEEDED else 0,
        ))

    body = _client().get("/api/cloud-production").json()
    assert len(body["sessions"]) == 1
    item = body["sessions"][0]
    assert item["session"] == "s-test77"
    assert item["jobs"] == 4
    assert item["counts"]["succeeded"] == 2
    assert item["counts"]["failed"] == 1
    assert item["active"] is True
    assert item["actual_usd_micros"] == 1_680_000


def _make_film(session: str, duration: float = 1.0) -> Path:
    import subprocess

    dest = cluster_root() / "director" / session / "film.mp4"
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc2=size=320x180:duration={duration}",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(dest),
        ],
        check=True, capture_output=True,
    )
    return dest


def test_stitch_selected_films_makes_a_join(tmp_path) -> None:
    import shutil as _shutil

    import pytest as _pytest

    if _shutil.which("ffmpeg") is None:
        _pytest.skip("ffmpeg not installed")
    _make_film("s-one")
    _make_film("s-two")
    client = _client()
    res = client.post(
        "/api/director-films/stitch", json={"sessions": ["s-one", "s-two"]}
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["session"].startswith("join-")
    served = client.get(body["url"])
    assert served.status_code == 200
    assert len(served.content) > 1024
    films = client.get("/api/director-films").json()["films"]
    assert any(f["session"] == body["session"] for f in films)


def test_stitch_requires_two_known_films() -> None:
    client = _client()
    assert client.post("/api/director-films/stitch", json={"sessions": ["s-one"]}).status_code == 422
    assert (
        client.post(
            "/api/director-films/stitch", json={"sessions": ["s-none", "s-nada"]}
        ).status_code
        == 404
    )


def test_delete_selected_films_removes_media_only(tmp_path) -> None:
    import shutil as _shutil

    import pytest as _pytest

    if _shutil.which("ffmpeg") is None:
        _pytest.skip("ffmpeg not installed")
    _make_film("s-del-a")
    _make_film("s-del-b")
    _make_film("s-keep")
    client = _client()
    res = client.post(
        "/api/director-films/delete", json={"sessions": ["s-del-a", "s-del-b", "s-ghost"]}
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert sorted(body["removed"]) == ["s-del-a", "s-del-b"]
    assert body["missing"] == ["s-ghost"]
    films = client.get("/api/director-films").json()["films"]
    assert [f["session"] for f in films] == ["s-keep"]
    assert client.get("/api/director-films/s-del-a").status_code == 404


def test_delete_refuses_empty_and_path_tricks() -> None:
    client = _client()
    assert client.post("/api/director-films/delete", json={"sessions": []}).status_code == 422
    res = client.post("/api/director-films/delete", json={"sessions": ["../../etc"]})
    assert res.status_code == 200
    assert res.json()["removed"] == []


def test_stitch_joins_in_gallery_order_newest_first(tmp_path, monkeypatch) -> None:
    """The Gallery lists newest FIRST; a stitch must play top-to-bottom in
    that same order regardless of checkbox-click order (it briefly shipped
    oldest-first — reversed from the display)."""
    import os
    import subprocess as _subprocess
    import time

    from diony_contracts.cluster import cluster_root

    root = cluster_root() / "director"
    old = root / "s-old" / "film.mp4"
    new = root / "s-new" / "film.mp4"
    for path in (old, new):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"mp4data" * 512)
    now = time.time()
    os.utime(old, (now - 1000, now - 1000))
    os.utime(new, (now, now))

    captured = {}
    real_run = _subprocess.run

    def fake_run(cmd, **kwargs):
        listing = next((c for c in cmd if str(c).endswith("inputs.txt")), None)
        if listing is not None:
            from pathlib import Path as _P

            captured["listing"] = _P(listing).read_text()
            dest = _P(str(cmd[-1]))
            dest.write_bytes(b"joined" * 4096)

            class _Done:
                returncode = 0
                stderr = b""

            return _Done()
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(_subprocess, "run", fake_run)
    res = _client().post(
        "/api/director-films/stitch", json={"sessions": ["s-old", "s-new"]}
    )
    assert res.status_code == 200, res.text
    lines = [l for l in captured["listing"].splitlines() if l.startswith("file ")]
    assert "s-new" in lines[0] and "s-old" in lines[1], "newest first — the Gallery order"
