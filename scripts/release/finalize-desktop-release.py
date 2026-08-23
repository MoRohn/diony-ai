from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "desk" / "src"))

from diony_desk.release import build_download_manifest, write_download_manifest  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Finalize the two-button Diony desktop release")
    parser.add_argument("--channel", default="stable")
    parser.add_argument("--published-at")
    args = parser.parse_args()
    release = json.loads((ROOT / "packaging" / "desktop" / "version.json").read_text())
    output = ROOT / "dist" / "desktop" / release["version"]
    mac = output / release["mac_artifact"]
    windows = output / release["windows_artifact"]
    proofs = {
        "mac": json.loads((output / "macos-proof.json").read_text()),
        "windows": json.loads((output / "windows-proof.json").read_text()),
    }
    manifest = build_download_manifest(
        version=release["version"],
        artifacts={"mac": mac, "windows": windows},
        proofs=proofs,
        channel=args.channel,
        published_at=args.published_at,
    )
    target = write_download_manifest(output / "downloads.json", manifest)
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
