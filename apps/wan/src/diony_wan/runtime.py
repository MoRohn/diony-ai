"""Validate the CUDA host Python used by official Wan2.2.

This module intentionally imports only the standard library before probing so it
can explain an incomplete GPU image instead of crashing on the first missing
package. It runs under the same interpreter later used for ``generate.py``.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import importlib.util
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


REQUIRED_IMPORTS: tuple[tuple[str, str], ...] = (
    ("torch", "torch"),
    ("torchvision", "torchvision"),
    ("einops", "einops"),
    ("decord", "decord"),
    ("diffusers", "diffusers"),
    ("transformers", "transformers"),
    ("tokenizers", "tokenizers"),
    ("accelerate", "accelerate"),
    ("opencv-python", "cv2"),
    ("imageio", "imageio"),
    ("easydict", "easydict"),
    ("ftfy", "ftfy"),
    ("dashscope", "dashscope"),
    ("Wan2.2", "wan"),
)

OPTIONAL_ENGINE_IMPORTS: dict[str, tuple[tuple[str, str], ...]] = {
    "s2v": (
        ("openai-whisper", "whisper"),
        ("HyperPyYAML", "hyperpyyaml"),
        ("onnxruntime", "onnxruntime"),
        ("omegaconf", "omegaconf"),
        ("hydra-core", "hydra"),
        ("librosa", "librosa"),
    ),
    "animate": (
        ("onnxruntime", "onnxruntime"),
        ("peft", "peft"),
        ("pandas", "pandas"),
        ("SAM-2", "sam2"),
        ("loguru", "loguru"),
        ("sentencepiece", "sentencepiece"),
    ),
}


def probe_runtime() -> dict:
    packages: dict[str, str] = {}
    failures: list[dict[str, str]] = []
    for distribution, module_name in REQUIRED_IMPORTS:
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:  # noqa: BLE001 - the report must retain import-time failures
            failures.append(
                {
                    "distribution": distribution,
                    "module": module_name,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            continue
        version = str(getattr(module, "__version__", "") or "")
        if not version and distribution != "Wan2.2":
            try:
                version = importlib.metadata.version(distribution)
            except importlib.metadata.PackageNotFoundError:
                version = "present"
        packages[distribution] = version or "present"
    core_missing = [row["distribution"] for row in failures]
    capabilities = {
        "t2v": {"available": not failures, "missing": core_missing},
        "i2v": {"available": not failures, "missing": core_missing},
    }
    for engine, imports in OPTIONAL_ENGINE_IMPORTS.items():
        missing: list[str] = []
        for distribution, module_name in imports:
            try:
                found = importlib.util.find_spec(module_name) is not None
            except (ImportError, AttributeError, ValueError):
                found = False
            if not found:
                missing.append(distribution)
        capabilities[engine] = {"available": not missing and not failures, "missing": missing}
    return {
        "schema_version": 1,
        "ok": not failures,
        "python": sys.executable,
        "python_version": sys.version.split()[0],
        "wan_repo": os.environ.get("PYTHONPATH", ""),
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "packages": packages,
        "failures": failures,
        "capabilities": capabilities,
    }


def _write_report(path: str, report: dict) -> None:
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=dest.parent,
        prefix=f"{dest.name}.",
        suffix=".tmp",
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        temporary.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        temporary.replace(dest)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate Diony Wan host Python")
    parser.add_argument("--write", default="", help="Persist a versioned JSON readiness report")
    parser.add_argument(
        "--require-capability",
        action="append",
        choices=sorted(OPTIONAL_ENGINE_IMPORTS),
        default=[],
        help="Fail readiness when an installed optional model engine cannot import.",
    )
    args = parser.parse_args(argv)
    report = probe_runtime()
    required = list(dict.fromkeys(args.require_capability))
    report["required_capabilities"] = required
    report["required_capabilities_ready"] = all(
        bool(report.get("capabilities", {}).get(name, {}).get("available"))
        for name in required
    )
    if args.write:
        _write_report(args.write, report)
    print(json.dumps(report, separators=(",", ":")))
    return 0 if report["ok"] and report["required_capabilities_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
