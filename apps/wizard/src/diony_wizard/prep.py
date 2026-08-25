"""Crate-to-system helpers: repo files, switch snippets, health probe, site pack."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path


_BOOTSTRAP_SSH_KEY = os.environ.get("DIONY_SSH_KEY", "").strip()


def repo_root() -> Path:
    explicit = os.environ.get("DIONY_APP_ROOT", "").strip()
    if explicit:
        root = Path(explicit).expanduser().resolve()
        if (root / "infra" / "profiles" / "spark-4x-v1.yaml").is_file():
            return root
        raise FileNotFoundError(f"Diony application profile is missing from {root}")
    here = Path(__file__).resolve()
    for path in [here, *here.parents]:
        if (path / "infra" / "profiles" / "spark-4x-v1.yaml").is_file():
            return path
    raise FileNotFoundError("spark-4x-v1 profile not found above the Studio")


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
    "On-package B200": Path("infra/switch/b200-node.md"),
    "b200-node": Path("infra/switch/b200-node.md"),
}

SKU_BY_PATH = {
    "local": "spark-4x-v1",
    "dual": "spark-2x-v1",
    "lease": "lease-gpu-v1",
    "b300": "b300-v1",
    "b200": "b200-v1",
}

SINGLE_GPU = {
    "b300": {
        "sku": "b300-v1",
        "host": "b300-01",
        "gpu_class": "1×B300",
        "recipe": "b300-v1 TP=1",
        "recipe_class": "b300-tp1",
        "recipe_label": "BRAIN first, TP=1 on this GPU",
        "max_model_len": "1048576",
        "gpu_util": "0.90",
        "fabric": "On-package",
        "label": "Leased B300",
        "gpu_name": "NVIDIA B300",
        "vram": "288 GB class",
        "start_here": "5",
        "other": "b200",
    },
    "b200": {
        "sku": "b200-v1",
        "host": "b200-01",
        "gpu_class": "1×B200",
        "recipe": "b200-v1 TP=1",
        "recipe_class": "b200-tp1",
        "recipe_label": "BRAIN first, TP=1 on this 180 GB GPU; max_model_len 16384",
        "max_model_len": "16384",
        "gpu_util": "0.95",
        "fabric": "On-package B200",
        "label": "Leased B200",
        "gpu_name": "NVIDIA B200",
        "vram": "180 GB",
        "start_here": "6",
        "other": "b300",
    },
}

# Connect: the login Studio uses on the GPU. Not the host, not an email.
SSH_USER_HINT = (
    "Word before @ on the SSH line. Runpod: often root. Not an email."
)
SSH_USER_TEACH = (
    "SSH user is the login name on the GPU. Find the SSH line on the provider "
    "(Runpod Connect, or Enverge instance page). Type only LOGIN, the word before @. "
    "Runpod is often root. Enverge example: ssh my-instance@relay.enverge.ai → type my-instance. "
    "Not your account email, not the host after @, not this laptop’s name. "
    "If you are already sitting on spark-01, App still records this name and runs locally."
)
SSH_USER_PLACEHOLDER = "my-instance"
SSH_USER_PLACEHOLDER_GPU = "root"
SSH_PORT_DEFAULT = "22"
# Worked example from a RunPod Connect dialog. Not a Studio default.
RUNPOD_TCP_EXAMPLE_HOST = "198.13.252.68"
RUNPOD_TCP_EXAMPLE_PORT = "28231"
RUNPOD_TCP_EXAMPLE_LINE = "ssh root@198.13.252.68 -p 28231 -i ~/.ssh/id_ed25519"
RUNPOD_PROXY_EXAMPLE_LINE = "ssh LOGIN@ssh.runpod.io -i ~/.ssh/id_ed25519"
SSH_USER_HINT_GPU = "Word before @. RunPod: root. Not an email."
VAULT_DEFAULT_GPU = "/workspace"
SSH_PORT_HINT_GPU = (
    f"Number after -p. Example {RUNPOD_TCP_EXAMPLE_PORT}. Not Jupyter 8888."
)

# Product addresses. Surfaces are named. Day 2 still binds 127.0.0.1:8787 / :8788.
STUDIO_URL = "http://studio.diony.local"
PRODUCER_URL = "http://producer.diony.local"
WIZARD_BIND = "http://127.0.0.1:8787"
PRODUCER_BIND = "http://127.0.0.1:8788"
VAULT_MOUNT = "/diony/vault"
PRODUCER_PORT = 8788

RUNPOD_CONNECT_DIAGRAM = """RunPod Connect tab                     Studio
HTTP services  Port 8888 Jupyter       SKIP
SSH            LOGIN@ssh.runpod.io     SKIP (no SCP)
SSH over exposed TCP                   USE THIS
  ssh root@198.13.252.68 -p 28231
  Host = 198.13.252.68  SSH user = root  SSH port = 28231
Web terminal                           SKIP
Direct TCP ports  IP:28231 → :22       SAME numbers
Details → Pod volume Mount path        Vault = /workspace
Container disk /models                 weights. Not the vault."""


def runpod_connect_guide(path: str) -> list[dict[str, str]]:
    """One row per RunPod Connect / Details heading. Studio fields copy from USE THIS."""
    if path not in SINGLE_GPU:
        raise KeyError(path)
    return [
        {
            "tab": "Connect",
            "heading": "HTTP services",
            "use": "skip",
            "body": "",
            "example": "",
            "take": "",
        },
        {
            "tab": "Connect",
            "heading": "SSH",
            "use": "skip",
            "body": "",
            "example": "",
            "take": "",
        },
        {
            "tab": "Connect",
            "heading": "SSH over exposed TCP",
            "use": "use",
            "body": "Paste this full line into Runpod quick connect. Studio separates the connection values automatically.",
            "example": RUNPOD_TCP_EXAMPLE_LINE,
            "take": (
                f"Host = {RUNPOD_TCP_EXAMPLE_HOST}   "
                "SSH user = root   "
                f"SSH port = {RUNPOD_TCP_EXAMPLE_PORT}"
            ),
        },
        {
            "tab": "Connect",
            "heading": "Web terminal",
            "use": "skip",
            "body": "",
            "example": "",
            "take": "",
        },
        {
            "tab": "Connect",
            "heading": "Direct TCP ports",
            "use": "check",
            "body": "Same IP and port.",
            "example": "",
            "take": "",
        },
        {
            "tab": "Details",
            "heading": "Pod volume Mount path",
            "use": "use",
            "body": "Stop keeps Volume disk. Terminate deletes it. /models is weights, not the vault.",
            "example": VAULT_DEFAULT_GPU,
            "take": f"Vault = {VAULT_DEFAULT_GPU}",
        },
    ]


def single_gpu_host_hint(path: str) -> str:
    return f"IP after @, before -p. Example {RUNPOD_TCP_EXAMPLE_HOST}."


def single_gpu_connect_teach(path: str) -> list[str]:
    spec = SINGLE_GPU[path]
    other = SINGLE_GPU[spec["other"]]
    return [
        f"Where? is {spec['label']}. Details → GPU must be {spec['gpu_name']} ({spec['vram']}). {other['label']} for this pod is a fail.",
        "Provider is Runpod if you used https://www.runpod.io/. Your name on Site is required.",
        f"Open the Connect tab. Copy SSH over exposed TCP (SCP/SFTP; App Continue rsyncs): {RUNPOD_TCP_EXAMPLE_LINE}. Paste the full line into Runpod quick connect; Studio fills Host, SSH user (the word before @, not the account email), SSH port, and identity. Manual fields remain below.",
        f"Skip HTTP services (Jupyter 8888). Skip SSH to ssh.runpod.io (proxy, no SCP). Skip Web terminal.",
        f"Vault is Details → Pod volume Mount path, usually {VAULT_DEFAULT_GPU}. Stop keeps Volume disk; Terminate deletes it. Container disk /models is weights. Diony mounts /diony/vault.",
        f"Enverge is second (https://enverge.ai/). Same boxes. START HERE §{spec['start_here']}.",
    ]


def single_gpu_connect_fail(path: str) -> list[str]:
    spec = SINGLE_GPU[path]
    other = SINGLE_GPU[spec["other"]]
    short = spec["gpu_name"].replace("NVIDIA ", "")
    return [
        f"Picking {other['label']} for a {short} pod",
        f"Four hostnames. This lease is one {short}.",
        "Pasting the whole ssh line into a manual Host, SSH user, or SSH port field instead of Runpod quick connect",
        "Using ssh.runpod.io as Host (proxy, no SCP — App Continue rsyncs)",
        "Your email as the SSH user",
        "Jupyter 8888 as the SSH port",
        "Leaving SSH port at 22 when the TCP line has -p (rsync fails)",
        "Using container disk /models as the vault",
        "A public cloud bucket (s3:// or open HTTPS) used as the vault",
        "Network Volume in a different data center than this GPU",
    ]


def single_gpu_connect_commands(path: str) -> list[str]:
    spec = SINGLE_GPU[path]
    return [
        f"# RunPod Connect tab. Use SSH over exposed TCP. Skip Jupyter 8888, ssh.runpod.io, Web terminal.",
        f"# Worked example (your IP and -p will differ):",
        f"#   {RUNPOD_TCP_EXAMPLE_LINE}",
        f"# Host: {RUNPOD_TCP_EXAMPLE_HOST}   SSH user: root   SSH port: {RUNPOD_TCP_EXAMPLE_PORT}",
        f"# Direct TCP ports: SSH → {RUNPOD_TCP_EXAMPLE_HOST}:{RUNPOD_TCP_EXAMPLE_PORT} → :22  (same numbers)",
        f"# Vault: Details → Pod volume Mount path → {VAULT_DEFAULT_GPU}",
        f"# Site: {spec['label']} — Details GPU must match. START HERE §{spec['start_here']}. Blueprint 14.",
    ]


def single_gpu_app_teach(path: str) -> list[str]:
    spec = SINGLE_GPU[path]
    if path == "b200":
        brain = (
            f"Brain is {spec['recipe']}, max_model_len {spec['max_model_len']}, "
            f"gpu-memory-utilization {spec['gpu_util']}. 180 GB against ~155.4 GiB weights. "
            "Do not paste B300 1048576 onto B200. App does not start vLLM."
        )
    else:
        brain = (
            f"Brain is {spec['recipe']}, max_model_len {spec['max_model_len']}. "
            "288 GB class. Do not paste Spark TP=4. Do not paste the B200 16k pin as if they were the same card. "
            "App does not start vLLM."
        )
    return [
        f"Nothing to pick. The pack is pinned for this GPU: DeepSeek-V4-Flash-0731 + Wan2.2 A14B (720p) plus diony_sr_v1. Not a Hugging Face latest tag.",
        "Continue SSHes as DIONY_SSH_USER on DIONY_SSH_PORT with DIONY_SSH_KEY (BatchMode), or runs locally on this GPU. Prefer RunPod SSH over exposed TCP (rsync). Hugging Face auth is the Diony-Enterprise HF Token, held by the backend. Nothing to paste. Never Dashscope. How is the apply log.",
        "The pull mounts the vault at /diony/vault, writes the pack into /models, clones Wan2.2, installs diony-finish, and starts Producer.",
        brain,
        "Sequential: drain → unload → load. Never brain and Wan together. Produce expands if brain is up; Produce starts Wan; Production runs 1080 finish and mix.",
        f"Producer opens at {PRODUCER_URL}. After Sign, open {PRODUCER_URL}. Do not quote the Quad-Spark 2–5 day SLA.",
        "Continue starts the pull and opens Ready. The pack can take minutes to hours. Watch it on Ready, then Test GPU. Sign writes site.json onto /diony/vault.",
    ]


def single_gpu_app_fail(path: str) -> list[str]:
    fails = [
        "Picking a different DeepSeek or Wan because a blog said so",
        "Leaving DASH_API_KEY set",
        "Skipping Producer start and treating Studio as the showrunner",
        "Cloud studio / Dashscope as a substitute",
        "Co-resident DeepSeek and Wan A14B",
    ]
    if path == "b200":
        fails.insert(2, "Pasting the B300 1M context command onto this 180 GB card")
    else:
        fails.insert(2, "Pasting Spark TP=4 or the B200 16k pin onto this B300")
    return fails


RECIPE_CLASS = {
    "local": "spark-tp",
    "dual": "spark-tp2",
    "lease": "lease-datacenter",
    "b300": "b300-tp1",
    "b200": "b200-tp1",
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
    "b200": "B200 pack — DeepSeek 0731 + Wan2.2 A14B",
}

SHOW_FLOORS = "Idea, Production, Gallery"


def parse_nfs_source(nas: str) -> tuple[str, str] | None:
    """Host + export from an NFS address. Placeholders and public URLs return None."""
    raw = (nas or "").strip()
    if not raw:
        return None
    low = raw.lower()
    if low in {"nfs://vault", "nfs://vault.internal"}:
        return None
    if vault_bind_source(raw):
        return None
    if vault_is_public(raw):
        return None
    nfs = re.match(r"^nfs://([^/]+)/(.+)$", raw, re.I)
    if nfs:
        share = nfs.group(2).strip()
        return nfs.group(1), "/" + share.lstrip("/")
    colon = re.match(r"^([^:/]+):(/[^ \t]+)$", raw)
    if colon:
        return colon.group(1), colon.group(2)
    return None


def vault_bind_source(nas: str) -> str | None:
    """Runpod Volume disk (and similar) already mounted on the GPU, usually at /workspace."""
    raw = (nas or "").strip()
    if not raw:
        return None
    low = raw.lower().rstrip("/")
    aliases = {
        "/workspace",
        "workspace",
        "bind:/workspace",
        "volume-disk",
        "volumedisk",
        "volume disk",
    }
    if low in aliases:
        return "/workspace"
    if low.startswith("bind:"):
        path = raw.split(":", 1)[1].strip()
        return path or None
    if raw.rstrip("/") == VAULT_MOUNT.rstrip("/"):
        return None
    if raw.startswith("/") and "//" not in raw and ":" not in raw[1:]:
        return raw
    return None


def vault_export(workbook: dict[str, dict[str, str]] | None) -> str:
    book = workbook or {}
    connect = book.get("connect") or {}
    vault = book.get("vault") or {}
    return str(connect.get("nas_ip") or vault.get("nas_ip") or "").strip()


VAULT_ENSURE_FN = r"""
_diony_ensure_vault() {
  src=$1
  dest=$2
  mkdir -p "$(dirname "$dest")" "$src"
  if [ -L "$dest" ]; then
    ln -sfn "$src" "$dest"
    return 0
  fi
  if command -v mountpoint >/dev/null 2>&1 && mountpoint -q "$dest"; then
    return 0
  fi
  if [ ! -e "$dest" ]; then
    mkdir -p "$dest"
    if mount --bind "$src" "$dest" 2>/dev/null; then
      return 0
    fi
    rmdir "$dest" 2>/dev/null || true
    ln -sfn "$src" "$dest"
    return 0
  fi
  if mount --bind "$src" "$dest" 2>/dev/null; then
    return 0
  fi
  echo "mount --bind $src $dest denied; linking" >&2
  bak="${dest}.prelink"
  if [ -d "$dest" ] && [ ! -L "$dest" ]; then
    rm -rf "$bak"
    mv "$dest" "$bak" || true
  fi
  if [ -d "$dest" ] && [ ! -L "$dest" ]; then
    echo "could not replace $dest; leaving directory" >&2
    return 0
  fi
  ln -sfn "$src" "$dest"
  if [ -d "$bak" ]; then
    mkdir -p "$dest"
    cp -a "$bak/." "$dest/" 2>/dev/null || true
    rm -rf "$bak"
  fi
}
"""


def vault_mount_lines(nas: str, mount: str = VAULT_MOUNT) -> list[str]:
    """Put the recorded share at /diony/vault. Bind if allowed; symlink on RunPod."""
    bind = vault_bind_source(nas)
    if bind:
        return [
            VAULT_ENSURE_FN.strip(),
            f"mkdir -p {shlex.quote(bind)}",
            f"_diony_ensure_vault {shlex.quote(bind)} {shlex.quote(mount)}",
        ]
    parsed = parse_nfs_source(nas)
    if not parsed:
        return [f"mkdir -p {shlex.quote(mount)}"]
    host, share = parsed
    return [
        f"mkdir -p {shlex.quote(mount)}",
        f"if ! mountpoint -q {shlex.quote(mount)} 2>/dev/null; then",
        f"  mount -t nfs -o defaults,_netdev,hard,timeo=600 {host}:{share} {shlex.quote(mount)} || true",
        "fi",
    ]


def producer_open_href(
    dry_run: bool,
    path: str | None = None,
    workbook: dict[str, dict[str, str]] | None = None,
) -> str:
    """Click target for Open Producer. Always the product URL."""
    _ = dry_run, path, workbook
    return PRODUCER_URL


def hosts_commands(host: str) -> list[str]:
    """Optional GPU hosts line. Open Producer is always the product URL."""
    name = (host or "spark-01").strip() or "spark-01"
    return [
        f"# Product URL is {PRODUCER_URL}. If DNS is missing on this laptop:",
        f"# echo \"$(getent hosts {name} | awk '{{print $1}}') producer.diony.local studio.diony.local\" | sudo tee -a /etc/hosts",
    ]


def producer_start_commands(*, cluster: bool = False) -> list[str]:
    """What Studio App runs (and How shows). Sequential SKU: Producer only."""
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
                "export DIONY_PYTHON=${DIONY_PYTHON:-/usr/bin/python3}",
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
    if path not in ("local", "dual", "b300", "b200"):
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
            "id": SINGLE_GPU["b300"]["sku"],
            "recipe": SINGLE_GPU["b300"]["recipe"],
            "recipe_label": SINGLE_GPU["b300"]["recipe_label"],
        },
        "b200": {
            "id": SINGLE_GPU["b200"]["sku"],
            "recipe": SINGLE_GPU["b200"]["recipe"],
            "recipe_label": SINGLE_GPU["b200"]["recipe_label"],
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
    if path in SINGLE_GPU:
        spec = SINGLE_GPU[path]
        cmds.append(f"# Brain is auto: BRAIN first, {spec['recipe']}")
        cmds.append(
            "vllm serve /models/deepseek/DeepSeek-V4-Flash-0731 "
            "--served-model-name DeepSeek-V4-Flash-0731 "
            "--trust-remote-code --tensor-parallel-size 1 "
            f"--max-model-len {spec['max_model_len']} "
            f"--gpu-memory-utilization {spec['gpu_util']}"
        )
    elif path == "dual":
        cmds.append("# Replicate /models to spark-02")
        cmds.append("# Brain is auto: BRAIN first, spark-2x-v1 TP=2. Sequential SPLIT.")
        cmds.append(
            "vllm serve /models/deepseek/DeepSeek-V4-Flash-0731 "
            "--served-model-name DeepSeek-V4-Flash-0731 "
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


def producer_href(
    dry_run: bool,
    path: str | None = None,
    workbook: dict[str, dict[str, str]] | None = None,
) -> str:
    """Where Open Producer actually goes."""
    return producer_open_href(dry_run, path, workbook)


def lease_inventory_defaults(path: str | None, gpu_class: str = "") -> dict[str, str]:
    """Interconnect and matched image come from the signed Site, not a quiz."""
    if path in SINGLE_GPU:
        return {"fabric": SINGLE_GPU[path]["fabric"], "os_matched": "yes"}
    if path == "dual":
        return {"fabric": "CX-7 RoCE", "os_matched": "yes"}
    if path == "lease":
        fabric = "RoCE" if "GB10" in gpu_class else "InfiniBand"
        return {"fabric": fabric, "os_matched": "yes"}
    return {}


def parse_lease_hosts(raw: str, path: str | None) -> tuple[list[str] | None, str | None]:
    parts = [p.strip() for p in re.split(r"[\s,;]+", raw or "") if p.strip()]
    if path in SINGLE_GPU:
        if len(parts) != 1:
            return None, f"Need one hostname for {SINGLE_GPU[path]['label']}."
        host_err = parse_gpu_ssh_host(parts[0])
        if host_err:
            return None, host_err
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


def parse_gpu_ssh_host(raw: str) -> str | None:
    """Host is one IP or hostname. Not a pasted ssh line, not the RunPod proxy."""
    text = (raw or "").strip()
    low = text.lower()
    if low.startswith("ssh ") or "@" in text:
        return (
            "Host is the IP (or hostname) before -p on SSH over exposed TCP. "
            "Do not paste the ssh line."
        )
    if low in {"ssh.runpod.io", "runpod.io"}:
        return (
            "ssh.runpod.io is the proxy (no SCP). "
            "Use SSH over exposed TCP: Host is the IP before -p."
        )
    if re.fullmatch(r"[\d.]+:\d+", text):
        return "Host is the IP only. Type the number after the colon in SSH port."
    if " " in text or "\t" in text:
        return "Host is one IP or hostname. SSH port is a separate box."
    return None


def parse_ssh_user(raw: str) -> str | None:
    """Reject a pasted SSH command or email. One login name only."""
    text = (raw or "").strip()
    if not text:
        return (
            "SSH user is the login name on the GPU. "
            "Prefer Connect → SSH over exposed TCP: type only the word before @ (root)."
        )
    low = text.lower()
    if low.startswith("ssh ") or text.count("@") >= 1:
        return "Type only the login before @, not the whole SSH line and not an email."
    if any(ch in text for ch in (" ", "/", "\\", ":", ";")):
        return "SSH user is one login name. No spaces, no path, no host."
    if len(text) > 64:
        return "SSH user looks too long. Use the login before @."
    return None


def parse_ssh_port(raw: str) -> tuple[int | None, str | None]:
    """Optional SSH port. Default 22. RunPod TCP uses the number after -p."""
    text = (raw or "").strip()
    if not text:
        return int(SSH_PORT_DEFAULT), None
    low = text.lower()
    if low.startswith("ssh ") or "@" in text:
        return None, (
            "Type only the -p number from SSH over exposed TCP, "
            "not the whole SSH line."
        )
    if text in {"8888", "8889"}:
        return None, "SSH port is not Jupyter 8888. Use the -p number from SSH over exposed TCP."
    match = re.fullmatch(r"(?:-p\s*)?(\d{1,5})", text, re.I)
    if not match:
        return None, (
            f"SSH port is a number (default {SSH_PORT_DEFAULT}; "
            f"RunPod TCP example {RUNPOD_TCP_EXAMPLE_PORT})."
        )
    port = int(match.group(1))
    if port < 1 or port > 65535:
        return None, "SSH port must be 1–65535."
    if port == 8888:
        return None, "SSH port is not Jupyter 8888. Use the -p number from SSH over exposed TCP."
    return port, None


def bind_ssh_env(connect: dict[str, str] | None) -> None:
    """Point App apply at the Connect login, TCP port, and optional identity."""
    data = connect or {}
    user = str(data.get("ssh_user") or "").strip()
    if user:
        os.environ["DIONY_SSH_USER"] = user
    port = str(data.get("ssh_port") or "").strip()
    if port:
        os.environ["DIONY_SSH_PORT"] = port
    identity = str(data.get("ssh_identity_file") or "").strip()
    if identity:
        os.environ["DIONY_SSH_KEY"] = str(Path(identity).expanduser())
    elif _BOOTSTRAP_SSH_KEY:
        os.environ["DIONY_SSH_KEY"] = _BOOTSTRAP_SSH_KEY
    else:
        os.environ.pop("DIONY_SSH_KEY", None)


def unbind_ssh_env() -> None:
    """Drop the connection values recorded by Studio after Start over."""
    os.environ.pop("DIONY_SSH_USER", None)
    os.environ.pop("DIONY_SSH_PORT", None)
    if _BOOTSTRAP_SSH_KEY:
        os.environ["DIONY_SSH_KEY"] = _BOOTSTRAP_SSH_KEY
    else:
        os.environ.pop("DIONY_SSH_KEY", None)


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

    Leased Dual-Spark, Leased B300, and Leased B200 keep App and Producer as operator pages.
    The model pack is recorded when App is continued. Brain is auto.
    """
    hosts, err = parse_lease_hosts(answers.get("hosts", ""), path)
    if err or not hosts:
        raise ValueError(err or "Need hosts.")
    defaults = lease_inventory_defaults(path, gpu_class)
    nas = str(answers.get("nas_ip", "")).strip()
    if not nas:
        nas = VAULT_DEFAULT_GPU if path in SINGLE_GPU else "nfs://vault"
    export = VAULT_MOUNT
    inventory = dict(defaults)
    ssh_user = str(answers.get("ssh_user", "")).strip()
    if not ssh_user:
        ssh_user = "root" if path in SINGLE_GPU else "spark"
    err = parse_ssh_user(ssh_user)
    if err:
        raise ValueError(err)
    inventory["ssh_user"] = ssh_user
    connect_row: dict[str, str] = {
        "hosts": "\n".join(hosts),
        "nas_ip": nas,
        "export": export,
        "ssh_user": ssh_user,
    }
    identity = str(answers.get("ssh_identity_file") or "").strip()
    if identity:
        connect_row["ssh_identity_file"] = str(Path(identity).expanduser())
    if path in SINGLE_GPU:
        port, port_err = parse_ssh_port(str(answers.get("ssh_port", "")).strip())
        if port_err or port is None:
            raise ValueError(port_err or "SSH port is a number.")
        connect_row["ssh_port"] = str(port)
        inventory["ssh_port"] = str(port)
        bind_ssh_env(connect_row)
    else:
        os.environ["DIONY_SSH_USER"] = ssh_user
    if path in SINGLE_GPU:
        inventory["s1"] = hosts[0]
    elif path == "dual":
        inventory["s1"] = hosts[0]
        inventory["s2"] = hosts[1]
    else:
        for index, host in enumerate(hosts, start=1):
            inventory[f"s{index}"] = host
    out: dict[str, dict[str, str]] = {
        "connect": connect_row,
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
    if path not in ("lease", "dual", "b300", "b200"):
        return None
    workbook = workbook or {}
    gpu = gpu_class or str(workbook.get("path", {}).get("gpu_class", ""))
    if path in SINGLE_GPU:
        gpu = SINGLE_GPU[path]["gpu_class"]
    if path == "dual":
        gpu = "2×GB10"
    defaults = lease_inventory_defaults(path, gpu)
    sku = SKU_BY_PATH[path]
    vault = workbook.get("vault") or {}
    connect = workbook.get("connect") or {}
    nas = str(vault.get("nas_ip") or connect.get("nas_ip") or "nfs://vault").strip()
    export = str(vault.get("export") or connect.get("export") or "/diony/vault").strip()
    split = "SPLIT 2+2 after BRAIN"
    if path in SINGLE_GPU:
        split = "One engine at a time"
    elif path == "dual":
        split = "Sequential TP=2 pair; one engine at a time"
    return {
        "sku": sku,
        "gpu_class": gpu,
        "nodes": site_nodes(path, workbook),
        "interconnect": defaults.get("fabric", ""),
        "vault": f"{nas} → {export}",
        "weights": "Studio App pulls DeepSeek 0731 + Wan2.2 A14B + diony_sr_v1, then go offline",
        "first_mode": "BRAIN (DeepSeek)",
        "split": split,
        "producer_url": producer_url(path, True),
        "serving": serving_rel(sku),
        "floors": SHOW_FLOORS,
        "program": "Overlap stitch, 8–16 frames",
    }


def site_nodes(path: str | None, workbook: dict[str, dict[str, str]]) -> list[str]:
    connect_hosts = [
        part
        for part in str((workbook.get("connect") or {}).get("hosts") or "")
        .replace(",", " ")
        .split()
        if part
    ]
    inventory = workbook.get("inventory", {})
    if path in SINGLE_GPU:
        host = (
            (connect_hosts[0] if connect_hosts else "")
            or str(inventory.get("s1", "")).strip()
            or SINGLE_GPU[path]["host"]
        )
        return [host]
    if path == "dual":
        if len(connect_hosts) >= 2:
            return connect_hosts[:2]
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
        "vault": VAULT_MOUNT,
        "vault_export": vault_export(workbook),
        "nodes": site_nodes(path, workbook),
        "producer_url": producer_url(path, dry_run),
        "producer_href": producer_href(dry_run, path, workbook),
        "dry_run": dry_run,
    }
    inventory = workbook.get("inventory", {})
    gpu_class = workbook.get("path", {}).get("gpu_class", "")
    if path in SINGLE_GPU:
        gpu_class = SINGLE_GPU[path]["gpu_class"]
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
