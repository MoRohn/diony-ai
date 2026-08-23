"""Parse the standard Runpod SSH-over-exposed-TCP command without executing it."""

from __future__ import annotations

import shlex
from pathlib import Path

from diony_wizard.prep import parse_gpu_ssh_host, parse_ssh_port, parse_ssh_user


class SSHCommandError(ValueError):
    """The pasted text is not a supported Runpod SSH connection command."""


def normalize_identity_file(raw: str) -> str:
    """Return a local absolute key path suitable for subprocess argv."""
    value = (raw or "").strip()
    if not value or "\x00" in value or "\n" in value or "\r" in value:
        raise SSHCommandError("The SSH identity file after -i is not valid.")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise SSHCommandError("The SSH identity file after -i must use ~/ or an absolute path.")
    return str(path)


def parse_runpod_ssh_command(raw: str) -> dict[str, str]:
    """Split a copied Runpod TCP command into Diony's connection contract."""
    command = (raw or "").strip()
    if not command:
        raise SSHCommandError("Paste the Runpod SSH over exposed TCP command.")
    try:
        parts = shlex.split(command, posix=True)
    except ValueError as exc:
        raise SSHCommandError("The SSH command has an unmatched quote.") from exc
    if parts and parts[0] == "$":
        parts = parts[1:]
    if not parts or parts[0].lower() != "ssh":
        raise SSHCommandError("Paste the full command beginning with ssh.")

    destination = ""
    port_text = ""
    identity_file = ""
    index = 1
    while index < len(parts):
        part = parts[index]
        if part in {"-p", "--port"}:
            index += 1
            if index >= len(parts):
                raise SSHCommandError("The SSH port is missing after -p.")
            port_text = parts[index]
        elif part.startswith("-p") and part != "-p":
            port_text = part[2:]
        elif part in {"-i", "--identity-file"}:
            index += 1
            if index >= len(parts):
                raise SSHCommandError("The SSH identity file is missing after -i.")
            identity_file = normalize_identity_file(parts[index])
        elif part.startswith("-"):
            raise SSHCommandError(
                "Use Runpod's standard SSH over exposed TCP command with only -p and -i."
            )
        elif destination:
            raise SSHCommandError("Paste only the SSH connection command, without a remote command.")
        else:
            destination = part
        index += 1

    if destination.count("@") != 1:
        raise SSHCommandError("The SSH command must include the GPU login as user@host.")
    user, host = destination.split("@", 1)
    user_error = parse_ssh_user(user)
    if user_error:
        raise SSHCommandError(user_error)
    host_error = parse_gpu_ssh_host(host)
    if host_error:
        raise SSHCommandError(host_error)
    if not port_text:
        raise SSHCommandError("Use SSH over exposed TCP and include its -p port.")
    port, port_error = parse_ssh_port(port_text)
    if port_error or port is None:
        raise SSHCommandError(port_error or "SSH port must be between 1 and 65535.")

    connection = {
        "hosts": host,
        "ssh_user": user,
        "ssh_port": str(port),
    }
    if identity_file:
        connection["ssh_identity_file"] = identity_file
    return connection
