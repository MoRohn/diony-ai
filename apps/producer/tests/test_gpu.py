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
    script = _gpu_producer_start_script()
    assert "wan-host-overlay.txt" in script
    assert "diony_wan/runtime.py" in script
    assert "wan-host-runtime.json" in script
    assert "ensure_optional_engine_lazy_imports" in script
    assert "GPU_HOST_PYTHON_WITH_TORCH_MISSING" in script
    assert "/usr/local/bin/python" in script
    assert "/opt/diony-ai/.venv/bin/diony-producer" in script
    assert "GPU_RUNTIME_OVERLAY_REPAIR" in script
    assert "DIONY_COSYVOICE_PYTHON" in script
    assert "/diony/vault/models/audio/CosyVoice2-0.5B" in script
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

    gpu._sync_gpu_apps({"ssh_port": "28869", "ssh_user": "root", "host": "gpu"})

    assert len(calls) == 1
    assert calls[0][-2:] == ["cat", "/diony/vault/.cluster/desk.stamp"]
    assert gpu._synced_stamp == ""


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
