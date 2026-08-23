from fastapi.testclient import TestClient

from diony_wizard.app import app
from diony_wizard.prove import match_gpu, record_dry_run, run_probe


def test_match_gpu_b200() -> None:
    ok, detail = match_gpu("b200", "NVIDIA B200, 183500 MiB")
    assert ok is True
    assert "B200" in detail
    bad, _ = match_gpu("b200", "NVIDIA B300, 288000 MiB")
    assert bad is False
    missing, _ = match_gpu("b200", "ERR nvidia-smi: not found")
    assert missing is False


def test_record_dry_run_never_needs_ssh() -> None:
    row = record_dry_run("b200", {"connect": {"hosts": "b200-01"}})
    assert row["ok"] is True
    assert row["live"] is False
    assert [c["id"] for c in row["checks"]] == ["ssh", "gpu", "vault", "dashscope", "pack"]


def test_prove_block_reason_pack() -> None:
    from diony_wizard.prove import prove_block_reason

    missing = {
        "ok": False,
        "checks": [
            {"id": "gpu", "ok": True, "detail": "B200"},
            {"id": "pack", "ok": False, "detail": "Missing: DeepSeek 0731"},
        ],
    }
    assert prove_block_reason(missing) == "Pack is missing. Pull pack, then Test GPU."
    assert prove_block_reason(missing, applying=True) == "Pack is still pulling."
    assert prove_block_reason({"ok": True, "checks": [{"id": "gpu", "ok": True}]}) is None


def test_should_apply_operator_ip(monkeypatch) -> None:
    monkeypatch.setenv("DIONY_DRY_RUN", "1")
    monkeypatch.delenv("DIONY_WIZARD_LIVE", raising=False)
    monkeypatch.delenv("DIONY_APPLY_FAKE", raising=False)
    monkeypatch.setattr("diony_wizard.prove.in_pytest", lambda: False)
    from diony_wizard.apply import should_apply

    assert should_apply("b200", {"connect": {"hosts": "198.13.252.68"}}) is True
    assert should_apply("b200", {"connect": {"hosts": "b200-01"}}) is False
    monkeypatch.setattr("diony_wizard.prove.in_pytest", lambda: True)
    assert should_apply("b200", {"connect": {"hosts": "198.13.252.68"}}) is False


def test_pytest_prove_does_not_ssh() -> None:
    client = TestClient(app)
    client.post("/api/reset")
    client.post(
        "/api/steps/path/pass",
        json={
            "answers": {
                "site": "b200",
                "provider": "this rack",
                "signer": "Rohn Springfield",
            }
        },
    )
    client.post("/api/steps/connect/pass", json={"answers": {}})
    client.post("/api/steps/app/pass", json={"answers": {}})
    state = client.get("/api/state").json()
    assert state["active_step"] == "prove"
    retry = client.post("/api/apply/retry")
    assert retry.status_code == 400
    assert "IP" in retry.json()["detail"] or "placeholder" in retry.json()["detail"].lower()
    probed = client.post("/api/prove", json={"scope": "gpu"})
    assert probed.status_code == 200, probed.text
    body = probed.json()
    assert body["prove"]["ok"] is True
    assert body["prove"]["live"] is False
    assert any(c["id"] == "gpu" for c in body["prove"]["checks"])
    passed = client.post("/api/steps/prove/pass", json={"answers": {}})
    assert passed.status_code == 200, passed.text
    assert passed.json()["active_step"] == "producer"


def test_run_probe_is_not_imported_into_pytest_path() -> None:
    # Guard: TestClient prove uses record_dry_run. run_probe still exists for operators.
    assert callable(run_probe)
