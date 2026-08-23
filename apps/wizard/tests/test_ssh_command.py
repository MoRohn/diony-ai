import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from diony_wizard.ssh_command import SSHCommandError, parse_runpod_ssh_command

wizard_app = importlib.import_module("diony_wizard.app")


def test_runpod_exposed_tcp_command_is_split_into_compute_connection_values() -> None:
    connection = parse_runpod_ssh_command(
        "ssh root@198.13.252.71 -p 47803 -i ~/.ssh/id_ed25519"
    )

    assert connection == {
        "hosts": "198.13.252.71",
        "ssh_user": "root",
        "ssh_port": "47803",
        "ssh_identity_file": str(Path("~/.ssh/id_ed25519").expanduser()),
    }


def test_runpod_command_parser_accepts_the_copied_shell_prompt_and_option_order() -> None:
    connection = parse_runpod_ssh_command(
        "$ ssh -i ~/.ssh/id_ed25519 -p 47803 root@198.13.252.71"
    )

    assert connection["hosts"] == "198.13.252.71"
    assert connection["ssh_user"] == "root"
    assert connection["ssh_port"] == "47803"


@pytest.mark.parametrize(
    ("command", "message"),
    [
        ("ssh root@ssh.runpod.io -p 22", "exposed TCP"),
        ("ssh root@198.13.252.71 -p 8888", "Jupyter"),
        ("ssh root@198.13.252.71 -p 47803 uname -a", "only the SSH connection"),
    ],
)
def test_runpod_command_parser_rejects_non_tcp_or_executable_input(
    command: str, message: str
) -> None:
    with pytest.raises(SSHCommandError, match=message):
        parse_runpod_ssh_command(command)


def test_streamlined_parse_api_and_compute_ui_use_the_connection_contract() -> None:
    client = TestClient(wizard_app.app)

    parsed = client.post(
        "/api/ssh/parse",
        json={"command": "ssh root@198.13.252.71 -p 47803 -i ~/.ssh/id_ed25519"},
    )
    assert parsed.status_code == 200, parsed.text
    assert parsed.json()["connection"]["hosts"] == "198.13.252.71"

    js = client.get("/static/app.js").text
    css = client.get("/static/app.css").text
    assert "Runpod quick connect" in js
    assert "Paste SSH command" in js
    assert "/api/ssh/parse" in js
    assert 'name="ssh_identity_file"' in js
    assert ".runpod-command-entry" in css
    assert ".runpod-command-action" in css
