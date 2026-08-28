"""Blueprint 24: no test reaches the network; loopback stays open."""

from diony_contracts.testing import network_guard_fixture

_diony_network_guard = network_guard_fixture()
