"""Deterministic desktop payload and two-platform download manifest tooling."""

from __future__ import annotations

import hashlib
import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PAYLOAD_SCHEMA_VERSION = 1
DOWNLOAD_SCHEMA_VERSION = 1
PRODUCT_NAME = "Diony AI"
LOCAL_BUILD_RECIPE = "macos-development-v3"
ROOT_FILES = (
    "AGENTS.md",
    "CHANGELOG.md",
    "LICENSE",
    "NOTICE",
    "README.md",
    "START HERE.txt",
)
PRODUCT_TREES = ("apps", "packages", "infra", "blueprints", "scripts/benchmark")
FEATURE_FILE_SETS = {
    "voice_performance": (
        Path("apps/desk/src/diony_desk/boot.py"),
        Path("apps/producer/src/diony_producer/app.py"),
        Path("apps/producer/src/diony_producer/farm.py"),
        Path("apps/producer/src/diony_producer/gpu.py"),
        Path("apps/producer/src/diony_producer/mix.py"),
        Path("apps/producer/src/diony_producer/voice.py"),
        Path("packages/contracts/src/diony_contracts/__init__.py"),
        Path("packages/contracts/src/diony_contracts/enums.py"),
        Path("packages/contracts/src/diony_contracts/export_schema.py"),
        Path("packages/contracts/src/diony_contracts/models.py"),
        Path("packages/contracts/src/diony_contracts/voice.py"),
        Path("packages/contracts/jsonschema/bible.schema.json"),
        Path("packages/contracts/jsonschema/speech_clip_package.schema.json"),
        Path("packages/contracts/jsonschema/speech_qc_receipt.schema.json"),
        Path("packages/contracts/jsonschema/project.schema.json"),
        Path("packages/contracts/jsonschema/qc_report.schema.json"),
        Path("packages/contracts/jsonschema/shot.schema.json"),
        Path("packages/contracts/jsonschema/take.schema.json"),
        Path("packages/contracts/jsonschema/voice_casting.schema.json"),
        Path("packages/contracts/jsonschema/wan_speech_request.schema.json"),
        Path("blueprints/22-voice-performance-system.md"),
    ),
    "optimal_clip_flow": (
        Path("apps/producer/src/diony_producer/farm.py"),
        Path("apps/producer/src/diony_producer/gpu.py"),
        Path("apps/producer/src/diony_producer/run.py"),
        Path("apps/producer/src/diony_producer/static/app.js"),
        Path("apps/producer/src/diony_producer/static/index.html"),
        Path("apps/wan/src/diony_wan/attention_qualification.py"),
        Path("apps/wan/src/diony_wan/runner.py"),
        Path("packages/contracts/src/diony_contracts/models.py"),
        Path("packages/contracts/src/diony_contracts/studio.py"),
        Path("packages/contracts/jsonschema/generation_execution_receipt.schema.json"),
        Path("scripts/benchmark/measure_wan_attention.py"),
        Path("blueprints/23-optimal-clip-production-flow.md"),
    ),
}
EXCLUDED_PARTS = {
    ".benchmarks",
    ".git",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "build",
    "data",
    "dist",
    "tests",
}
EXCLUDED_SUFFIXES = (".pyc", ".pyo", ".sqlite", ".sqlite3", ".log")


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _ship(relative: Path) -> bool:
    if any(part in EXCLUDED_PARTS for part in relative.parts):
        return False
    if any(part.endswith((".egg-info", ".dist-info")) for part in relative.parts):
        return False
    name = relative.name.lower()
    if name == ".ds_store" or name == ".env" or name.startswith(".env."):
        return False
    return not name.endswith(EXCLUDED_SUFFIXES)


def _payload_files(source: Path) -> list[Path]:
    rows: list[Path] = []
    for name in ROOT_FILES:
        candidate = source / name
        if candidate.is_file():
            rows.append(candidate)
    for tree in PRODUCT_TREES:
        base = source / tree
        if not base.is_dir():
            continue
        for candidate in base.rglob("*"):
            if candidate.is_file() and not candidate.is_symlink():
                relative = candidate.relative_to(source)
                if _ship(relative):
                    rows.append(candidate)
    return sorted(rows, key=lambda path: path.relative_to(source).as_posix())


def _payload_digest(root: Path, files: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in files:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(bytes.fromhex(sha256_file(path)))
    return digest.hexdigest()


def _feature_receipts(source: Path) -> dict[str, dict[str, Any]]:
    receipts: dict[str, dict[str, Any]] = {}
    for feature, relatives in FEATURE_FILE_SETS.items():
        paths = [source / relative for relative in relatives]
        missing = [relative.as_posix() for relative, path in zip(relatives, paths) if not path.is_file()]
        if missing:
            raise ValueError(
                f"desktop feature {feature} is incomplete; missing " + ", ".join(missing)
            )
        receipts[feature] = {
            "files": len(paths),
            "paths": [relative.as_posix() for relative in relatives],
            "sha256": _payload_digest(source, paths),
        }
    return receipts


def stage_product(source: Path, target: Path, *, version: str) -> dict[str, Any]:
    """Copy the redistributable product tree without tests, state, logs, or secrets."""
    source = source.resolve()
    if not version.strip():
        raise ValueError("desktop version is required")
    if target.exists() and any(target.iterdir()):
        raise ValueError(f"desktop payload target must be empty: {target}")
    target.mkdir(parents=True, exist_ok=True)
    source_files = _payload_files(source)
    if not source_files:
        raise ValueError(f"no Diony product files found in {source}")
    features = _feature_receipts(source)
    for source_path in source_files:
        relative = source_path.relative_to(source)
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, destination)
    staged_files = sorted(
        (path for path in target.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(target).as_posix(),
    )
    report = {
        "schema_version": PAYLOAD_SCHEMA_VERSION,
        "product": PRODUCT_NAME,
        "version": version.strip(),
        "files": len(staged_files),
        "payload_sha256": _payload_digest(target, staged_files),
        "features": features,
    }
    (target / "desktop-release.json").write_bytes(_json_bytes(report))
    return report


def build_development_report(
    *,
    app: Path,
    payload_metadata: Path,
    version: str,
    architecture: str,
    built_at: str | None = None,
) -> dict[str, Any]:
    """Describe a local app without confusing it with trusted release proof."""
    app = app.resolve()
    executable = app / "Contents" / "MacOS" / PRODUCT_NAME
    if not executable.is_file():
        raise ValueError(f"local Diony application is incomplete: {executable}")
    try:
        payload = json.loads(payload_metadata.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"invalid desktop payload metadata: {payload_metadata}") from exc
    version = version.strip()
    if not version or payload.get("version") != version:
        raise ValueError("local app version must match the staged desktop payload")
    architecture = architecture.strip()
    if architecture not in {"arm64", "x86_64"}:
        raise ValueError(f"unsupported local Mac architecture: {architecture}")
    files = sorted(
        (path for path in app.rglob("*") if path.is_file() and not path.is_symlink()),
        key=lambda path: path.relative_to(app).as_posix(),
    )
    return {
        "schema_version": 1,
        "product": PRODUCT_NAME,
        "channel": "development",
        "version": version,
        "architecture": architecture,
        "built_at": built_at
        or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "publishable": False,
        "trust": "ad-hoc-local-only",
        "build_recipe": LOCAL_BUILD_RECIPE,
        "apple_notarized": False,
        "payload_files": int(payload.get("files") or 0),
        "payload_sha256": str(payload.get("payload_sha256") or ""),
        "features": payload.get("features", {}),
        "bundle_files": len(files),
        "bundle_bytes": sum(path.stat().st_size for path in files),
        "bundle_sha256": _payload_digest(app, files),
    }


def _proof_is_complete(platform_key: str, proof: dict[str, Any]) -> bool:
    requirements = {
        "mac": ("signed", "notarized", "stapled", "gatekeeper_verified"),
        "windows": ("signed", "timestamped", "signature_verified"),
    }
    return all(proof.get(key) is True for key in requirements[platform_key])


def build_download_manifest(
    *,
    version: str,
    artifacts: dict[str, Path],
    proofs: dict[str, dict[str, Any]],
    channel: str = "development",
    published_at: str | None = None,
) -> dict[str, Any]:
    """Build the future website's exact Mac/Windows download contract."""
    version = version.strip()
    if not version:
        raise ValueError("desktop version is required")
    if set(artifacts) != {"mac", "windows"}:
        raise ValueError("desktop release requires exactly mac and windows artifacts")
    expected_names = {
        "mac": f"DionyAI-{version}-macOS-universal.dmg",
        "windows": f"DionyAI-Setup-{version}-windows-x64.exe",
    }
    for platform_key, path in artifacts.items():
        if not path.is_file():
            raise ValueError(f"missing {platform_key} artifact: {path}")
        if path.name != expected_names[platform_key]:
            raise ValueError(
                f"unexpected {platform_key} artifact filename: {path.name}; "
                f"expected {expected_names[platform_key]}"
            )
    if channel == "stable":
        incomplete = [
            key for key in ("mac", "windows") if not _proof_is_complete(key, proofs.get(key, {}))
        ]
        if incomplete:
            raise ValueError(
                "stable downloads require verified release proof for " + ", ".join(incomplete)
            )
    timestamp = published_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    platform_meta = {
        "mac": {
            "label": "Download for Mac",
            "platform": "macos",
            "architecture": "universal2",
            "minimum_os": "macOS 13",
        },
        "windows": {
            "label": "Download for Windows",
            "platform": "windows",
            "architecture": "x86_64",
            "minimum_os": "Windows 10 64-bit",
        },
    }
    downloads: dict[str, Any] = {}
    for platform_key in ("mac", "windows"):
        path = artifacts[platform_key]
        downloads[platform_key] = {
            **platform_meta[platform_key],
            "filename": path.name,
            "url": f"/downloads/{path.name}",
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
            "proof": proofs.get(platform_key, {}),
        }
    return {
        "schema_version": DOWNLOAD_SCHEMA_VERSION,
        "product": PRODUCT_NAME,
        "channel": channel,
        "version": version,
        "published_at": timestamp,
        "downloads": downloads,
    }


def write_download_manifest(path: Path, manifest: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_json_bytes(manifest))
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build Diony AI desktop release metadata")
    commands = parser.add_subparsers(dest="command", required=True)
    stage = commands.add_parser("stage")
    stage.add_argument("source", type=Path)
    stage.add_argument("target", type=Path)
    stage.add_argument("--version", required=True)
    manifest = commands.add_parser("manifest")
    manifest.add_argument("--version", required=True)
    manifest.add_argument("--mac", required=True, type=Path)
    manifest.add_argument("--windows", required=True, type=Path)
    manifest.add_argument("--mac-proof", required=True)
    manifest.add_argument("--windows-proof", required=True)
    manifest.add_argument("--channel", default="development")
    manifest.add_argument("--published-at")
    manifest.add_argument("--output", required=True, type=Path)
    development = commands.add_parser("development-report")
    development.add_argument("--app", required=True, type=Path)
    development.add_argument("--payload-metadata", required=True, type=Path)
    development.add_argument("--version", required=True)
    development.add_argument("--architecture", required=True)
    development.add_argument("--built-at")
    development.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.command == "stage":
        print(json.dumps(stage_product(args.source, args.target, version=args.version)))
        return 0
    if args.command == "development-report":
        report = build_development_report(
            app=args.app,
            payload_metadata=args.payload_metadata,
            version=args.version,
            architecture=args.architecture,
            built_at=args.built_at,
        )
        write_download_manifest(args.output, report)
        print(args.output)
        return 0
    output = build_download_manifest(
        version=args.version,
        artifacts={"mac": args.mac, "windows": args.windows},
        proofs={
            "mac": json.loads(args.mac_proof),
            "windows": json.loads(args.windows_proof),
        },
        channel=args.channel,
        published_at=args.published_at,
    )
    write_download_manifest(args.output, output)
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
