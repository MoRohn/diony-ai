from __future__ import annotations

import json
from pathlib import Path

from diony_contracts.fixture import demo_project
from diony_contracts.models import (
    Asset,
    Bible,
    Brief,
    ClipEdit,
    ClusterSite,
    Deliverable,
    MixLock,
    Note,
    Project,
    QCReport,
    Shot,
    Take,
    Timeline,
)


def main() -> None:
    root = Path(__file__).resolve().parent.parent.parent
    out = root / "jsonschema"
    out.mkdir(parents=True, exist_ok=True)
    models = {
        "project": Project,
        "bible": Bible,
        "brief": Brief,
        "asset": Asset,
        "clip_edit": ClipEdit,
        "cluster_site": ClusterSite,
        "shot": Shot,
        "take": Take,
        "qc_report": QCReport,
        "timeline": Timeline,
        "mix_lock": MixLock,
        "deliverable": Deliverable,
        "note": Note,
    }
    for name, model in models.items():
        path = out / f"{name}.schema.json"
        path.write_text(json.dumps(model.model_json_schema(), indent=2) + "\n")
        print(path)
    examples = root / "examples"
    examples.mkdir(parents=True, exist_ok=True)
    pilot = examples / "pilot.json"
    pilot.write_text(json.dumps(demo_project().model_dump(mode="json"), indent=2) + "\n")
    print(pilot)


if __name__ == "__main__":
    main()
