import os
import sys

from fastapi.testclient import TestClient

from diony_wizard.app import app
from diony_wizard.apply import (
    apply_fail_text,
    ensure_apply,
    hf_download_lines,
    producer_only_script,
    public_apply,
    remote_setup_script,
    reset_apply,
    site_readiness_script,
)


def test_hf_pack_includes_wan_and_sr(monkeypatch) -> None:
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    joined = "\n".join(hf_download_lines())
    assert "Wan-AI/Wan2.2-T2V-A14B" in joined
    assert "DeepSeek-V4-Flash-0731" in joined
    assert "diony_sr_v1" in joined
    assert "RealESRGAN_x2.pth" in joined
    script = remote_setup_script("/opt/diony-ai", "nfs://10.0.8.12/diony-vault")
    assert "diony-producer" in script
    assert "apps/finish" in script
    assert "mount -t nfs" in script
    assert "10.0.8.12:/diony-vault" in script
    assert "huggingface_hub" in script
    assert "snapshot_download" in script
    assert script.find("huggingface_hub") < script.find("snapshot_download")
    assert "huggingface-cli download" not in script
    assert "--use_prompt_extend" not in script
    assert "DASH_API_KEY" in script
    assert "diony-wan &" not in script
    assert "diony-finish &" not in script
    assert "python3 -m venv" in script
    assert "_diony_pip" in script
    assert "DIONY_PYTHON" in script
    assert "diony-system-torch.pth" in script
    assert "_diony_host_wan_pip" in script
    assert "wan-host-overlay.txt" in script
    assert "diony_wan/runtime.py" in script
    assert "wan-host-runtime.json" in script
    assert "ensure_optional_engine_lazy_imports" in script
    assert "WAN_HOST_PYTHON_WITH_TORCH_MISSING" in script
    assert "/usr/local/bin/python" in script
    assert "WAN_HOST_OVERLAY_REPAIR" in script
    assert script.find('"$overlay"') < script.find("/tmp/diony-wan-req.txt")
    assert "flash_attn" in script
    assert "ensure_attention_fallback" in script
    assert "|numpy)" in script or "flash_attn|numpy" in script
    assert "--break-system-packages" in script
    assert "python3 -m pip install -U" not in script
    assert "python3 -m pip install -e" not in script
    assert "install -U pip" not in script
    assert "PIP_OK" in script
    assert "HF_START" in script
    assert "HF_PULL" in script
    assert "PACK_DEDUPE_START" in script
    assert "PACK_DEDUPE_OK" in script
    assert "cmp -s" in script
    assert "ln -f" in script
    assert script.find('print("HF_OK"') < script.find("PACK_DEDUPE_START")
    assert "huggingface_hub already present" in script
    assert "iic/CosyVoice2-0.5B" in script
    assert "/models/audio/CosyVoice2-0.5B" in script
    assert "FunAudioLLM/CosyVoice.git" in script
    assert "requirements_s2v.txt" in script
    assert "COSYVOICE_OK" in script


def test_apply_fail_text_skips_pip_already_satisfied() -> None:
    from types import SimpleNamespace

    result = SimpleNamespace(
        ok=False,
        code=1,
        stdout="",
        stderr=(
            "Requirement already satisfied: huggingface_hub in "
            "/opt/diony-ai/.venv/lib/python3.12/site-packages (1.28.0)\n"
            "Requirement already satisfied: click<9.0.0,>=8.4.2"
        ),
        text="Requirement already satisfied: huggingface_hub",
    )
    msg = apply_fail_text(result)
    assert "already" in msg.lower()
    assert "Requirement already satisfied: click" not in msg
    assert "Retry Pull pack" in msg


def test_remote_setup_runpod_vault_and_venv() -> None:
    script = remote_setup_script("/opt/diony-ai", "/workspace")
    assert "_diony_ensure_vault /workspace /diony/vault" in script
    assert "mount --bind" in script
    assert "ln -sfn" in script
    assert "python3 -m venv" in script
    assert "_diony_pip install" in script
    assert "--break-system-packages" in script
    producer = producer_only_script("/opt/diony-ai", "/workspace")
    assert "python3 -m venv" in producer
    assert "_diony_pip install" in producer
    assert "_diony_host_wan_pip" in producer
    assert "wan-host-overlay.txt" in producer
    assert "diony_wan/runtime.py" in producer
    assert "wan-host-runtime.json" in producer
    assert "ensure_optional_engine_lazy_imports" in producer
    assert "WAN_HOST_OVERLAY_REPAIR" in producer


def test_site_readiness_probe_checks_the_complete_pack_before_any_pull() -> None:
    script = site_readiness_script("/opt/diony-ai")
    for path in (
        "/models/deepseek/DeepSeek-V4-Flash-0731",
        "/models/wan/Wan2.2-T2V-A14B",
        "/models/wan/Wan2.2-I2V-A14B",
        "/models/wan/Wan2.2-S2V-14B",
        "/models/wan/Wan2.2-Animate-14B",
        "/models/wan/Wan2.2-VACE-Fun-A14B",
        "/models/diony/diony_sr_v1",
        "/models/audio/CosyVoice2-0.5B",
        "/opt/wan22/.git",
        "/opt/wan22/CosyVoice/.git",
    ):
        assert path in script
    assert "DIONY_SITE_STATE" in script
    assert '"diony_wan" / "runtime.py"' in script
    assert '"wan_runtime"' in script
    assert "huggingface" not in script.lower()


def test_prepared_site_skips_the_model_pull(monkeypatch) -> None:
    from types import SimpleNamespace
    from diony_wizard import apply as apply_mod

    reset_apply()
    epoch = apply_mod._apply_epoch
    commands: list[str] = []

    def fake_exec(_host: str, command: str, **_kwargs):
        commands.append(command)
        if "DIONY_SITE_STATE" in command:
            state = (
                '{"prepared":true,"pack":true,"wan_repo":true,'
                '"wan_runtime":true,"producer_installed":true,"producer_running":true}'
            )
            return SimpleNamespace(ok=True, stdout=f"DIONY_SITE_STATE {state}\n", stderr="", code=0, text=state)
        raise AssertionError("A prepared Site must not run setup or pull commands")

    monkeypatch.setattr(apply_mod, "exec_host", fake_exec)
    monkeypatch.setattr(apply_mod, "apply_local", lambda _host: False)
    apply_mod._run_apply("b200", ["198.13.252.68"], "/workspace", epoch)

    state = public_apply()
    assert state["ok"] is True
    assert state["prepared"] is True
    assert state["pull_skipped"] is True
    assert "Model pull skipped" in "\n".join(state["log"])
    assert len(commands) == 1
    assert "HF_START" not in commands[0]


def test_prepared_site_repairs_host_runtime_without_model_pull(monkeypatch) -> None:
    from types import SimpleNamespace
    from diony_wizard import apply as apply_mod

    reset_apply()
    epoch = apply_mod._apply_epoch
    commands: list[str] = []

    def fake_exec(_host: str, command: str, **_kwargs):
        commands.append(command)
        if "DIONY_SITE_STATE" in command:
            state = (
                '{"prepared":true,"pack":true,"wan_repo":true,'
                '"wan_runtime":false,"producer_installed":true,"producer_running":true}'
            )
            return SimpleNamespace(ok=True, stdout=f"DIONY_SITE_STATE {state}\n", stderr="", code=0, text=state)
        assert "HF_START" not in command
        assert "_diony_host_wan_pip" in command
        return SimpleNamespace(ok=True, stdout="WAN_HOST_PYTHON_OK\nAPPLY_OK\n", stderr="", code=0, text="APPLY_OK")

    monkeypatch.setattr(apply_mod, "exec_host", fake_exec)
    monkeypatch.setattr(apply_mod, "apply_local", lambda _host: True)
    apply_mod._run_apply("b200", ["localhost"], "/workspace", epoch)

    state = public_apply()
    assert state["ok"] is True
    assert state["pull_skipped"] is True
    assert len(commands) == 2
    assert "Repair host runtime" in "\n".join(state["log"])


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
    assert body["active_step"] == "prove"
    assert body["dry_run"] is False
    ready = client.post("/api/steps/prove/pass", json={"answers": {}})
    assert ready.status_code == 200
    assert ready.json()["active_step"] == "producer"
    signed = client.post(
        "/api/steps/producer/pass",
        json={"answers": {"ready": "yes"}},
    )
    assert signed.status_code == 200
    from diony_contracts.cluster import read_site_lock

    lock = read_site_lock()
    assert lock is not None
    assert lock["vault"] == "/diony/vault"
    assert lock["compute_path"] == "dual"
    assert "spark-01" in str(lock.get("producer_href") or "") or lock.get("producer_href")


def test_apply_retry_is_live_only() -> None:
    client = TestClient(app)
    client.post("/api/reset")
    res = client.post("/api/apply/retry")
    assert res.status_code == 400


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
    assert body["active_step"] == "prove"


def test_app_continue_opens_ready_while_apply_runs(monkeypatch) -> None:
    monkeypatch.delenv("DIONY_DRY_RUN", raising=False)
    monkeypatch.setenv("DIONY_WIZARD_LIVE", "1")
    running = {
        "running": True,
        "ok": False,
        "error": "",
        "detail": "pulling",
        "log": ["pulling"],
        "host": "198.13.252.68",
        "live": True,
    }
    wizard_mod = sys.modules["diony_wizard.app"]
    monkeypatch.setattr(wizard_mod, "should_apply", lambda *_a, **_k: True)
    monkeypatch.setattr(wizard_mod, "ensure_apply", lambda *_a, **_k: running)
    monkeypatch.setattr(wizard_mod, "public_apply", lambda: running)
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
    res = client.post("/api/steps/app/pass", json={"answers": {}})
    assert res.status_code == 200
    body = res.json()
    assert body["active_step"] == "prove"
    assert body["apply"]["running"] is True


def test_ensure_apply_while_running_does_not_deadlock(monkeypatch) -> None:
    from diony_wizard import apply as apply_mod

    monkeypatch.setattr(apply_mod, "apply_allowed", lambda *_a, **_k: True)
    monkeypatch.setattr(apply_mod, "_apply_fake", lambda: False)
    reset_apply()
    with apply_mod._lock:
        apply_mod._job["running"] = True
        apply_mod._job["ok"] = False
        apply_mod._job["host"] = "198.13.252.68"
    try:
        out = ensure_apply("b200", ["198.13.252.68"])
        assert out["running"] is True
        assert out["ok"] is False
    finally:
        reset_apply()


def test_public_apply_includes_started_at() -> None:
    from diony_wizard import apply as apply_mod

    reset_apply()
    snap = public_apply()
    assert snap["started_at"] == ""
    assert snap["running"] is False
    with apply_mod._lock:
        apply_mod._job["running"] = True
        apply_mod._job["started_at"] = "2026-08-19T17:00:00+00:00"
        apply_mod._job["detail"] = "Sync repo"
    try:
        out = public_apply()
        assert out["running"] is True
        assert out["started_at"] == "2026-08-19T17:00:00+00:00"
        assert out["detail"] == "Sync repo"
    finally:
        reset_apply()


def test_connect_rerecord_after_pass_keeps_ready(monkeypatch) -> None:
    monkeypatch.setenv("DIONY_DRY_RUN", "1")
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
    assert client.get("/api/state").json()["active_step"] == "prove"
    again = client.post(
        "/api/steps/connect/pass",
        json={
            "answers": {
                "hosts": "10.9.8.7",
                "ssh_user": "root",
                "ssh_port": "30111",
                "nas_ip": "/workspace",
            }
        },
    )
    assert again.status_code == 200, again.text
    body = again.json()
    assert body["active_step"] == "prove"
    assert body["workbook"]["inventory"]["s1"] == "10.9.8.7"
    assert body["workbook"]["connect"]["ssh_port"] == "30111"


def test_hf_token_is_bound_and_not_echoed(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DIONY_DRY_RUN", "1")
    monkeypatch.setenv("DIONY_VAULT", str(tmp_path))
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    from diony_wizard.apply import hf_token_set

    try:
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
        saved = client.post(
            "/api/steps/app/save",
            json={"answers": {"hf_token": "hf_secret_test_token"}},
        )
        assert saved.status_code == 200
        assert "hf_secret_test_token" not in saved.text
        assert saved.json()["hf_token"] is True
        assert hf_token_set()
        assert (tmp_path / ".cluster" / "hf.token").is_file()
        passed = client.post("/api/steps/app/pass", json={"answers": {}})
        assert passed.status_code == 200
        assert "hf_secret_test_token" not in passed.text
    finally:
        os.environ.pop("HF_TOKEN", None)
        os.environ.pop("HUGGING_FACE_HUB_TOKEN", None)


def test_reset_clears_apply_and_ssh_env(monkeypatch) -> None:
    monkeypatch.setenv("DIONY_SSH_USER", "root")
    monkeypatch.setenv("DIONY_SSH_PORT", "28231")
    from diony_wizard import apply as apply_mod

    with apply_mod._lock:
        apply_mod._job["error"] = "198.13.252.68:28231 is not accepting SSH"
        apply_mod._job["host"] = "198.13.252.68"
        apply_mod._job["running"] = False
        apply_mod._job["log"] = ["FAIL leftover"]
    client = TestClient(app)
    res = client.post("/api/reset")
    assert res.status_code == 200
    body = res.json()
    assert body["active_step"] == "path"
    assert body["compute_path"] is None
    assert body["apply"]["error"] == ""
    assert body["apply"]["host"] == ""
    assert body["apply"]["log"] == []
    assert body["apply"]["running"] is False
    assert os.environ.get("DIONY_SSH_PORT") is None
    assert os.environ.get("DIONY_SSH_USER") is None


def test_stale_apply_does_not_write_after_reset() -> None:
    from diony_wizard import apply as apply_mod

    reset_apply()
    epoch = apply_mod._apply_epoch
    assert apply_mod._commit_job(
        epoch,
        running=False,
        ok=False,
        error="stale FAIL",
        detail="stale FAIL",
        host="198.13.252.68",
    )
    assert public_apply()["error"] == "stale FAIL"
    reset_apply()
    assert apply_mod._apply_epoch != epoch
    assert apply_mod._commit_job(epoch, running=False, ok=False, error="stale FAIL") is False
    apply_mod._log("FAIL leftover", epoch=epoch)
    snap = public_apply()
    assert snap["error"] == ""
    assert snap["host"] == ""
    assert snap["log"] == []


def test_reset_clears_local_site_lock_keeps_hf_token(tmp_path) -> None:
    from diony_contracts.cluster import read_site_lock, site_lock_path, write_site_lock
    from diony_wizard.apply import bind_hf_token, hf_token_path, hf_token_set

    write_site_lock(
        {
            "sku": "b200-v1",
            "compute_path": "b200",
            "compute": {
                "path": "b200",
                "profile": "b200-v1",
                "gpu_class": "B200",
                "exception_by": "Rohn Springfield",
            },
        }
    )
    bind_hf_token("hf_keep_across_reset")
    assert site_lock_path().is_file()
    client = TestClient(app)
    res = client.post("/api/reset")
    assert res.status_code == 200
    assert read_site_lock() is None
    assert not site_lock_path().is_file()
    assert hf_token_set()
    assert hf_token_path().is_file()
    assert "hf_keep_across_reset" in hf_token_path().read_text(encoding="utf-8")
