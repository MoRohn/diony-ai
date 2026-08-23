import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "wan" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "finish" / "src"))


@pytest.fixture(autouse=True)
def isolated_vault(tmp_path, monkeypatch):
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
    reset_farm()
    reset_finish()
    reset_mix()
