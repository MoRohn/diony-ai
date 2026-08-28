"""Repository-wide offline guard for whole-repo pytest runs (Blueprint 24).

Each package's ``tests/conftest.py`` installs the same guard for single-package
runs; the helper is idempotent so both may register it.
"""

from diony_contracts.testing import network_guard_fixture

_diony_network_guard = network_guard_fixture()
