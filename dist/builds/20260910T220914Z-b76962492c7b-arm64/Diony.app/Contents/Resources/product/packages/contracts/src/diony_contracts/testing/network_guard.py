"""Socket-level offline guard for every Diony test session (Blueprint 24).

Diony is never cloud-by-default and its test suite must prove it. The wall
sits at the socket layer because every client — httpx, requests, urllib,
vendor SDKs — bottoms out in ``socket.connect``. Loopback and AF_UNIX stay
open: the service test clients and the Desk port probes
need them. ``DIONY_ALLOW_NETWORK=1`` lifts the guard for an explicitly live run.

Each package's ``tests/conftest.py`` registers ``network_guard_fixture`` so the
guard is active whether one package or the whole repository is collected.
"""

from __future__ import annotations

import os
import socket
from contextlib import contextmanager

_LOOPBACK = {"localhost", "127.0.0.1", "::1", "0.0.0.0", ""}
_MARKER = "_diony_network_guard"


class NetworkBlocked(RuntimeError):
    """A test attempted to reach a non-loopback address."""


def address_allowed(address) -> bool:
    if isinstance(address, (str, bytes)):
        return True  # AF_UNIX
    try:
        host = str(address[0])
    except (TypeError, IndexError):
        return False
    # Diony's product hostnames (studio.diony.local, producer.diony.local) are
    # loopback names by design (Blueprint 11); they never resolve on the internet.
    return host in _LOOPBACK or host.startswith("127.") or host.endswith(".diony.local")


def _refuse(address) -> None:
    raise NetworkBlocked(
        f"Blocked a network connection to {address!r}: Diony tests run offline. "
        "Set DIONY_ALLOW_NETWORK=1 only for an explicitly live run."
    )


def network_allowed_by_env() -> bool:
    return os.environ.get("DIONY_ALLOW_NETWORK", "").strip().lower() in {"1", "true", "yes"}


@contextmanager
def install_network_guard():
    """Patch the socket layer for the duration of the context; idempotent."""
    if network_allowed_by_env() or getattr(socket, _MARKER, False):
        yield
        return
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_create = socket.create_connection

    def connect(self, address):
        if not address_allowed(address):
            _refuse(address)
        return real_connect(self, address)

    def connect_ex(self, address):
        if not address_allowed(address):
            _refuse(address)
        return real_connect_ex(self, address)

    def create_connection(address, *args, **kwargs):
        if not address_allowed(address):
            _refuse(address)
        return real_create(address, *args, **kwargs)

    socket.socket.connect = connect  # type: ignore[method-assign]
    socket.socket.connect_ex = connect_ex  # type: ignore[method-assign]
    socket.create_connection = create_connection  # type: ignore[assignment]
    setattr(socket, _MARKER, True)
    try:
        yield
    finally:
        socket.socket.connect = real_connect  # type: ignore[method-assign]
        socket.socket.connect_ex = real_connect_ex  # type: ignore[method-assign]
        socket.create_connection = real_create  # type: ignore[assignment]
        setattr(socket, _MARKER, False)


def network_guard_fixture():
    """Return a session-scoped autouse pytest fixture installing the guard."""
    import pytest

    @pytest.fixture(scope="session", autouse=True)
    def _diony_network_guard():
        with install_network_guard():
            yield

    return _diony_network_guard


__all__ = ["NetworkBlocked", "address_allowed", "install_network_guard", "network_guard_fixture"]
