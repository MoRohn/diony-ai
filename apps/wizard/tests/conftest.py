import pytest


@pytest.fixture(autouse=True)
def isolated_vault(tmp_path, monkeypatch):
    monkeypatch.setenv("DIONY_VAULT", str(tmp_path))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path))
    monkeypatch.setenv("DIONY_DRY_RUN", "1")
    # Provider-readiness tests set their own keys. Never let a key loaded by
    # the developer's launcher make an offline test report Gemini as ready.
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    yield


# Blueprint 24: no test reaches the network; loopback stays open.
from diony_contracts.testing import network_guard_fixture  # noqa: E402

_diony_network_guard = network_guard_fixture()
