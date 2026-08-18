from diony_contracts.cluster import write_mode_lock, write_site_lock
from fastapi.testclient import TestClient

from diony_conductor.app import app


def test_tick_dispatches_stub_take() -> None:
    client = TestClient(app)
    client.post("/v1/reset")
    res = client.post("/v1/tick")
    body = res.json()
    assert body["action"] == "dispatch_shot"
    project = client.get("/v1/project").json()
    alley = [t for t in project["takes"] if t["shot_id"] == "sh-030"]
    assert len(alley) >= 2


def test_dual_site_refuses_concurrent_engines() -> None:
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
            "recipe": "spark-2x-v1 TP=2",
            "exception_by": "Rohn Springfield",
        }
    )
    write_mode_lock("BRAIN", "brain")
    client = TestClient(app)
    site = client.get("/v1/site").json()
    assert site["path"] == "dual"
    assert site["sequential"] is True
    refused = client.post("/v1/mode", json={"mode": "FARM", "engine": "wan"})
    assert refused.status_code == 400
    allowed = client.post("/v1/mode", json={"mode": "BRAIN", "engine": "brain"})
    assert allowed.status_code == 200
    health = client.get("/health").json()
    assert health["site"]["path"] == "dual"
