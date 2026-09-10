import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2].parent
for extra in ("apps/producer/src", "apps/wan/src", "apps/finish/src", "packages/contracts/src"):
    candidate = REPO / extra
    if candidate.is_dir() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from diony_contracts.testing import network_guard_fixture  # noqa: E402 - after the path shim

# Blueprint 24 offline guard: every package registers it so a single-package
# run is as offline as a whole-repo run (the helper is idempotent).
_diony_network_guard = network_guard_fixture()


@pytest.fixture(autouse=True)
def director_isolated_vault(tmp_path, monkeypatch):
    """Every director test runs against its own vault, control plane, and keys."""

    # No DIONY_CONTROL_PLANE_DB here: in multi-directory runs this repo's
    # tests/conftest.py autouse fixtures bleed across packages, and an explicit
    # DB path would detach other packages' control planes from their vaults.
    # control_plane_path() falls back to the vault set below, which isolates
    # director tests and stays harmless everywhere else.
    monkeypatch.setenv("DIONY_VAULT", str(tmp_path))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path))
    monkeypatch.setenv("DIONY_DRY_RUN", "1")
    monkeypatch.setenv("XAI_API_KEY", "test-xai-key")
    monkeypatch.setenv("USEAPI_TOKEN", "test-useapi-token")
    monkeypatch.setenv("DIONY_FLOW_PLAN", "pro")
    monkeypatch.delenv("DIONY_RATECARD_DIR", raising=False)
    monkeypatch.delenv("DIONY_ENGINE", raising=False)
    monkeypatch.delenv("DIONY_KEYS_FILE", raising=False)
    # Brain explicitly OFF for unit determinism (unset would default to xAI
    # since the test key is present); brain tests set the URL themselves.
    monkeypatch.setenv("DIONY_DIRECTOR_BRAIN_URL", "")
    monkeypatch.setenv("DIONY_KEYS_FILE", str(tmp_path / "keys.env"))
    # Studio always chose a camera before the Director opened; tests default to Grok
    from diony_contracts.cluster import write_engine_choice

    write_engine_choice("grok")
    # The Flow request-pacing gate stays live (requests still serialize and
    # stamp the clock) but never really sleeps between fakes; the pacing
    # tests install their own recording sleep.
    from diony_director.providers import flow as _flow

    monkeypatch.setattr(_flow, "_sleep", lambda seconds: None)
    monkeypatch.setattr(_flow, "_last_request_done_at", 0.0)
    from diony_director import session as _director_session

    _director_session.reset()
    yield tmp_path
    _director_session.reset()


@pytest.fixture
def fake_narrator(monkeypatch):
    """A Blueprint 28 narrator backend for tests: ffmpeg speaks a sine tone
    through the same DIONY_NARRATOR_CLI contract a real narrator uses."""
    import shutil

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg not installed")
    # the vault-wide XAI test key would auto-select the grok voice backend;
    # tests speak through the CLI contract explicitly
    monkeypatch.setenv("DIONY_NARRATOR_BACKEND", "cli")
    monkeypatch.setenv(
        "DIONY_NARRATOR_CLI",
        f"{ffmpeg} -y -f lavfi -i sine=frequency=520:duration=1.2 -ar 48000 -ac 1 {{output}}",
    )
