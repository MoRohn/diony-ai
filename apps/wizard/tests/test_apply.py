from fastapi.testclient import TestClient

from diony_wizard.app import app
from diony_wizard.apply import hf_download_lines, remote_setup_script


def test_hf_pack_includes_wan_and_sr() -> None:
    joined = "\n".join(hf_download_lines())
    assert "Wan-AI/Wan2.2-T2V-A14B" in joined
    assert "DeepSeek-V4-Flash-0731" in joined
    assert "diony_sr_v1" in joined
    assert "RealESRGAN_x2.pth" in joined
    script = remote_setup_script("/opt/diony-ai")
    assert "diony-producer" in script
    assert "apps/finish" in script
    assert "huggingface_hub" in script
    assert "snapshot_download" in script
    assert script.find("huggingface_hub") < script.find("snapshot_download")
    assert "huggingface-cli download" not in script
    assert "--use_prompt_extend" not in script
    assert "DASH_API_KEY" in script
    assert "diony-wan &" not in script
    assert "diony-finish &" not in script


def test_dual_app_live_fake_apply(monkeypatch) -> None:
    monkeypatch.delenv("DIONY_DRY_RUN", raising=False)
    monkeypatch.setenv("DIONY_WIZARD_LIVE", "1")
    monkeypatch.setenv("DIONY_APPLY_FAKE", "1")
    client = TestClient(app)
    client.post("/api/reset")
    path = client.post(
        "/api/steps/path/pass",
        json={
            "answers": {
                "site": "dual",
                "provider": "this rack",
                "signer": "Rohn Springfield",
            }
        },
    )
    assert path.status_code == 200
    client.post("/api/steps/connect/pass", json={"answers": {}})
    res = client.post("/api/steps/app/pass", json={"answers": {}})
    assert res.status_code == 200
    body = res.json()
    assert body["apply"]["ok"] is True
    assert body["active_step"] == "producer"
    assert body["dry_run"] is False


def test_dual_app_dry_run_does_not_ssh(monkeypatch) -> None:
    monkeypatch.setenv("DIONY_DRY_RUN", "1")
    client = TestClient(app)
    client.post("/api/reset")
    client.post(
        "/api/steps/path/pass",
        json={
            "answers": {
                "site": "dual",
                "provider": "this rack",
                "signer": "Rohn Springfield",
            }
        },
    )
    client.post("/api/steps/connect/pass", json={"answers": {}})
    res = client.post("/api/steps/app/pass", json={"answers": {}})
    assert res.status_code == 200
    body = res.json()
    assert body["dry_run"] is True
    assert body["apply"]["running"] is False
    assert body["apply"]["ok"] is False
    assert body["active_step"] == "producer"
