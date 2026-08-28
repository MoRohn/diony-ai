"""The committed JSON Schemas must match the contracts they describe."""

from __future__ import annotations

import json

from diony_contracts.export_schema import schema_documents, schema_root


def test_committed_jsonschema_is_in_sync_with_the_models() -> None:
    root = schema_root() / "jsonschema"
    stale: list[str] = []
    for name, schema in schema_documents().items():
        path = root / f"{name}.schema.json"
        if not path.is_file():
            stale.append(f"{name}: missing (run diony-export-schemas)")
            continue
        if json.loads(path.read_text()) != schema:
            stale.append(f"{name}: differs from the model (run diony-export-schemas)")
    assert not stale, "\n".join(stale)
