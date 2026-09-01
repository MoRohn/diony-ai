import pytest
from diony_producer.gpu import (
    _gpu_producer_start_script,
    gpu_block_copy,
    in_pytest,
    merge_connect,
    parse_generate_ps,
    parse_nvidia,
    routable_host,
    should_proxy,
    signed_gpu_site,
    useful_producer_lines,
    wizard_pack_on_gpu,
)


def test_gpu_producer_start_repairs_and_validates_wan_host_python() -> None:
    script = _gpu_producer_start_script("content-stamp-v1")
    assert "wan-host-overlay.txt" in script
    assert "diony_wan/runtime.py" in script
    assert "wan-host-runtime.json" in script
    assert "ensure_optional_engine_lazy_imports" in script
    assert "GPU_HOST_PYTHON_WITH_TORCH_MISSING" in script
    assert "/usr/local/bin/python" in script
    assert "/diony/vault/runtime/wan/venv/bin/python" in script
    assert "--system-site-packages" in script
    assert "/opt/diony-ai/.venv/bin/diony-producer" in script
    assert "GPU_RUNTIME_OVERLAY_REPAIR" in script
    assert "--require-capability s2v --require-capability animate" in script
    assert "/opt/wan22/requirements_s2v.txt" in script
    assert "/opt/wan22/requirements_animate.txt" in script
    assert "diony-wan-animate-req.txt" in script
    assert "--no-build-isolation" in script
    assert "SAM-2" in script
    assert "DIONY_VOLUME_CAPACITY_GB" in script
    assert "DIONY_VOLUME_MIN_FREE_GB" in script
    assert "DIONY_MODEL_HOT_CACHE_GB" in script
    assert "DIONY_MODEL_HOT_CACHE_MIN_FREE_GB" in script
    assert "/proc/$PRODUCER_PID/environ" in script
    assert 'grep -Fqx "DIONY_PYTHON=$DIONY_PYTHON"' in script
    assert "DIONY_CODE_STAMP=content-stamp-v1" in script
    assert 'grep -Fqx "DIONY_CODE_STAMP=$DIONY_CODE_STAMP"' in script
    assert script.find("_diony_existing_producer_ready") < script.find("GPU_PRODUCER_OK")
    assert 'OLD_PRODUCER_PID=$(_diony_8788_pid)' in script
    assert 'kill "$OLD_PRODUCER_PID"' in script
    assert "_diony_production_busy" in script
    assert "GPU_PRODUCER_BUSY_DEFERRED" in script
    assert script.find("_diony_production_busy") < script.find('kill "$OLD_PRODUCER_PID"')
    assert 'if _diony_existing_producer_ready; then' in script
    assert "GPU_PRODUCER_ENV_MISMATCH" in script
    # Wan2.2's own speech lane: the pack lives beside generate.py, never in a Diony venv.
    assert "DIONY_COSYVOICE_PYTHON" not in script
    assert "DIONY_VOICE_BACKEND_COMMAND" not in script
    assert "DIONY_FACE_DETECTOR_MODEL" not in script
    assert "/opt/wan22/CosyVoice/cosyvoice/cli/cosyvoice.py" in script
    assert "/opt/wan22/CosyVoice2-0.5B" in script
    # A model directory Studio App is still filling is not a speech pack yet.
    assert "_diony_wan_speech_pack_ready" in script
    for name in ("cosyvoice2.yaml", "llm.pt", "flow.pt", "hift.pt", "speech_tokenizer_v2.onnx"):
        assert name in script
    assert script.find("_diony_wan_speech_pack_ready() {") < script.find("if _diony_wan_speech_pack_ready; then")
    assert "DIONY_WAN_TTS_PROMPT_LIBRARY=/diony/vault/runtime/voice/prompt-library" in script
    assert 'DIONY_WAN_TTS_PROMPT_AUDIO="${DIONY_WAN_TTS_PROMPT_AUDIO:-}"' in script
    assert 'grep -Fqx "DIONY_WAN_TTS_PROMPT_LIBRARY=$DIONY_WAN_TTS_PROMPT_LIBRARY"' in script
    # The sound library and generated-cue adapter no longer hide behind the voice pack.
    assert script.find("DIONY_SOUND_LIBRARY=/diony/vault/runtime/audio/library") > 0
    assert script.find('"$RUNTIME_OVERLAY"') < script.find("/tmp/diony-wan-req.txt")
    assert script.find("_diony_wan_host_ready") < script.find("GPU_PRODUCER_OK")


def test_gpu_start_discovers_only_named_vault_validation_receipts() -> None:
    script = _gpu_producer_start_script()

    assert "/diony/vault/qualification/wan-regional-compile-validation.json" in script
    assert "/diony/vault/qualification/wan-resident-validation.json" in script
    assert "DIONY_WAN_RESIDENT=validated" in script
    assert "DIONY_WAN_RESIDENT_RECEIPT" in script
    assert "DIONY_WAN_KERNEL_MODE" in script
    assert "DIONY_WAN_RESIDENT_CANARY_PERCENT=10" in script
    assert "DIONY_WAN_KERNEL_MODE=regional_compile" in script
    assert "DIONY_WAN_KERNEL_MODE=eager" in script
    assert "/diony/vault/qualification/wan-cudnn-sdpa-validation.json" in script
    assert "attention_decision" in script
    assert "DIONY_WAN_ATTENTION_BACKEND=cudnn_sdpa" in script


def test_gpu_restart_falls_back_to_the_verified_8788_owner_without_fuser(monkeypatch) -> None:
    import diony_producer.gpu as gpu

    captured: dict[str, str] = {}

    def fake_run(command, **kwargs):
        captured["script"] = kwargs["input"]
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(gpu.subprocess, "run", fake_run)

    gpu._restart_gpu_producer_bind({"ssh_port": "28869", "ssh_user": "root", "host": "gpu"})

    assert "command -v ss" in captured["script"]
    assert "sport = :8788" in captured["script"]
    assert 'kill "$pid"' in captured["script"]
    assert "apps/producer/src/diony_producer/gpu.py" in gpu.GPU_STAMP_FILES


def test_gpu_start_allows_persistent_optional_runtime_repair(monkeypatch) -> None:
    import diony_producer.gpu as gpu

    observed: dict[str, int] = {}

    def fake_start(command, script, *, timeout):
        observed["timeout"] = timeout
        return 0, "GPU_PRODUCER_OK\n", ""

    monkeypatch.setattr(gpu, "_run_start_script", fake_start)

    gpu._start_gpu_producer({"ssh_port": "28869", "ssh_user": "root", "host": "gpu"})

    assert observed["timeout"] >= 600


def test_gpu_start_treats_active_production_restart_deferral_as_healthy(monkeypatch) -> None:
    import diony_producer.gpu as gpu

    monkeypatch.setattr(gpu, "_run_start_script", lambda c, s, *, timeout: (0, "GPU_PRODUCER_BUSY_DEFERRED\n", ""))
    gpu._set_error("stale error")

    gpu._start_gpu_producer({"ssh_port": "28869", "ssh_user": "root", "host": "gpu"})

    assert gpu.last_gpu_error() == ""


def test_gpu_code_stamp_hashes_content_not_package_mtime(tmp_path, monkeypatch) -> None:
    import os
    import diony_producer.gpu as gpu

    source = tmp_path / "farm.py"
    source.write_text("same production code\n")
    monkeypatch.setattr(gpu, "desk_repo", lambda: tmp_path)
    monkeypatch.setattr(gpu, "GPU_STAMP_FILES", ("farm.py",))

    first = gpu._code_stamp()
    os.utime(source, ns=(source.stat().st_atime_ns, source.stat().st_mtime_ns + 10_000_000))
    second = gpu._code_stamp()
    source.write_text("changed production code\n")

    assert first == second
    assert gpu._code_stamp() != first


def test_gpu_code_stamp_tracks_storage_and_runtime_contracts() -> None:
    import diony_producer.gpu as gpu

    assert "apps/wan/src/diony_wan/storage.py" in gpu.GPU_STAMP_FILES
    assert "apps/wan/src/diony_wan/runtime.py" in gpu.GPU_STAMP_FILES
    assert "apps/wan/src/diony_wan/resident_worker.py" in gpu.GPU_STAMP_FILES
    assert "apps/wan/src/diony_wan/validation.py" in gpu.GPU_STAMP_FILES


def test_gpu_remote_code_manifest_verifies_every_stamped_file(tmp_path, monkeypatch) -> None:
    import hashlib
    import diony_producer.gpu as gpu

    first = tmp_path / "first.py"
    second = tmp_path / "nested" / "second.py"
    second.parent.mkdir()
    first.write_text("first\n")
    second.write_text("second\n")
    observed = {}

    def fake_run(command, **kwargs):
        observed["command"] = command
        observed["input"] = kwargs["input"]
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(gpu, "GPU_STAMP_FILES", ("first.py", "nested/second.py"))
    monkeypatch.setattr(gpu.subprocess, "run", fake_run)

    assert gpu._remote_code_matches(
        {"ssh_port": "28869", "ssh_user": "root", "host": "gpu"}, tmp_path
    ) is True
    assert observed["command"][-3:] == ["sha256sum", "-c", "-"]
    assert (
        f"{hashlib.sha256(first.read_bytes()).hexdigest()}  /opt/diony-ai/first.py"
        in observed["input"]
    )
    assert (
        f"{hashlib.sha256(second.read_bytes()).hexdigest()}  /opt/diony-ai/nested/second.py"
        in observed["input"]
    )


def test_gpu_matching_stamp_does_not_hide_partial_remote_sync(tmp_path, monkeypatch) -> None:
    import diony_producer.gpu as gpu

    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return type("Result", (), {"returncode": 0, "stdout": "same-stamp\n", "stderr": ""})()

    monkeypatch.setattr(gpu, "in_pytest", lambda: False)
    monkeypatch.setattr(gpu, "_code_stamp", lambda: "same-stamp")
    monkeypatch.setattr(gpu, "desk_repo", lambda: tmp_path)
    monkeypatch.setattr(gpu, "_remote_code_matches", lambda _conn, _root: False)
    monkeypatch.setattr(gpu, "_remote_producer_busy", lambda _conn: True)
    monkeypatch.setattr(gpu.subprocess, "run", fake_run)
    monkeypatch.setattr(gpu, "_synced_stamp", "")

    gpu._sync_gpu_apps({"ssh_port": "28869", "ssh_user": "root", "host": "gpu"})

    assert len(calls) == 1
    assert gpu._synced_stamp == ""


def test_gpu_sync_defers_remote_replacement_while_production_is_working(tmp_path, monkeypatch) -> None:
    import diony_producer.gpu as gpu

    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return type("Result", (), {"returncode": 0, "stdout": "old-stamp\n", "stderr": ""})()

    monkeypatch.setattr(gpu, "in_pytest", lambda: False)
    monkeypatch.setattr(gpu, "_code_stamp", lambda: "new-stamp")
    monkeypatch.setattr(gpu, "desk_repo", lambda: tmp_path)
    monkeypatch.setattr(gpu, "_remote_producer_busy", lambda _conn: True)
    monkeypatch.setattr(gpu.subprocess, "run", fake_run)
    monkeypatch.setattr(gpu, "_synced_stamp", "")

    monkeypatch.setattr(gpu, "_sync_deferred_at", 0.0)

    assert gpu._sync_gpu_apps({"ssh_port": "28869", "ssh_user": "root", "host": "gpu"}) is False

    assert len(calls) == 1
    assert calls[0][-2:] == ["cat", "/diony/vault/.cluster/desk.stamp"]
    assert gpu._synced_stamp == ""
    assert gpu._sync_deferred_at > 0.0


def test_gpu_fast_path_waits_a_minute_before_retrying_a_deferred_sync(monkeypatch) -> None:
    import diony_producer.gpu as gpu

    conn = {"ssh_port": "28869", "ssh_user": "root", "host": "gpu"}
    synced: list[str] = []
    started: list[str] = []

    def deferred_sync(_conn):
        synced.append("sync")
        gpu._sync_deferred_at = gpu.time.time()
        return False

    monkeypatch.setattr(gpu, "should_proxy", lambda: True)
    monkeypatch.setattr(gpu, "sync_connect_from_wizard", lambda: None)
    monkeypatch.setattr(gpu, "connect_from_lock", lambda: dict(conn))
    monkeypatch.setattr(gpu, "apply_ssh_env", lambda _conn: None)
    monkeypatch.setattr(gpu, "_port_open", lambda _port: True)
    monkeypatch.setattr(gpu, "_tunnel_key", "")
    monkeypatch.setattr(gpu, "_code_stamp", lambda: "new-stamp")
    monkeypatch.setattr(gpu, "_synced_stamp", "")
    monkeypatch.setattr(gpu, "_sync_deferred_at", 0.0)
    monkeypatch.setattr(gpu, "_probe_ssh", lambda _conn: True)
    monkeypatch.setattr(gpu, "persist_connect", lambda _conn: None)
    monkeypatch.setattr(gpu, "_sync_gpu_apps", deferred_sync)
    monkeypatch.setattr(gpu, "_start_gpu_producer", lambda _conn: started.append("start"))
    monkeypatch.setattr(
        gpu.httpx, "get", lambda *_args, **_kwargs: type("Res", (), {"status_code": 200})()
    )

    assert gpu.ensure_gpu_producer() == gpu.gpu_producer_base()
    assert synced == ["sync"]
    assert started == [], "a deferred push must not run the GPU start script"

    assert gpu.ensure_gpu_producer() == gpu.gpu_producer_base()
    assert synced == ["sync"], "the deferred push is retried once a minute, not every tick"

    gpu._sync_deferred_at = 0.0
    monkeypatch.setattr(gpu, "_sync_gpu_apps", lambda _conn: (synced.append("sync"), True)[1])
    assert gpu.ensure_gpu_producer() == gpu.gpu_producer_base()
    assert synced == ["sync", "sync"]
    assert started == ["start"], "a landed push restarts the GPU Producer exactly once"


def test_gpu_remote_busy_boundary_includes_mix(monkeypatch) -> None:
    import diony_producer.gpu as gpu

    captured: dict[str, str] = {}

    def fake_run(_command, **kwargs):
        captured["script"] = kwargs["input"]
        return type("Result", (), {"returncode": 0, "stdout": "busy\n", "stderr": ""})()

    monkeypatch.setattr(gpu.subprocess, "run", fake_run)

    assert gpu._remote_producer_busy(
        {"ssh_port": "28869", "ssh_user": "root", "host": "gpu"}
    ) is True
    assert "mix=data.get('mix')" in captured["script"]
    assert "mix.get('busy')" in captured["script"]
    assert 'if [ -z "$payload" ]; then\n  echo busy' in captured["script"]


def test_gpu_remote_busy_boundary_allows_a_pending_stop_keep_hotfix(monkeypatch) -> None:
    import diony_producer.gpu as gpu

    captured: dict[str, str] = {}

    def fake_run(_command, **kwargs):
        captured["script"] = kwargs["input"]
        return type("Result", (), {"returncode": 0, "stdout": "idle\n", "stderr": ""})()

    monkeypatch.setattr(gpu.subprocess, "run", fake_run)

    assert gpu._remote_producer_busy(
        {"ssh_port": "28869", "ssh_user": "root", "host": "gpu"}
    ) is False
    script = captured["script"]
    assert "/api/project" in script
    assert "production_pause" in script
    assert "stop_keep" in script
    assert script.index("stop_keep") < script.index("pgrep -f '[g]enerate.py.*--task'")


def test_gpu_sync_writes_content_stamp_through_stdin_script(tmp_path, monkeypatch) -> None:
    import diony_producer.gpu as gpu

    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return type("Result", (), {"returncode": 0, "stdout": "old-stamp\n", "stderr": ""})()

    monkeypatch.setattr(gpu, "in_pytest", lambda: False)
    monkeypatch.setattr(gpu, "_code_stamp", lambda: "new-content-stamp")
    monkeypatch.setattr(gpu, "desk_repo", lambda: tmp_path)
    monkeypatch.setattr(gpu, "GPU_SYNC_TREES", ())
    monkeypatch.setattr(gpu, "_remote_code_matches", lambda _conn, _root: True)
    monkeypatch.setattr(gpu, "_remote_producer_busy", lambda _conn: False)
    monkeypatch.setattr(gpu, "_restart_gpu_producer_bind", lambda _conn: None)
    monkeypatch.setattr(gpu.subprocess, "run", fake_run)
    monkeypatch.setattr(gpu, "_synced_stamp", "")

    gpu._sync_gpu_apps({"ssh_port": "28869", "ssh_user": "root", "host": "gpu"})

    command, kwargs = calls[-1]
    assert command[-2:] == ["bash", "-s"]
    assert "new-content-stamp" in kwargs["input"]
    assert "/diony/vault/.cluster/desk.stamp" in kwargs["input"]
    assert gpu._synced_stamp == "new-content-stamp"


def test_pytest_never_proxies_to_gpu() -> None:
    assert in_pytest() is True
    assert should_proxy() is False


def test_placeholder_hosts_are_not_ssh_targets() -> None:
    assert routable_host("b200-01") is False
    assert routable_host("spark-01") is False
    assert routable_host("198.13.252.68") is True


def test_wizard_connect_wins_stale_lock() -> None:
    lock = {
        "compute_path": "b200",
        "connect": {
            "host": "1.1.1.1",
            "ssh_port": "1111",
            "ssh_user": "root",
            "vault": "/workspace",
        },
        "nodes": ["1.1.1.1"],
    }
    wiz = {"hosts": "9.9.9.9", "ssh_port": "2222", "ssh_user": "root", "nas_ip": "/workspace"}
    row = merge_connect(lock, wiz)
    assert row["host"] == "9.9.9.9"
    assert row["ssh_port"] == "2222"
    assert row["ssh_user"] == "root"


def test_empty_wizard_does_not_wipe_signed_connect() -> None:
    lock = {
        "compute_path": "b200",
        "connect": {"host": "1.1.1.1", "ssh_port": "1111", "ssh_user": "root"},
        "nodes": ["1.1.1.1"],
    }
    row = merge_connect(lock, {})
    assert row["host"] == "1.1.1.1"
    assert row["ssh_port"] == "1111"


def test_gpu_block_copy_names_wizard_connect_after_pod_restart(monkeypatch) -> None:
    import diony_producer.gpu as gpu

    monkeypatch.setattr(
        gpu, "connect_from_lock", lambda lock=None: {"host": "10.0.0.8", "ssh_port": "40123"}
    )
    monkeypatch.setattr(gpu, "last_gpu_error", lambda: "")
    monkeypatch.setattr(gpu, "read_site_lock", lambda: {"sku": "b200-v1"})
    text = gpu_block_copy()
    assert "10.0.0.8:40123" in text
    assert "Studio Connect" in text
    assert "pod restart" in text


def test_signed_gpu_site_reads_lock(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DIONY_VAULT", str(tmp_path))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path))
    from diony_contracts.cluster import write_site_lock

    write_site_lock(
        {
            "sku": "b200-v1",
            "compute_path": "b200",
            "gpu_ready": True,
            "compute": {
                "path": "b200",
                "profile": "b200-v1",
                "gpu_class": "1×B200",
                "exception_by": "Rohn",
            },
        }
    )
    assert signed_gpu_site() is True
    assert wizard_pack_on_gpu() is True
    assert should_proxy() is False


def test_access_logs_are_not_activity() -> None:
    blob = """
INFO:     127.0.0.1:51080 - "GET /api/farm HTTP/1.1" 200 OK
INFO:     127.0.0.1:51085 - "GET /api/project HTTP/1.1" 200 OK
INFO:     Started server process [61203]
Traceback (most recent call last):
ModuleNotFoundError: No module named 'dashscope'
"""
    lines = useful_producer_lines(blob)
    joined = "\n".join(lines)
    assert "GET /api/farm" not in joined
    assert "dashscope" in joined
    assert "Started server process" not in joined


def test_nvidia_heartbeat_is_readable() -> None:
    row = parse_nvidia("NVIDIA B200, 0 %, 4 MiB, 183359 MiB")
    assert row["name"] == "B200"
    assert row["util"] == 0
    assert "B200" in row["heartbeat"]
    assert "0%" in row["heartbeat"]
    assert "4 / 183359 MiB" in row["heartbeat"]
    gen = parse_generate_ps(
        "04:12 /usr/bin/python3 /opt/wan22/generate.py --save_file /diony/vault/takes/sh-010-t1/negative_720.mp4"
    )
    assert "sh-010-t1" in gen
    assert "04:12" in gen


def test_gpu_monitor_reads_the_local_activity_journal_on_the_gpu(tmp_path, monkeypatch) -> None:
    import json
    import diony_producer.gpu as gpu

    journal = tmp_path / ".cluster" / "activity.jsonl"
    journal.parent.mkdir(parents=True)
    journal.write_text(
        json.dumps(
            {
                "at": "15:41:06",
                "src": "mix",
                "level": "info",
                "text": "Mix measured and verified",
                "shot_id": "",
            }
        )
        + "\n"
    )
    monkeypatch.setenv("DIONY_VAULT", str(tmp_path))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path))
    monkeypatch.setattr(gpu, "in_pytest", lambda: False)
    monkeypatch.setattr(gpu, "should_proxy", lambda: False)

    row = gpu.gpu_monitor()

    assert row["journal"] == [
        {
            "at": "15:41:06",
            "src": "mix",
            "level": "info",
            "text": "Mix measured and verified",
            "shot_id": "",
        }
    ]


@pytest.fixture(autouse=True)
def _clean_launch_state():
    import diony_producer.gpu as gpu

    with gpu._launch_lock:
        gpu._launch.update(
            active=False,
            phase="",
            stage="",
            stamp="",
            since=0.0,
            updated=0.0,
            finished_at=0.0,
            outcome="",
        )
    yield
    with gpu._launch_lock:
        gpu._launch.update(
            active=False,
            phase="",
            stage="",
            stamp="",
            since=0.0,
            updated=0.0,
            finished_at=0.0,
            outcome="",
        )


def _fake_conn() -> dict:
    return {"ssh_port": "27168", "ssh_user": "root", "host": "198.13.252.73"}


def test_gpu_start_treats_a_starting_producer_as_healthy_and_arms_the_grace_window(monkeypatch) -> None:
    import diony_producer.gpu as gpu

    monkeypatch.setattr(gpu, "_run_start_script", lambda c, s, *, timeout: (0, "GPU_PRODUCER_STARTING\n", ""))
    monkeypatch.setattr(gpu, "_last_producer_start_at", 0.0)
    gpu._set_error("stale error")
    gpu._start_gpu_producer(_fake_conn())
    assert gpu.last_gpu_error() == ""
    assert gpu._producer_starting() is True


def test_gpu_start_script_never_kills_a_young_producer_on_the_current_stamp() -> None:
    import diony_producer.gpu as gpu

    script = gpu._gpu_producer_start_script("stamp-abc")
    assert "_diony_starting_producer" in script
    assert script.index("GPU_PRODUCER_STARTING") < script.index('kill "$OLD_PRODUCER_PID"')
    assert 'grep -Fqx "DIONY_CODE_STAMP=$DIONY_CODE_STAMP"' in script
    assert '"$age" -lt 300' in script


def test_gpu_fast_path_keeps_the_tunnel_while_the_producer_starts(monkeypatch) -> None:
    """2026-08-27: a silent :8788 during the Producer's own launch tore the tunnel down and
    re-ran the start script, which killed the launching process — every mutation got 503."""
    import httpx

    import diony_producer.gpu as gpu

    monkeypatch.setattr(gpu, "should_proxy", lambda: True)
    monkeypatch.setattr(gpu, "sync_connect_from_wizard", lambda: None)
    monkeypatch.setattr(gpu, "connect_from_lock", lambda *_a, **_k: _fake_conn())
    monkeypatch.setattr(gpu, "apply_ssh_env", lambda *_a, **_k: None)
    monkeypatch.setattr(gpu, "_port_open", lambda _port: True)
    monkeypatch.setattr(gpu, "_tunnel_key", "")
    monkeypatch.setattr(gpu, "_tunnel", None)
    monkeypatch.setattr(gpu, "_last_producer_start_at", gpu.time.time() - 30)
    monkeypatch.setattr(gpu, "_remote_producer_alive", lambda _c: True)

    def unreachable(*_a, **_k):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(gpu.httpx, "get", unreachable)
    torn_down: list[str] = []
    monkeypatch.setattr(gpu, "_free_local_tunnel_port", lambda: torn_down.append("freed"))
    monkeypatch.setattr(gpu, "_start_gpu_producer", lambda _c: torn_down.append("started"))
    monkeypatch.setattr(gpu, "_probe_ssh", lambda _c: torn_down.append("probed") or True)

    assert gpu.ensure_gpu_producer() is None
    assert torn_down == [], "a starting Producer is left alone: no teardown, no second start script"
    assert "starting" in gpu.last_gpu_error()

    # Past the grace window the old recovery path is back.
    monkeypatch.setattr(gpu, "_last_producer_start_at", gpu.time.time() - 3600)
    monkeypatch.setattr(gpu, "_ensure_tunnel", lambda _c: False)
    monkeypatch.setattr(gpu, "_sync_gpu_apps", lambda _c: True)
    monkeypatch.setattr(gpu, "persist_connect", lambda _c: None)
    assert gpu.ensure_gpu_producer() is None
    assert torn_down == ["freed", "probed", "started"]


def test_gpu_ensure_runs_one_start_script_at_a_time(monkeypatch) -> None:
    import diony_producer.gpu as gpu

    monkeypatch.setattr(gpu, "should_proxy", lambda: True)
    monkeypatch.setattr(gpu, "sync_connect_from_wizard", lambda: None)
    monkeypatch.setattr(gpu, "connect_from_lock", lambda *_a, **_k: _fake_conn())
    monkeypatch.setattr(gpu, "apply_ssh_env", lambda *_a, **_k: None)
    monkeypatch.setattr(gpu, "_port_open", lambda _port: False)
    monkeypatch.setattr(gpu, "_tunnel_key", "")
    calls: list[str] = []
    monkeypatch.setattr(gpu, "_probe_ssh", lambda _c: calls.append("probe") or True)
    monkeypatch.setattr(gpu, "_start_gpu_producer", lambda _c: calls.append("start"))
    assert gpu._ensure_lock.acquire(blocking=False)
    try:
        assert gpu.ensure_gpu_producer() is None
    finally:
        gpu._ensure_lock.release()
    assert calls == [], "a concurrent caller never starts a second start script"


def test_gpu_grace_never_covers_a_dead_producer(monkeypatch) -> None:
    """A killed Producer inside the grace window is relaunched, not waited on for five minutes."""
    import httpx

    import diony_producer.gpu as gpu

    monkeypatch.setattr(gpu, "should_proxy", lambda: True)
    monkeypatch.setattr(gpu, "sync_connect_from_wizard", lambda: None)
    monkeypatch.setattr(gpu, "connect_from_lock", lambda *_a, **_k: _fake_conn())
    monkeypatch.setattr(gpu, "apply_ssh_env", lambda *_a, **_k: None)
    monkeypatch.setattr(gpu, "_port_open", lambda _port: True)
    monkeypatch.setattr(gpu, "_tunnel_key", "")
    monkeypatch.setattr(gpu, "_tunnel", None)
    monkeypatch.setattr(gpu, "_last_producer_start_at", gpu.time.time() - 30)
    monkeypatch.setattr(gpu, "_remote_producer_alive", lambda _c: False)

    def unreachable(*_a, **_k):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(gpu.httpx, "get", unreachable)
    steps: list[str] = []
    monkeypatch.setattr(gpu, "_free_local_tunnel_port", lambda: steps.append("freed"))
    monkeypatch.setattr(gpu, "_probe_ssh", lambda _c: steps.append("probed") or True)
    monkeypatch.setattr(gpu, "persist_connect", lambda _c: None)
    monkeypatch.setattr(gpu, "_sync_gpu_apps", lambda _c: steps.append("synced") or True)
    monkeypatch.setattr(gpu, "_start_gpu_producer", lambda _c: steps.append("started"))
    monkeypatch.setattr(gpu, "_ensure_tunnel", lambda _c: False)
    assert gpu.ensure_gpu_producer() is None
    assert steps == ["freed", "probed", "synced", "started"]


def test_gpu_alive_probe_is_cached_and_fails_safe(monkeypatch) -> None:
    import diony_producer.gpu as gpu

    calls: list[int] = []

    def fake_run(command, **kwargs):
        calls.append(1)
        return type("Result", (), {"returncode": 0, "stdout": "1234\n", "stderr": ""})()

    monkeypatch.setattr(gpu.subprocess, "run", fake_run)
    monkeypatch.setattr(gpu, "_alive_probe", (0.0, False))
    assert gpu._remote_producer_alive(_fake_conn()) is True
    assert gpu._remote_producer_alive(_fake_conn()) is True and len(calls) == 1, "cached"

    def broken(command, **kwargs):
        raise OSError("ssh missing")

    monkeypatch.setattr(gpu.subprocess, "run", broken)
    monkeypatch.setattr(gpu, "_alive_probe", (0.0, False))
    assert gpu._remote_producer_alive(_fake_conn()) is True, "an unreachable line is not a dead Producer"


def test_gpu_start_script_treats_a_silent_alive_producer_as_busy() -> None:
    """2026-08-27: a Producer loading CosyVoice missed a 2 s curl and was killed mid-run."""
    import diony_producer.gpu as gpu

    script = gpu._gpu_producer_start_script("stamp-abc")
    body = script[script.index("_diony_production_busy()"):script.index("if _diony_existing_producer_ready")]
    assert '2>/dev/null) || return 0' in body, "a failed status call fails closed (busy)"
    assert '[ -n "$payload" ] || return 0' in body
    assert '"status"[[:space:]]*:[[:space:]]*"shooting"' in body, "a shooting project is busy even between renders"
    assert "--max-time 2 " not in body


def _reset_line_state(monkeypatch) -> None:
    import diony_producer.gpu as gpu

    monkeypatch.setattr(gpu, "_ssh_ok_at", 0.0)
    monkeypatch.setattr(gpu, "_ssh_fail_at", 0.0)
    monkeypatch.setattr(gpu, "_ssh_fail_key", "")
    monkeypatch.setattr(gpu, "_last_producer_start_at", 0.0)
    gpu._launch_end("", "")
    gpu._launch.update(outcome="", stage="", stamp="", finished_at=0.0)


def test_block_copy_reports_the_desk_relaunch_instead_of_a_pod_restart(
    monkeypatch,
) -> None:
    import diony_producer.gpu as gpu

    _reset_line_state(monkeypatch)
    monkeypatch.setattr(
        gpu,
        "connect_from_lock",
        lambda lock=None: {"host": "10.0.0.8", "ssh_port": "40123"},
    )
    monkeypatch.setattr(gpu, "last_gpu_error", lambda: "[Errno 61] Connection refused")
    monkeypatch.setattr(gpu, "read_site_lock", lambda: {"sku": "b200-v1"})
    gpu._launch_begin("start", "running the runtime check on the GPU", "9db3a0e1ff00")
    gpu._launch_marker("GPU_RUNTIME_OVERLAY_REPAIR\n")
    text = gpu_block_copy()
    assert "Updating GPU Producer to build 9db3a0e1" in text
    assert "repairing Wan2.2 host Python dependencies" in text
    assert "pod restart" not in text
    assert "Cannot reach" not in text
    state = gpu.gpu_line_state(live=False)
    assert state["producer"] == "updating"
    assert state["beat"] == "Updating…"
    assert state["ssh"] == "unknown"

    gpu._launch_marker("GPU_PRODUCER_LAUNCHED\n")
    assert "waiting for it to answer on :8788" in gpu_block_copy()
    gpu._launch_end("ok", "GPU Producer answered")
    launch = gpu.gpu_launch_state()
    assert launch["active"] is False
    assert launch["outcome"] == "ok"
    assert launch["label"] == "GPU Producer build 9db3a0e1 answered"


def test_block_copy_keeps_the_pod_restart_hint_for_a_dead_ssh_line(monkeypatch) -> None:
    import time

    import diony_producer.gpu as gpu

    _reset_line_state(monkeypatch)
    monkeypatch.setattr(
        gpu,
        "connect_from_lock",
        lambda lock=None: {"host": "10.0.0.8", "ssh_port": "40123"},
    )
    monkeypatch.setattr(gpu, "read_site_lock", lambda: {"sku": "b200-v1"})
    monkeypatch.setattr(gpu, "_ssh_fail_key", "10.0.0.8:40123")
    monkeypatch.setattr(gpu, "_ssh_fail_at", time.time())
    monkeypatch.setattr(gpu, "_last_producer_start_at", time.time())
    monkeypatch.setattr(
        gpu,
        "last_gpu_error",
        lambda: "SSH 10.0.0.8:40123 refused. After a pod restart the SSH over exposed TCP IP and -p change. Save the new line on Studio Connect, then Produce.",
    )
    text = gpu_block_copy()
    assert "Cannot reach Producer on 10.0.0.8:40123" in text
    assert "pod restart" in text
    assert "starting" not in text.lower()
    state = gpu.gpu_line_state(live=False)
    assert state["ssh"] == "down"
    assert state["producer"] == "unreachable"
    assert state["beat"] == "Connect required"


def test_block_copy_says_the_line_is_up_when_only_the_producer_is_silent(
    monkeypatch,
) -> None:
    import time

    import diony_producer.gpu as gpu

    _reset_line_state(monkeypatch)
    monkeypatch.setattr(
        gpu,
        "connect_from_lock",
        lambda lock=None: {"host": "10.0.0.8", "ssh_port": "40123"},
    )
    monkeypatch.setattr(gpu, "read_site_lock", lambda: {"sku": "b200-v1"})
    monkeypatch.setattr(gpu, "_ssh_ok_at", time.time())
    monkeypatch.setattr(gpu, "last_gpu_error", lambda: "All connection attempts failed")
    text = gpu_block_copy()
    assert "SSH line to 10.0.0.8:40123 is up" in text
    assert "pod restart" not in text
    state = gpu.gpu_line_state(live=False)
    assert state["ssh"] == "up"
    assert state["producer"] == "down"
    assert state["beat"] == "Reconnecting…"


def test_start_script_markers_stream_into_the_launch_state(monkeypatch) -> None:
    import diony_producer.gpu as gpu

    _reset_line_state(monkeypatch)
    monkeypatch.setattr(gpu, "_code_stamp", lambda: "abcdef0123456789")
    stages: list[str] = []

    def fake_start(command, script, *, timeout):
        for marker in (
            "GPU_STAGE_RUNTIME_CHECK",
            "GPU_MONTAGE_OVERLAY_REPAIR",
            "GPU_PRODUCER_LAUNCHED",
            "GPU_PRODUCER_OK",
        ):
            gpu._launch_marker(marker + "\n")
            stages.append(gpu.gpu_launch_state()["stage"])
        return (
            0,
            "GPU_MONTAGE_OVERLAY_REPAIR\nGPU_PRODUCER_LAUNCHED\nGPU_PRODUCER_OK\n",
            "",
        )

    monkeypatch.setattr(gpu, "_run_start_script", fake_start)
    gpu._start_gpu_producer(_fake_conn())
    assert stages == [
        "running the Wan2.2 host runtime check (about three minutes)",
        "installing the montage intelligence overlay",
        "GPU Producer launched; waiting for it to answer on :8788",
        "GPU Producer answered",
    ]
    launch = gpu.gpu_launch_state()
    assert launch["active"] is False
    assert launch["outcome"] == "ok"
    assert launch["stamp"] == "abcdef01"
    assert gpu._producer_starting() is True


def test_start_script_failure_lands_in_the_launch_state(monkeypatch) -> None:
    import diony_producer.gpu as gpu

    _reset_line_state(monkeypatch)
    monkeypatch.setattr(
        gpu,
        "_run_start_script",
        lambda c, s, *, timeout: (13, "GPU_RUNTIME_MISSING\n", ""),
    )
    gpu._start_gpu_producer(_fake_conn())
    launch = gpu.gpu_launch_state()
    assert launch["active"] is False
    assert launch["outcome"] == "failed"
    assert "could not be repaired" in launch["label"]
    assert "GPU Producer update failed" in gpu_block_copy()


def test_run_start_script_streams_markers_from_a_real_process(monkeypatch) -> None:
    import sys

    import diony_producer.gpu as gpu

    _reset_line_state(monkeypatch)
    seen: list[str] = []
    monkeypatch.setattr(gpu, "_launch_marker", lambda line: seen.append(line.strip()))
    code, out, err = gpu._run_start_script(
        [
            sys.executable,
            "-c",
            "import sys; print(sys.stdin.read().strip()); print('GPU_PRODUCER_OK'); sys.stderr.write('warn\\n')",
        ],
        "GPU_PRODUCER_LAUNCHED\n",
        timeout=30,
    )
    assert code == 0
    assert seen == ["GPU_PRODUCER_LAUNCHED", "GPU_PRODUCER_OK"]
    assert "GPU_PRODUCER_OK" in out
    assert err.strip() == "warn"


def test_gpu_monitor_still_samples_when_only_the_tunnel_is_refused(monkeypatch) -> None:
    import diony_producer.gpu as gpu

    _reset_line_state(monkeypatch)
    monkeypatch.setattr(gpu, "in_pytest", lambda: False)
    monkeypatch.setattr(gpu, "should_proxy", lambda: True)
    monkeypatch.setattr(gpu, "last_gpu_error", lambda: "[Errno 61] Connection refused")
    monkeypatch.setattr(
        gpu,
        "connect_from_lock",
        lambda lock=None: {"host": "10.0.0.8", "ssh_port": "40123", "ssh_user": "root"},
    )
    monkeypatch.setattr(gpu, "routable_host", lambda host: True)
    monkeypatch.setattr(gpu, "_MONITOR_CACHE", {"at": 0.0, "row": None})

    def fake_run(command, **kwargs):
        return type(
            "Result",
            (),
            {
                "returncode": 0,
                "stdout": "NVIDIA B200, 0 %, 0 MiB, 183359 MiB\n---PS---\n---JOURNAL---\n---LOG---\n",
                "stderr": "",
            },
        )()

    monkeypatch.setattr(gpu.subprocess, "run", fake_run)
    row = gpu.gpu_monitor()
    assert row["gpu"]["name"] == "B200"
    assert row["gpu"]["util"] == 0
    assert row["gpu"]["total_mib"] == 183359
    assert gpu._monitor_cache_seconds(row) == gpu._MONITOR_STANDBY_CACHE_SECONDS
    row["generate"] = "generate.py 00:12"
    assert gpu._monitor_cache_seconds(row) == gpu._MONITOR_ACTIVE_CACHE_SECONDS


def test_gpu_monitor_skips_ssh_when_the_line_is_down(monkeypatch) -> None:
    import time

    import diony_producer.gpu as gpu

    _reset_line_state(monkeypatch)
    monkeypatch.setattr(gpu, "in_pytest", lambda: False)
    monkeypatch.setattr(gpu, "should_proxy", lambda: True)
    monkeypatch.setattr(gpu, "last_gpu_error", lambda: "SSH 10.0.0.8:40123 refused.")
    monkeypatch.setattr(
        gpu,
        "connect_from_lock",
        lambda lock=None: {"host": "10.0.0.8", "ssh_port": "40123", "ssh_user": "root"},
    )
    monkeypatch.setattr(gpu, "_ssh_fail_key", "10.0.0.8:40123")
    monkeypatch.setattr(gpu, "_ssh_fail_at", time.time())
    monkeypatch.setattr(gpu, "_MONITOR_CACHE", {"at": 0.0, "row": None})

    def boom(command, **kwargs):
        raise AssertionError("SSH must not be opened on a dead line")

    monkeypatch.setattr(gpu.subprocess, "run", boom)
    assert gpu.gpu_monitor()["gpu"]["name"] == ""


def test_start_script_emits_stage_markers_that_never_read_as_runtime_failures() -> None:
    import diony_producer.gpu as gpu

    script = _gpu_producer_start_script()
    for marker in ("GPU_STAGE_WAN_PATCH", "GPU_STAGE_RUNTIME_CHECK", "GPU_STAGE_PRODUCER_STOP"):
        assert f"echo {marker}" in script
        assert marker in gpu._LAUNCH_STAGES
        assert "GPU_RUNTIME_" not in marker
    assert script.index("echo GPU_STAGE_RUNTIME_CHECK") < script.index("echo GPU_RUNTIME_OVERLAY_REPAIR")
    assert script.index("echo GPU_STAGE_PRODUCER_STOP") < script.index("echo GPU_PRODUCER_LAUNCHED")


def test_start_script_defers_before_any_probe_while_a_camera_pass_is_live() -> None:
    script = _gpu_producer_start_script()
    guard = script.index("echo GPU_PRODUCER_BUSY_DEFERRED")
    assert "[g]enerate.py.*--task" in script[:guard]
    assert guard < script.index("echo GPU_STAGE_WAN_PATCH")
    assert guard < script.index("echo GPU_STAGE_RUNTIME_CHECK")
    assert guard < script.index("_diony_wan_host_ready")


def test_dropped_tunnel_to_an_answering_producer_reopens_the_line_without_a_relaunch(monkeypatch) -> None:
    import diony_producer.gpu as gpu

    _reset_line_state(monkeypatch)
    conn = {"host": "10.0.0.8", "ssh_port": "40123", "ssh_user": "root"}
    monkeypatch.setattr(gpu, "should_proxy", lambda: True)
    monkeypatch.setattr(gpu, "sync_connect_from_wizard", lambda: None)
    monkeypatch.setattr(gpu, "connect_from_lock", lambda lock=None: dict(conn))
    monkeypatch.setattr(gpu, "apply_ssh_env", lambda c: None)
    monkeypatch.setattr(gpu, "persist_connect", lambda c: None)
    monkeypatch.setattr(gpu, "_port_open", lambda port: False)
    monkeypatch.setattr(gpu, "_probe_ssh", lambda c: True)
    monkeypatch.setattr(gpu, "_producer_coming_up", lambda c: False)
    monkeypatch.setattr(gpu, "_remote_producer_answering", lambda c: True)
    steps: list[str] = []
    monkeypatch.setattr(gpu, "_sync_gpu_apps", lambda c: steps.append("sync") or True)
    monkeypatch.setattr(gpu, "_start_gpu_producer", lambda c: steps.append("start"))
    monkeypatch.setattr(gpu, "_ensure_tunnel", lambda c: steps.append("tunnel") or True)

    class Res:
        status_code = 200

    monkeypatch.setattr(gpu.httpx, "get", lambda *a, **k: Res())
    assert gpu.ensure_gpu_producer() == gpu.gpu_producer_base()
    assert steps == ["tunnel"]

    # A Producer that does not answer on the box is relaunched as before.
    steps.clear()
    monkeypatch.setattr(gpu, "_remote_producer_answering", lambda c: False)
    assert gpu.ensure_gpu_producer() == gpu.gpu_producer_base()
    assert steps == ["sync", "start", "tunnel"]


def test_line_state_drops_a_stale_ssh_error_once_the_line_is_up(monkeypatch) -> None:
    import time

    import diony_producer.gpu as gpu

    _reset_line_state(monkeypatch)
    monkeypatch.setattr(gpu, "connect_from_lock", lambda lock=None: {"host": "10.0.0.8", "ssh_port": "40123"})
    monkeypatch.setattr(gpu, "_ssh_ok_at", time.time())
    monkeypatch.setattr(gpu, "last_gpu_error", lambda: "SSH 10.0.0.8:40123 refused. After a pod restart …")
    state = gpu.gpu_line_state(live=False)
    assert state["ssh"] == "up" and state["error"] == ""

