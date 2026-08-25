from pathlib import Path

from diony_wizard.prep import bind_ssh_env
from diony_wizard.remote import exec_host, rsync_repo, ssh_args


def test_ssh_args_include_identity(monkeypatch) -> None:
    monkeypatch.setenv("DIONY_SSH_KEY", "/tmp/diony_id")
    args = ssh_args()
    assert "BatchMode=yes" in args
    assert "-i" in args
    assert args[args.index("-i") + 1] == "/tmp/diony_id"


def test_streamlined_connection_binds_the_copied_identity_file(monkeypatch) -> None:
    monkeypatch.delenv("DIONY_SSH_KEY", raising=False)

    bind_ssh_env({"ssh_identity_file": "~/.ssh/id_ed25519"})

    assert ssh_args()[ssh_args().index("-i") + 1] == str(
        Path("~/.ssh/id_ed25519").expanduser()
    )


def test_rsync_uses_ssh_identity(monkeypatch) -> None:
    monkeypatch.delenv("DIONY_APPLY_LOCAL", raising=False)
    monkeypatch.setenv("DIONY_SSH_KEY", "/tmp/diony_id")
    monkeypatch.setenv("DIONY_SSH_USER", "spark")
    monkeypatch.setattr("diony_wizard.remote.apply_local", lambda host: False)

    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd

        class Proc:
            returncode = 0
            stdout = ""
            stderr = ""

        return Proc()

    monkeypatch.setattr("diony_wizard.remote.subprocess.run", fake_run)
    result = rsync_repo("spark-01", "/src", "/opt/diony-ai")
    assert result.ok is True
    cmd = captured["cmd"]
    assert cmd[0] == "rsync"
    assert "-e" in cmd
    transport = cmd[cmd.index("-e") + 1]
    assert "-i" in transport
    assert "/tmp/diony_id" in transport
    assert "spark@spark-01:" in cmd[-1]


def test_exec_host_streams_local_output_to_the_progress_callback(monkeypatch) -> None:
    monkeypatch.setenv("DIONY_APPLY_LOCAL", "1")
    lines: list[str] = []

    result = exec_host(
        "localhost",
        "printf 'HF_START\\nCOSYVOICE_OK\\n'",
        timeout=5,
        on_output=lines.append,
    )

    assert result.ok is True
    assert result.stdout == "HF_START\nCOSYVOICE_OK\n"
    assert lines == ["HF_START", "COSYVOICE_OK"]


def test_ssh_args_include_port(monkeypatch) -> None:
    monkeypatch.setenv("DIONY_SSH_PORT", "28231")
    args = ssh_args()
    assert "-p" in args
    assert args[args.index("-p") + 1] == "28231"


def test_rsync_uses_ssh_port(monkeypatch) -> None:
    monkeypatch.delenv("DIONY_APPLY_LOCAL", raising=False)
    monkeypatch.setenv("DIONY_SSH_USER", "root")
    monkeypatch.setenv("DIONY_SSH_PORT", "28231")
    monkeypatch.setattr("diony_wizard.remote.apply_local", lambda host: False)

    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd

        class Proc:
            returncode = 0
            stdout = ""
            stderr = ""

        return Proc()

    monkeypatch.setattr("diony_wizard.remote.subprocess.run", fake_run)
    result = rsync_repo("198.13.252.68", "/src", "/opt/diony-ai")
    assert result.ok is True
    transport = captured["cmd"][captured["cmd"].index("-e") + 1]
    assert "-p" in transport
    assert "28231" in transport
    assert "root@198.13.252.68:" in captured["cmd"][-1]


def test_ssh_connect_advice_maps_refused() -> None:
    from diony_wizard.remote import ssh_connect_advice

    msg = ssh_connect_advice(
        "198.13.252.68",
        28231,
        "ssh: connect to host 198.13.252.68 port 28231: Connection refused",
    )
    assert msg is not None
    assert "198.13.252.68:28231" in msg
    assert "Resume the pod" in msg
    assert "SSH over exposed TCP" in msg
    assert ssh_connect_advice("198.13.252.68", 28231, "Permission denied") is None


def test_ssh_args_keep_long_pulls_alive() -> None:
    args = ssh_args()
    assert "ServerAliveInterval=30" in args
    assert "ServerAliveCountMax=8" in args


def test_terminate_streaming_stops_registered_sessions() -> None:
    import subprocess

    from diony_wizard import remote

    proc = subprocess.Popen(["sleep", "30"])
    with remote._streaming_lock:
        remote._streaming_procs.add(proc)
    try:
        assert remote.terminate_streaming() == 1
        assert proc.poll() is not None
    finally:
        with remote._streaming_lock:
            remote._streaming_procs.discard(proc)
        if proc.poll() is None:
            proc.kill()
    assert remote.terminate_streaming() == 0
