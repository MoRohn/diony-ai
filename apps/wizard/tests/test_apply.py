import os
import subprocess
import sys

from fastapi.testclient import TestClient

from diony_wizard.app import app
from diony_wizard.prep import repo_root
from diony_wizard.apply import (
    apply_exception_text,
    apply_fail_text,
    apply_progress_text,
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
    assert "FunAudioLLM/CosyVoice2-0.5B" in script
    assert "/diony/vault/models/audio/CosyVoice2-0.5B" in script
    assert "FunAudioLLM/CosyVoice.git" in script
    assert "COSY_RUNTIME=/diony/vault/runtime/cosyvoice" in script
    assert 'COSY_ROOT="$COSY_RUNTIME/CosyVoice"' in script
    assert 'COSY_PY="$COSY_RUNTIME/venv/bin/python"' in script
    assert "--system-site-packages" in script
    assert "git clone --depth 1 --recursive https://github.com/FunAudioLLM/CosyVoice.git /opt/wan22/CosyVoice" not in script
    assert "requirements_s2v.txt" in script
    assert "COSYVOICE_OK" in script
    assert "from modelscope import snapshot_download" not in script
    assert "ThreadPoolExecutor" in script
    assert "DIONY_MODEL_PULL_WORKERS" in script
    assert "revision=revision" in script
    assert "max_workers=file_workers" in script
    assert "requirements_animate.txt" in script
    assert "diony-wan-animate-req.txt" in script
    assert "--no-build-isolation" in script
    assert "SAM-2" in script
    assert "_DIONY_WAN_RUNTIME=/diony/vault/runtime/wan" in script
    assert '_DIONY_WAN_PYTHON="$_DIONY_WAN_RUNTIME/venv/bin/python"' in script
    assert "DIONY_VOLUME_CAPACITY_GB" in script
    assert "DIONY_VOLUME_MIN_FREE_GB" in script
    assert "DIONY_MODEL_HOT_CACHE_GB" in script
    assert "DIONY_MODEL_HOT_CACHE_MIN_FREE_GB" in script


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


def test_apply_fail_text_summarizes_runtime_report_traceback() -> None:
    from types import SimpleNamespace

    traceback = """Traceback (most recent call last):
  File "/opt/diony-ai/apps/wan/src/diony_wan/runtime.py", line 128, in <module>
    raise SystemExit(main())
  File "/opt/diony-ai/apps/wan/src/diony_wan/runtime.py", line 122, in main
    _write_report(args.write, report)
  File "/opt/diony-ai/apps/wan/src/diony_wan/runtime.py", line 113, in _write_report
    temporary.replace(dest)
FileNotFoundError: [Errno 2] shared readiness temporary file was already moved
"""
    result = SimpleNamespace(
        ok=False,
        code=1,
        stdout="",
        stderr=traceback,
        text=traceback,
    )

    message = apply_fail_text(result)

    assert message.startswith("Wan runtime readiness report could not be finalized.")
    assert "Retry validation." in message
    assert "FileNotFoundError:" in message
    assert "Traceback" not in message
    assert "raise SystemExit" not in message


def test_remote_setup_runpod_vault_and_venv(monkeypatch) -> None:
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    script = remote_setup_script("/opt/diony-ai", "/workspace")
    assert "_diony_ensure_vault /workspace /diony/vault" in script
    assert "mount --bind" in script
    assert "ln -sfn" in script
    assert "MODEL_VOLUME_START" in script
    assert "MODEL_VOLUME_OK" in script
    assert "/diony/vault/models/$kind" in script
    assert 'rsync -a "${sync_filters[@]}" --' in script
    assert 'rsync -rlni --size-only "${sync_filters[@]}" --' in script
    assert "--exclude '*.incomplete' --exclude '.cache'" in script
    assert "ln -s \"$volume\" \"$legacy\"" in script
    assert script.find("_diony_ensure_vault /workspace /diony/vault") < script.find(
        "MODEL_VOLUME_START"
    )
    assert script.find("MODEL_VOLUME_OK") < script.find("HF_START")
    assert "python3 -m venv" in script
    assert "_diony_pip install" in script
    assert "--break-system-packages" in script
    producer = producer_only_script("/opt/diony-ai", "/workspace")
    assert "MODEL_VOLUME_START" in producer
    assert producer.find("_diony_ensure_vault /workspace /diony/vault") < producer.find(
        "MODEL_VOLUME_START"
    )
    assert "python3 -m venv" in producer
    assert "_diony_pip install" in producer
    assert "_diony_host_wan_pip" in producer
    assert "wan-host-overlay.txt" in producer
    assert "diony_wan/runtime.py" in producer
    assert "wan-host-runtime.json" in producer
    assert "ensure_optional_engine_lazy_imports" in producer
    assert "WAN_HOST_OVERLAY_REPAIR" in producer


def test_remote_setup_resumes_an_incomplete_cosyvoice_snapshot(monkeypatch) -> None:
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    script = remote_setup_script("/opt/diony-ai", "/workspace")

    assert "any(os.scandir(dest))" not in script
    assert "llm.pt" in script
    assert "flow.pt" in script
    assert "hift.pt" in script
    assert "speech_tokenizer_v2.onnx" in script
    assert "snapshot_download(repo_id=repo_id, revision=revision, local_dir=dest" in script


def test_apply_progress_text_exposes_remote_setup_stages() -> None:
    assert apply_progress_text("HF_PULL Wan-AI/Wan2.2-S2V-14B -> /models/wan") == (
        "Downloading Wan2.2-S2V-14B"
    )
    assert apply_progress_text("COSYVOICE_PULL FunAudioLLM/CosyVoice2-0.5B -> /diony/vault/models/audio") == (
        "Downloading CosyVoice2 voice model"
    )
    assert apply_progress_text("COSYVOICE_OK") == "CosyVoice2 voice model ready"
    assert apply_progress_text("MODEL_VOLUME_MIGRATE wan") == (
        "Moving existing wan models to the attached volume"
    )
    assert apply_progress_text("random dependency noise") == ""


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
        "/diony/vault/models/audio/CosyVoice2-0.5B",
        "/opt/wan22/.git",
        "/diony/vault/runtime/cosyvoice/CosyVoice",
        "/diony/vault/runtime/cosyvoice/venv/bin/python",
    ):
        assert path in script
    assert "DIONY_SITE_STATE" in script
    assert '/diony/vault/.cluster/wan-host-runtime.json' in script
    assert '"required_capabilities_ready"' in script
    assert '"schema_version"' in script
    assert '"checked_at"' in script
    assert 'from cosyvoice.cli.cosyvoice import CosyVoice2' not in script
    assert '"--require-capability"' not in script
    assert "subprocess.PIPE" not in script
    assert '"wan_runtime"' in script
    assert '"model_volume"' in script
    assert 'cosy_root / ".git"' in script
    assert '"cosy_runtime"' in script
    assert '"voice_backend"' in script
    assert 'volume_root = Path("/diony/vault/models").resolve()' in script
    assert '(Path("/models") / kind).resolve() == volume_root / kind' in script
    assert "cosy_required" in script
    assert "llm.pt" in script
    assert "speech_tokenizer_v2.onnx" in script
    assert '"snapshot_complete"' in script
    assert '"snapshot_pinned"' in script
    assert '"optional_engines"' in script
    assert ".incomplete" in script
    assert "huggingface" not in script.lower()


def test_apply_exception_text_humanizes_ssh_readiness_timeout() -> None:
    error = subprocess.TimeoutExpired(
        ["ssh", "-p", "49135", "root@198.13.252.71", "bash", "-s"],
        90,
    )

    message = apply_exception_text(error)

    assert "read-only readiness check" in message
    assert "90 seconds" in message
    assert "did not stop or restart Producer" in message
    assert "['ssh'" not in message
    assert "198.13.252.71" not in message


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


def test_prepared_site_defers_runtime_repair_while_producer_is_active(monkeypatch) -> None:
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
        raise AssertionError("Active Producer must not be restarted during Connect validation")

    monkeypatch.setattr(apply_mod, "exec_host", fake_exec)
    monkeypatch.setattr(apply_mod, "apply_local", lambda _host: True)
    apply_mod._run_apply("b200", ["localhost"], "/workspace", epoch)

    state = public_apply()
    assert state["ok"] is False
    assert len(commands) == 1
    assert "Diony did not interrupt Producer" in state["error"]


def test_prepared_site_repairs_host_runtime_when_producer_is_idle(monkeypatch) -> None:
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
                '"wan_runtime":false,"producer_installed":true,"producer_running":false}'
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


def test_enterprise_hf_token_is_always_used_and_never_echoed(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DIONY_DRY_RUN", "1")
    monkeypatch.setenv("DIONY_VAULT", str(tmp_path))
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    from diony_wizard.apply import hf_token, hf_token_set, load_hf_token
    from diony_wizard.enterprise import DIONY_ENTERPRISE_HF_TOKEN

    # No operator input: the backend token is the one on every apply.
    assert hf_token() == DIONY_ENTERPRISE_HF_TOKEN
    assert hf_token_set()
    assert f"export HF_TOKEN={DIONY_ENTERPRISE_HF_TOKEN}" in remote_setup_script("/opt/diony-ai", "/workspace")
    load_hf_token()
    assert os.environ["HF_TOKEN"] == DIONY_ENTERPRISE_HF_TOKEN
    assert os.environ["HUGGING_FACE_HUB_TOKEN"] == DIONY_ENTERPRISE_HF_TOKEN

    # Env override rotates the token without a code change.
    monkeypatch.setenv("DIONY_ENTERPRISE_HF_TOKEN", "hf_rotated")
    assert hf_token() == "hf_rotated"
    assert "export HF_TOKEN=hf_rotated" in remote_setup_script("/opt/diony-ai", "/workspace")
    monkeypatch.delenv("DIONY_ENTERPRISE_HF_TOKEN")

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
    # A pasted token is ignored and never stored or echoed.
    saved = client.post(
        "/api/steps/app/save",
        json={"answers": {"hf_token": "hf_secret_test_token"}},
    )
    assert saved.status_code == 200
    assert "hf_secret_test_token" not in saved.text
    assert DIONY_ENTERPRISE_HF_TOKEN not in saved.text
    assert saved.json()["hf_token"] is True
    assert not (tmp_path / ".cluster" / "hf.token").exists()
    assert "hf_token" not in saved.json()["workbook"].get("app", {})
    passed = client.post("/api/steps/app/pass", json={"answers": {}})
    assert passed.status_code == 200
    assert DIONY_ENTERPRISE_HF_TOKEN not in passed.text
    assert hf_token() == DIONY_ENTERPRISE_HF_TOKEN


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
    from diony_wizard.apply import hf_token_set

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
    assert site_lock_path().is_file()
    client = TestClient(app)
    res = client.post("/api/reset")
    assert res.status_code == 200
    assert read_site_lock() is None
    assert not site_lock_path().is_file()
    assert hf_token_set()


def test_apply_fail_text_names_the_stale_git_lock_not_the_download_bars() -> None:
    """The B200 run on 2026-08-25: pack finished, pip's sam-2 checkout hit index.lock."""
    from types import SimpleNamespace

    stderr = (
        "███▊| 438/444 [02:22<00:06,  1.13s/it]\n"
        "Fetching 444 files:  99%|█████████▉| 439/444 [02:22<00:05,  1.04s/it]\n"
        "Fetching 14 files:  93%|█████████▎| 13/14 [01:54<00:19, 19.58s/it][A\n"
        "Fetching 444 files: 100%|██████████| 444/444 [03:01<00:00,  2.44it/s]\n"
        "Cloning into '/opt/wan22'...\n"
        "  Running command git reset --hard -q 0e78a118995e66bb27d78518c4bd9a3e95b4e266\n"
        "  fatal: Unable to create '/workspace/runtime/wan/venv/src/sam-2/.git/index.lock': File exists.\n"
        "\n"
        "  Another git process seems to be running in this repository, e.g.\n"
        "  error: subprocess-exited-with-error\n"
        "error: subprocess-exited-with-error\n"
        "note: This error originates from a subprocess, and is likely not a problem with pip.\n"
    )
    stdout = "\n".join(
        [
            "HF_START",
            "HF_PULL Wan-AI/Wan2.2-Animate-14B -> /models/wan/Wan2.2-Animate-14B",
            "HF_DONE Wan-AI/Wan2.2-Animate-14B",
            "HF_OK",
            "WAN_HOST_OVERLAY_REPAIR",
        ]
    )
    message = apply_fail_text(SimpleNamespace(ok=False, code=1, stdout=stdout, stderr=stderr, text=stderr))

    assert message.startswith("Setup stopped at: Repairing the Wan host runtime.")
    assert "/workspace/runtime/wan/venv/src/sam-2/.git/index.lock" in message
    assert "Retry validation" in message
    assert "Fetching" not in message
    assert "%|" not in message
    assert len(message) < 300  # Studio shows the first 300 characters


def test_apply_fail_text_generic_error_leads_with_the_cause_line() -> None:
    from types import SimpleNamespace

    stderr = (
        "Fetching 32 files: 100%|██████████| 32/32 [10:01<00:00, 18.80s/it]\n"
        "ERROR: Could not install packages due to an OSError: [Errno 28] No space left on device\n"
    )
    message = apply_fail_text(SimpleNamespace(ok=False, code=1, stdout="HF_OK\nPACK_DEDUPE_START", stderr=stderr, text=stderr))
    assert message.startswith("Setup stopped at: Consolidating shared model assets. ERROR: Could not install")
    assert "No space left on device" in message
    assert "Fetching" not in message


def test_apply_fail_text_explains_busy_host_and_gated_download() -> None:
    from types import SimpleNamespace

    busy = "DIONY_APPLY_BUSY another Diony setup still holds /diony/vault/.cluster/apply.lock"
    message = apply_fail_text(SimpleNamespace(ok=False, code=75, stdout="", stderr=busy, text=busy))
    assert "Another Diony setup is still running" in message
    assert "DIONY_APPLY_BUSY" not in message

    gated = (
        "HF_FAIL deepseek-ai/DeepSeek-V4-Flash-0731: 403 Client Error: Forbidden (GatedRepoError)\n"
        "Traceback (most recent call last):\n  File \"<stdin>\", line 40\nGatedRepoError: 403\n"
    )
    message = apply_fail_text(SimpleNamespace(ok=False, code=1, stdout="HF_START", stderr=gated, text=gated))
    assert message.startswith("Model download failed for DeepSeek-V4-Flash-0731: 403")
    assert "Diony-Enterprise HF Token" in message
    assert "Traceback" not in message


def test_apply_progress_text_reports_file_counts_and_lock_repair() -> None:
    assert apply_progress_text("HF_PROGRESS Wan-AI/Wan2.2-Animate-14B 438/444") == (
        "Downloading Wan2.2-Animate-14B · 438/444 files"
    )
    assert apply_progress_text("WAN_GIT_LOCK_CLEARED /workspace/runtime/wan/venv/src/sam-2/.git/index.lock") == (
        "Clearing a stale git lock left by an interrupted install"
    )
    assert apply_progress_text("Fetching 444 files:  99%|█████████▉| 439/444 [02:22<00:05,  1.04s/it]") == ""


def test_remote_setup_script_is_quiet_locked_and_clears_stale_git_locks(monkeypatch) -> None:
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    script = remote_setup_script("/opt/diony-ai", "/workspace")

    assert "export HF_HUB_DISABLE_PROGRESS_BARS=1" in script
    assert "HF_PROGRESS {repo_id} {done}/{total}" in script
    assert "flock -n 9" in script
    assert "DIONY_APPLY_BUSY" in script
    assert script.find("exec 9>&-") < script.find("nohup env PATH")
    assert "_diony_clear_stale_git_locks()" in script
    assert "WAN_GIT_LOCK_CLEARED" in script
    assert script.find("WAN_HOST_OVERLAY_REPAIR\n") < script.find('_diony_clear_stale_git_locks "$(cd')
    assert script.find('_diony_clear_stale_git_locks "$(cd') < script.find("facebookresearch/sam2.git@")
    assert subprocess.run(["bash", "-n"], input=script, text=True, capture_output=True).returncode == 0


def test_reset_apply_terminates_the_streamed_session(monkeypatch) -> None:
    import diony_wizard.apply as apply_module

    calls: list[str] = []
    monkeypatch.setattr(apply_module, "terminate_streaming", lambda: calls.append("stop") or 1)
    reset_apply()
    assert calls == ["stop"]


def test_remote_setup_installs_cosyvoice_overlay_and_reports_import_failures(monkeypatch) -> None:
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    script = remote_setup_script("/opt/diony-ai", "/workspace")

    assert 'COSY_OVERLAY="$_diony_repo/infra/runtime/cosyvoice-overlay.txt"' in script
    assert "COSYVOICE_OVERLAY_MISSING" in script
    assert '-r /opt/wan22/requirements_s2v.txt -r "$COSY_OVERLAY"' in script
    assert "COSYVOICE_IMPORT_FAILED" in script
    assert script.count("_diony_cosyvoice_ready") >= 3
    assert '"transformers", "diffusers", "x_transformers"' in script
    overlay = (repo_root() / "infra" / "runtime" / "cosyvoice-overlay.txt").read_text()
    assert "transformers==" in overlay
    assert "diffusers==" in overlay
    assert "x-transformers==" in overlay
    assert "torch==" not in overlay  # never clobber the host CUDA torch


def test_apply_fail_text_summarizes_cosyvoice_import_traceback() -> None:
    """The B200 run on 2026-08-25 after the git-lock fix: transformers missing in the CosyVoice venv."""
    from types import SimpleNamespace

    traceback = (
        "Traceback (most recent call last):\n"
        '  File "<string>", line 1, in <module>\n'
        '  File "/diony/vault/runtime/cosyvoice/CosyVoice/cosyvoice/cli/cosyvoice.py", line 24, in <module>\n'
        "    from cosyvoice.utils.class_utils import get_model_type\n"
        '  File "/diony/vault/runtime/cosyvoice/CosyVoice/cosyvoice/llm/llm.py", line 24, in <module>\n'
        "    from transformers import Qwen2ForCausalLM\n"
        "ModuleNotFoundError: No module named 'transformers'\n"
    )
    stdout = "HF_OK\nCOSYVOICE_RUNTIME_INSTALL"
    message = apply_fail_text(SimpleNamespace(ok=False, code=1, stdout=stdout, stderr=traceback, text=traceback))
    assert message.startswith("Setup stopped at: Installing the CosyVoice2 runtime.")
    assert "No module named 'transformers'" in message
    assert "cosyvoice/llm/llm.py line 24" in message
    assert "Traceback" not in message
    assert len(message) < 300

    marked = traceback + "COSYVOICE_IMPORT_FAILED the CosyVoice2 venv cannot import cosyvoice\n"
    message = apply_fail_text(SimpleNamespace(ok=False, code=15, stdout=stdout, stderr=marked, text=marked))
    assert "The CosyVoice2 venv is missing the Python package 'transformers'" in message
    assert "cosyvoice-overlay.txt" in message
    assert len(message) < 300


def test_summarize_traceback_leads_with_the_exception() -> None:
    from diony_wizard.apply import summarize_traceback

    text = (
        "Traceback (most recent call last):\n"
        '  File "/opt/diony-ai/apps/wan/src/diony_wan/runner.py", line 40, in <module>\n'
        "    main()\n"
        "OSError: [Errno 28] No space left on device\n"
        "SOME_MARKER after the traceback\n"
    )
    assert summarize_traceback(text) == (
        "OSError: [Errno 28] No space left on device — raised in src/diony_wan/runner.py line 40."
    )


def test_setup_stage_tracker_reports_weighted_percentages() -> None:
    from diony_wizard.apply import apply_stage_event, setup_percent, setup_stage_plan

    plan = setup_stage_plan()
    assert [stage["id"] for stage in plan] == [
        "site", "sync", "storage", "python", "pack", "dedupe", "wan", "cosyvoice", "voiceqc", "producer",
    ]
    pack = next(stage for stage in plan if stage["id"] == "pack")
    assert {item["label"] for item in pack["items"]} >= {"Wan2.2-Animate-14B", "DeepSeek-V4-Flash-0731", "Real-ESRGAN"}
    assert setup_percent(plan) == 0

    assert apply_stage_event("HF_PROGRESS Wan-AI/Wan2.2-Animate-14B 438/444") == {
        "stage": "pack",
        "status": "active",
        "item": "Wan-AI/Wan2.2-Animate-14B",
        "item_status": "active",
        "done": 438,
        "total": 444,
        "detail": "Downloading Wan2.2-Animate-14B · 438/444 files",
    }
    assert apply_stage_event("WAN_HOST_PYTHON_OK") == {"stage": "wan", "status": "done", "detail": "Wan host runtime ready"}
    assert apply_stage_event("COSYVOICE_RUNTIME_START") == {
        "stage": "cosyvoice", "status": "active", "detail": "Preparing the CosyVoice2 runtime",
    }
    assert apply_stage_event("Fetching 444 files: 99%|███| 439/444") is None

    for stage in plan[:4]:
        stage["status"] = "done"
        stage["percent"] = 100
    pack["status"] = "active"
    pack["percent"] = 50
    assert setup_percent(plan) == round((2 + 3 + 3 + 3 + 55 * 0.5) / 97 * 100)
    for stage in plan:
        stage["status"] = "done"
    assert setup_percent(plan) == 100


def test_fake_apply_exposes_complete_progress(monkeypatch, tmp_path) -> None:
    from diony_wizard.apply import public_progress

    monkeypatch.setenv("DIONY_APPLY_FAKE", "1")
    monkeypatch.setenv("DIONY_VAULT", str(tmp_path))
    reset_apply()
    ensure_apply("b200", ["10.0.0.9"], nas="/workspace")
    progress = public_progress()
    assert progress["percent"] == 100
    assert progress["running"] is False
    assert all(stage["status"] == "done" for stage in progress["stages"])
    assert progress["stages"][4]["items"][0]["percent"] == 100


def test_remote_setup_script_emits_stage_markers(monkeypatch) -> None:
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    script = remote_setup_script("/opt/diony-ai", "/workspace")
    order = ["echo PY_RUNTIME_START", "echo HF_START", "echo WAN_RUNTIME_START", "echo COSYVOICE_RUNTIME_START", "echo PRODUCER_START", "echo APPLY_OK"]
    positions = [script.find(marker) for marker in order]
    assert all(pos >= 0 for pos in positions), positions
    assert positions == sorted(positions)


def test_remote_setup_pins_the_mouth_sync_models_after_cosyvoice() -> None:
    from diony_wizard.apply import (
        MOUTH_SYNC_MODELS,
        SETUP_STAGES,
        apply_progress_text,
        apply_stage_event,
        remote_setup_script,
    )

    script = remote_setup_script("/opt/diony-ai")
    assert script.find("COSYVOICE_OK") < script.find("VOICEQC_START") < script.find("PRODUCER_START")
    assert "infra/runtime/voice-qc-overlay.txt" in script
    assert "libEGL.so.1" in script and "apt-get install -y -q libegl1" in script
    assert "hasattr(cv2, 'FaceDetectorYN')" in script
    assert "/diony/vault/runtime/voice/models" in script
    for _url, name, sha256, size in MOUTH_SYNC_MODELS:
        assert name in script and sha256 in script and str(size) in script
        assert len(sha256) == 64
    assert "VOICEQC_PIN_MISMATCH" in script
    assert [stage for stage, _label, _weight in SETUP_STAGES].index("voiceqc") == (
        [stage for stage, _label, _weight in SETUP_STAGES].index("cosyvoice") + 1
    )
    assert sum(weight for _stage, _label, weight in SETUP_STAGES) == 97
    assert apply_progress_text("VOICEQC_OK") == "Mouth-sync QC models ready"
    event = apply_stage_event("VOICEQC_PULL face_landmarker.task")
    assert event and event["stage"] == "voiceqc" and event["status"] == "active"
