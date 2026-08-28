import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "wan" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "finish" / "src"))


# Unique name on purpose: apps/wizard and apps/conductor also define an
# autouse `isolated_vault`, and in a multi-directory pytest run the same
# basename shadowed this one, so the farm was never reset between producer
# tests (a leaked `_pending` take then blocked the early-finish test).
@pytest.fixture(autouse=True)
def producer_isolated_vault(tmp_path, monkeypatch):
    monkeypatch.setenv("DIONY_VAULT", str(tmp_path))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path))
    monkeypatch.setenv("DIONY_DRY_RUN", "1")
    from diony_producer.farm import abort_farm, reset_farm
    from diony_producer.finish import reset_finish
    from diony_producer.mix import reset_mix

    reset_farm()
    reset_finish()
    reset_mix()
    yield
    abort_farm()
    # A farm worker that is still finalizing after abort re-queues its take
    # into `_pending` *after* the reset below; join it so no test inherits
    # another test's queue.
    import threading

    for thread in threading.enumerate():
        if thread.name == "diony-farm" and thread is not threading.current_thread():
            thread.join(timeout=5)
    reset_farm()
    reset_finish()
    reset_mix()

# Blueprint 24: no test reaches the network; loopback stays open.
from diony_contracts.testing import network_guard_fixture  # noqa: E402

_diony_network_guard = network_guard_fixture()
