import pytest


# Unique name on purpose: apps/wizard and apps/director also define an
# autouse vault fixture, and in a multi-directory pytest run the same
# basename shadowed this one.
@pytest.fixture(autouse=True)
def producer_isolated_vault(tmp_path, monkeypatch):
    monkeypatch.setenv("DIONY_VAULT", str(tmp_path))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path))
    monkeypatch.setenv("DIONY_DRY_RUN", "1")
    yield


# Blueprint 24: no test reaches the network; loopback stays open.
from diony_contracts.testing import network_guard_fixture  # noqa: E402

_diony_network_guard = network_guard_fixture()
