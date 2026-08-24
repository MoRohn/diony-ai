from pathlib import Path

import yaml


def test_model_manifest_is_valid_revision_pinned_and_persistent() -> None:
    manifest = yaml.safe_load(Path("infra/models/manifest.yaml").read_text(encoding="utf-8"))
    models = manifest["models"]

    assert len(models) >= 8
    for model in models:
        assert len(model["revision"]) == 40
        assert model["dest"].startswith("/models/")
        assert model["persistent_dest"].startswith("/diony/vault/models/")
        assert model["bytes"] is None or model["bytes"] > 0
    assert manifest["storage"]["canonical"] == "/diony/vault/models"
    assert manifest["storage"]["compatibility"] == "/models"
    assert manifest["storage"]["hot_cache_gb"] == 320
