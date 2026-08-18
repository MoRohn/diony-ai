from diony_wizard.remote import rsync_repo, ssh_args


def test_ssh_args_include_identity(monkeypatch) -> None:
    monkeypatch.setenv("DIONY_SSH_KEY", "/tmp/diony_id")
    args = ssh_args()
    assert "BatchMode=yes" in args
    assert "-i" in args
    assert args[args.index("-i") + 1] == "/tmp/diony_id"


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
