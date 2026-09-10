import pytest


@pytest.fixture(autouse=True)
def isolated_vault(tmp_path, monkeypatch):
    monkeypatch.setenv("DIONY_VAULT", str(tmp_path))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path))
    monkeypatch.setenv("DIONY_DRY_RUN", "1")
    yield


# Blueprint 24: no test reaches the network; loopback stays open.
from diony_contracts.testing import network_guard_fixture  # noqa: E402

_diony_network_guard = network_guard_fixture()
