import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# Blueprint 24: no test reaches the network; loopback stays open.
from diony_contracts.testing import network_guard_fixture  # noqa: E402

_diony_network_guard = network_guard_fixture()
