"""Crate-to-system helpers: repo files, switch snippets, health probe, site pack."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path


def repo_root() -> Path:
    here = Path(__file__).resolve()
    for path in [here, *here.parents]:
        if (path / "infra" / "profiles" / "spark-4x-v1.yaml").is_file():
            return path
    raise FileNotFoundError("spark-4x-v1 profile not found above the Wizard")


SNIPPETS = {
    "CRS804-4DDQ-HRM": Path("infra/switch/crs804-200g.rsc"),
    "CRS812-8DS-2DQ-2DDQ": Path("infra/switch/crs812-200g.rsc"),
    "lease-interconnect": Path("infra/switch/lease-interconnect.md"),
    "InfiniBand": Path("infra/switch/lease-interconnect.md"),
    "RoCE": Path("infra/switch/lease-interconnect.md"),
    "NVLink": Path("infra/switch/lease-interconnect.md"),
    "CX-7 RoCE": Path("infra/switch/spark-2x-interconnect.md"),
    "On-package": Path("infra/switch/b300-node.md"),
    "b300-node": Path("infra/switch/b300-node.md"),
}

SKU_BY_PATH = {
    "local": "spark-4x-v1",
    "dual": "spark-2x-v1",
    "lease": "lease-gpu-v1",
    "b300": "b300-v1",
}

# Product addresses. Surfaces are named. Day 2 still binds 127.0.0.1:8787 / :8788.
WIZARD_URL = "http://wizard.diony.local"
PRODUCER_URL = "http://producer.diony.local"
WIZARD_BIND = "http://127.0.0.1:8787"
PRODUCER_BIND = "http://127.0.0.1:8788"

RECIPE_CLASS = {
    "local": "spark-tp",
    "dual": "spark-tp2",
    "lease": "lease-datacenter",
    "b300": "b300-tp1",
}

# Pinned SKU packs. "Latest" means the current Diony pin, never a `latest` tag.
# Camera is Wan2.2 A14B (720p negative). TI2V-5B is previz and is not in the pack.
_BRAIN = (
    "deepseek-ai/DeepSeek-V4-Flash-0731",
    "/models/deepseek/DeepSeek-V4-Flash-0731",
)
_WAN_CAMERA = (
    ("Wan-AI/Wan2.2-T2V-A14B", "/models/wan/Wan2.2-T2V-A14B"),
    ("Wan-AI/Wan2.2-I2V-A14B", "/models/wan/Wan2.2-I2V-A14B"),
    ("Wan-AI/Wan2.2-S2V-14B", "/models/wan/Wan2.2-S2V-14B"),
    ("Wan-AI/Wan2.2-Animate-14B", "/models/wan/Wan2.2-Animate-14B"),
    ("alibaba-pai/Wan2.2-VACE-Fun-A14B", "/models/wan/Wan2.2-VACE-Fun-A14B"),
)

PACK_LABEL = {
    "local": "Quad-Spark pack — DeepSeek 0731 + Wan2.2 A14B",
    "dual": "Dual-Spark pack — DeepSeek 0731 + Wan2.2 A14B",
    "b300": "B300 pack — DeepSeek 0731 + Wan2.2 A14B",
}

SHOW_FLOORS = "Idea, Dailies, Program, Lock"


def producer_start_commands(*, cluster: bool = False) -> list[str]:
    """What Wizard App runs (and How shows). Sequential SKU: Producer only."""
    cmds = ["pip install -e packages/contracts -e apps/wan -e apps/finish -e apps/producer"]
    if cluster:
        cmds.extend(
            [
                "test -d /opt/wan22/.git || git clone --depth 1 https://github.com/Wan-Video/Wan2.2.git /opt/wan22",
                "export DIONY_WAN_REPO=/opt/wan22",
                "export PYTHONPATH=/opt/wan22${PYTHONPATH:+:$PYTHONPATH}",
                "pip install -r /opt/wan22/requirements.txt",
                "unset DASH_API_KEY DASHSCOPE_API_KEY",
                "export DIONY_VAULT=/diony/vault",
                "export DIONY_BIND=0.0.0.0",
                "export DIONY_PYTHON=$(command -v python3)",
                "export DIONY_WAN_URL=http://127.0.0.1:8010",
                "export DIONY_FINISH_URL=http://127.0.0.1:8020",
                "export DIONY_DRAIN_BRAIN='pkill -f \"vllm serve\" || true'",
                "export DIONY_DRAIN_WAN='pkill -f diony-wan || true'",
                "# Sequential: Produce starts diony-wan for FARM. Picture lock starts diony-finish for 1080.",
                "# Do not start diony-wan and vllm together. Do not start diony-wan and diony-finish together.",
            ]
        )
    cmds.append("diony-producer")
    cmds.append(
        f"# Opens {PRODUCER_URL}. Produce farms Wan2.2 720p negatives. Picture lock writes the 1080 master."
    )
    return cmds


def model_pack(path: str | None) -> dict | None:
    """Site-sized DeepSeek + Wan pack. Brain recipe is part of the pack."""
    if path not in ("local", "dual", "b300"):
        return None
    recipes = {
        "local": {
            "id": "spark-4x-v1",
            "recipe": "spark-4x-v1 TP=4",
            "recipe_label": "BRAIN first, TP=4 across Quad-Spark",
        },
        "dual": {
            "id": "spark-2x-v1",
            "recipe": "spark-2x-v1 TP=2",
            "recipe_label": "BRAIN first, TP=2 on this pair; sequential SPLIT",
        },
        "b300": {
            "id": "b300-v1",
            "recipe": "b300-v1 TP=1",
            "recipe_label": "BRAIN first, TP=1 on this GPU",
        },
    }
    spec = recipes[path]
    return {
        "id": spec["id"],
        "label": PACK_LABEL[path],
        "brain": "DeepSeek-V4-Flash-0731 (~167 GB)",
        "brain_id": _BRAIN[0],
        "camera": "Wan2.2 T2V · I2V · S2V · Animate · VACE Fun (720p)",
        "camera_ids": [item[0] for item in _WAN_CAMERA],
        "recipe": spec["recipe"],
        "recipe_label": spec["recipe_label"],
        "first_mode": "BRAIN",
        "tool": "huggingface-cli → /models, then offline",
        "commands": pack_pull_commands(path),
    }


def pack_pull_commands(path: str | None) -> list[str]:
    cmds = [
        f"huggingface-cli download {_BRAIN[0]} --local-dir {_BRAIN[1]}",
    ]
    cmds.extend(
        f"huggingface-cli download {hid} --local-dir {dest}" for hid, dest in _WAN_CAMERA
    )
    cmds.append("unset DASH_API_KEY")
    cmds.append(
        "huggingface-cli download ai-forever/Real-ESRGAN RealESRGAN_x2.pth "
        "--local-dir /models/diony/diony_sr_v1"
    )
    if path == "b300":
        cmds.append("# Brain is auto: BRAIN first, b300-v1 TP=1")
        cmds.append(
            "vllm serve /models/deepseek/DeepSeek-V4-Flash-0731 "
            "--trust-remote-code --tensor-parallel-size 1 "
            "--max-model-len 1048576 --gpu-memory-utilization 0.90"
        )
    elif path == "dual":
        cmds.append("# Replicate /models to spark-02")
        cmds.append("# Brain is auto: BRAIN first, spark-2x-v1 TP=2. Sequential SPLIT.")
        cmds.append(
            "vllm serve /models/deepseek/DeepSeek-V4-Flash-0731 "
            "--trust-remote-code --kv-cache-dtype nvfp4_ds_mla --block-size 256 "
            "--tensor-parallel-size 2 --distributed-executor-backend mp --nnodes 2 "
            "--enable-expert-parallel --max-model-len 1048576 --max-num-seqs 6 "
            "--gpu-memory-utilization 0.85 "
            "--speculative-config "
            '\'{"method":"dspark","num_speculative_tokens":3,'
            '"draft_sample_method":"probabilistic"}\''
        )
    else:
        cmds.append("# Replicate /models to spark-02..04")
        cmds.append("# Brain is auto: BRAIN first, spark-4x-v1 TP=4. See serving notes.")
    return cmds


def expand_site_models(path: str, answers: dict[str, str]) -> dict[str, dict[str, str]]:
    """Record the site pack and auto-config Brain. Operator does not pick models."""
    pack = model_pack(path)
    if pack is None:
        raise ValueError("No model pack for this site.")
    label = str(answers.get("pack", "")).strip() or pack["label"]
    return {
        "weights": {
            "pack": pack["id"],
            "label": label,
            "brain": pack["brain_id"],
            "camera": ",".join(pack["camera_ids"]),
            "path": "huggingface-cli",
            "offline": "yes",
        },
        "brain_smoke": {
            "mode": pack["first_mode"],
            "recipe": pack["recipe"],
        },
    }


def snippet_text(sku: str) -> tuple[str, str]:
    rel = SNIPPETS.get(sku)
    if rel is None:
        raise KeyError(sku)
    path = repo_root() / rel
    return str(rel), path.read_text()


def health_probe() -> dict:
    script = repo_root() / "infra" / "health" / "check_cluster.py"
    result = subprocess.run(
        [sys.executable, str(script)],
        check=False,
        capture_output=True,
        text=True,
        cwd=str(repo_root()),
    )
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        payload = {
            "ok": False,
            "dry_run": True,
            "checks": [],
            "detail": result.stderr or result.stdout,
        }
    payload["exit_code"] = result.returncode
    return payload


def manifest_text() -> str:
    return (repo_root() / "infra" / "models" / "manifest.yaml").read_text()


def vault_fstab_text() -> str:
    return (repo_root() / "infra" / "storage" / "vault.fstab.example").read_text()


def profile_rel(sku: str) -> str:
    return f"infra/profiles/{sku}.yaml"


def serving_rel(sku: str) -> str:
    return f"infra/profiles/{sku}.serving.md"


def profile_text(sku: str) -> str:
    return (repo_root() / profile_rel(sku)).read_text()


def serving_text(sku: str) -> str:
    path = repo_root() / serving_rel(sku)
    if not path.is_file():
        return ""
    return path.read_text()


def producer_url(path: str | None = None, dry_run: bool = True) -> str:
    """Operator-facing Producer address. Same on every site."""
    return PRODUCER_URL


def producer_href(dry_run: bool) -> str:
    """Where Open Producer actually goes. Day 2 is the laptop bind."""
    return PRODUCER_BIND if dry_run else PRODUCER_URL


def lease_inventory_defaults(path: str | None, gpu_class: str = "") -> dict[str, str]:
    """Interconnect and matched image come from the signed Site, not a quiz."""
    if path == "b300":
        return {"fabric": "On-package", "os_matched": "yes"}
    if path == "dual":
        return {"fabric": "CX-7 RoCE", "os_matched": "yes"}
    if path == "lease":
        fabric = "RoCE" if "GB10" in gpu_class else "InfiniBand"
        return {"fabric": fabric, "os_matched": "yes"}
    return {}


def parse_lease_hosts(raw: str, path: str | None) -> tuple[list[str] | None, str | None]:
    parts = [p.strip() for p in re.split(r"[\s,;]+", raw or "") if p.strip()]
    if path == "b300":
        if len(parts) != 1:
            return None, "Need one hostname for Leased B300."
        return parts, None
    if path == "dual":
        if len(parts) != 2:
            return None, "Need two hostnames for Leased Dual-Spark."
        if len(set(parts)) != 2:
            return None, "Two different hosts. This pair needs fabric between them."
        return parts, None
    if path == "lease":
        if len(parts) != 4:
            return None, "Need four hostnames, one per GPU."
        if len(set(parts)) != 4:
            return None, "Four different hosts. Mixed boxes fail SPLIT."
        return parts, None
    return None, "Connect is a lease line."


def vault_is_public(nas: str) -> bool:
    low = nas.lower()
    if "s3://" in low or "s3.amazonaws.com" in low:
        return True
    return low.startswith("https://") and "s3" in low


def expand_lease_connect(
    path: str,
    gpu_class: str,
    answers: dict[str, str],
) -> dict[str, dict[str, str]]:
    """Record host + vault. Four-node lease also fills the rest of the software pack.

    Leased Dual-Spark and Leased B300 keep App and Producer as operator pages.
    The model pack is recorded when App is continued. Brain is auto.
    """
    hosts, err = parse_lease_hosts(answers.get("hosts", ""), path)
    if err or not hosts:
        raise ValueError(err or "Need hosts.")
    defaults = lease_inventory_defaults(path, gpu_class)
    nas = str(answers.get("nas_ip", "")).strip() or "nfs://vault"
    export = "/diony/vault"
    inventory = dict(defaults)
    ssh_user = str(answers.get("ssh_user", "")).strip() or "spark"
    inventory["ssh_user"] = ssh_user
    os.environ["DIONY_SSH_USER"] = ssh_user
    if path == "b300":
        inventory["s1"] = hosts[0]
    elif path == "dual":
        inventory["s1"] = hosts[0]
        inventory["s2"] = hosts[1]
    else:
        for index, host in enumerate(hosts, start=1):
            inventory[f"s{index}"] = host
    out: dict[str, dict[str, str]] = {
        "connect": {
            "hosts": "\n".join(hosts),
            "nas_ip": nas,
            "export": export,
            "ssh_user": ssh_user,
        },
        "inventory": inventory,
        "vault": {"nas_ip": nas, "export": export},
    }
    if path == "lease":
        out["weights"] = {"path": "huggingface-cli on node-01"}
        out["brain_smoke"] = {"mode": "BRAIN"}
        out["producer"] = {"url": producer_url(path, True), "ready": "Ready"}
    return out


def lease_plan(
    path: str | None,
    gpu_class: str = "",
    workbook: dict[str, dict[str, str]] | None = None,
) -> dict | None:
    """What Diony will run on this lease. Shown on Connect and Producer."""
    if path not in ("lease", "dual", "b300"):
        return None
    workbook = workbook or {}
    gpu = gpu_class or str(workbook.get("path", {}).get("gpu_class", ""))
    if path == "b300":
        gpu = "1×B300"
    if path == "dual":
        gpu = "2×GB10"
    defaults = lease_inventory_defaults(path, gpu)
    sku = SKU_BY_PATH[path]
    vault = workbook.get("vault") or {}
    connect = workbook.get("connect") or {}
    nas = str(vault.get("nas_ip") or connect.get("nas_ip") or "nfs://vault").strip()
    export = str(vault.get("export") or connect.get("export") or "/diony/vault").strip()
    split = "SPLIT 2+2 after BRAIN"
    if path == "b300":
        split = "One engine at a time"
    elif path == "dual":
        split = "Sequential TP=2 pair; one engine at a time"
    return {
        "sku": sku,
        "gpu_class": gpu,
        "nodes": site_nodes(path, workbook),
        "interconnect": defaults.get("fabric", ""),
        "vault": f"{nas} → {export}",
        "weights": "Wizard App pulls DeepSeek 0731 + Wan2.2 A14B + diony_sr_v1, then go offline",
        "first_mode": "BRAIN (DeepSeek)",
        "split": split,
        "producer_url": producer_url(path, True),
        "serving": serving_rel(sku),
        "floors": SHOW_FLOORS,
        "cut": "Prompt-to-edit on a take (VACE)",
        "program": "Overlap stitch, 8–16 frames",
    }


def site_nodes(path: str | None, workbook: dict[str, dict[str, str]]) -> list[str]:
    inventory = workbook.get("inventory", {})
    if path == "b300":
        host = str(inventory.get("s1", "")).strip() or "b300-01"
        return [host]
    if path == "dual":
        found = [
            str(inventory.get("s1", "")).strip() or "spark-01",
            str(inventory.get("s2", "")).strip() or "spark-02",
        ]
        return found
    keys = ("s1", "s2", "s3", "s4")
    found = [str(inventory[k]).strip() for k in keys if str(inventory.get(k, "")).strip()]
    if found:
        return found
    if path == "lease":
        return ["node-01", "node-02", "node-03", "node-04"]
    return ["spark-01", "spark-02", "spark-03", "spark-04"]


def site_pack(
    path: str,
    sku: str,
    workbook: dict[str, dict[str, str]],
    dry_run: bool,
    *,
    include_texts: bool = False,
) -> dict:
    pack = {
        "sku": sku,
        "compute_path": path,
        "recipe_class": RECIPE_CLASS.get(path, ""),
        "profile": profile_rel(sku),
        "serving_notes": serving_rel(sku),
        "weights_manifest": "infra/models/manifest.yaml",
        "vault_fstab": "infra/storage/vault.fstab.example",
        "vault": "/diony/vault",
        "nodes": site_nodes(path, workbook),
        "producer_url": producer_url(path, dry_run),
        "dry_run": dry_run,
    }
    inventory = workbook.get("inventory", {})
    gpu_class = workbook.get("path", {}).get("gpu_class", "")
    if path == "b300":
        gpu_class = "1×B300"
    if path == "dual":
        gpu_class = "2×GB10"
    defaults = lease_inventory_defaults(path, gpu_class)
    fabric = str(inventory.get("fabric") or defaults.get("fabric") or "").strip()
    if fabric:
        pack["interconnect"] = fabric
    if include_texts:
        pack["profile_text"] = profile_text(sku)
        pack["serving_text"] = serving_text(sku)
        pack["manifest_text"] = manifest_text()
        pack["fstab_text"] = vault_fstab_text()
    return pack
