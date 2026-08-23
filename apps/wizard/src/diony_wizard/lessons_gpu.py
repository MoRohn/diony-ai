"""Shared Connect / App / Producer lessons for Leased B300 and Leased B200.

Never ImportError on a stale prep module (uvicorn reload=False). Import prep as
a module and getattr every symbol. Empty fallbacks beat a dead Studio.
"""

from __future__ import annotations

from diony_wizard.lesson import Field, Lesson

try:
    from diony_wizard import prep as _prep
except Exception:  # ImportError or a circular import on a stale process
    _prep = None

# Keep the diagram here. Importing it from prep 500s Site Continue if Studio
# started before that symbol existed (uvicorn does not reload).
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

_FALLBACK_SINGLE_GPU = {
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

_FALLBACK_PACK_LABEL = {
    "local": "Quad-Spark pack — DeepSeek 0731 + Wan2.2 A14B",
    "dual": "Dual-Spark pack — DeepSeek 0731 + Wan2.2 A14B",
    "b300": "B300 pack — DeepSeek 0731 + Wan2.2 A14B",
    "b200": "B200 pack — DeepSeek 0731 + Wan2.2 A14B",
}

_PRODUCER_URL = "http://producer.diony.local"
_SHOW_FLOORS = "Idea, Production, Gallery"
_SSH_PORT_DEFAULT = "22"
_VAULT_DEFAULT_GPU = "/workspace"
_SSH_USER_PLACEHOLDER_GPU = "root"
_SSH_USER_HINT_GPU = "Word before @. RunPod: root. Not an email."
_SSH_PORT_HINT_GPU = "Number after -p. Example 28231. Not Jupyter 8888."
HF_TOKEN_URL = "https://huggingface.co/settings/tokens"


def hf_token_field() -> Field:
    return Field(
        "hf_token",
        "HF_TOKEN",
        "password",
        placeholder="hf_…",
        prefill=False,
        source=f"{HF_TOKEN_URL} → Create new token +",
        hint="Open that page. Create a new token. Paste it here. Gated DeepSeek and Wan pulls need it. Never Dashscope.",
    )


def _attr(name: str, default):
    if _prep is None:
        return default
    return getattr(_prep, name, default)


def _call(name: str, fallback, *args, **kwargs):
    fn = getattr(_prep, name, None) if _prep is not None else None
    if callable(fn):
        try:
            return fn(*args, **kwargs)
        except Exception:
            return fallback(*args, **kwargs)
    return fallback(*args, **kwargs)


SINGLE_GPU = _attr("SINGLE_GPU", _FALLBACK_SINGLE_GPU)
PACK_LABEL = _attr("PACK_LABEL", _FALLBACK_PACK_LABEL)
PRODUCER_URL = _attr("PRODUCER_URL", _PRODUCER_URL)
SHOW_FLOORS = _attr("SHOW_FLOORS", _SHOW_FLOORS)
SSH_PORT_DEFAULT = _attr("SSH_PORT_DEFAULT", _SSH_PORT_DEFAULT)
SSH_PORT_HINT_GPU = _attr("SSH_PORT_HINT_GPU", _SSH_PORT_HINT_GPU)
SSH_USER_HINT_GPU = _attr("SSH_USER_HINT_GPU", _SSH_USER_HINT_GPU)
SSH_USER_PLACEHOLDER_GPU = _attr("SSH_USER_PLACEHOLDER_GPU", _SSH_USER_PLACEHOLDER_GPU)
VAULT_DEFAULT_GPU = _attr("VAULT_DEFAULT_GPU", _VAULT_DEFAULT_GPU)


def _fallback_host_hint(path: str) -> str:
    return "IP after @, before -p. Example 198.13.252.68."


def _fallback_guide(path: str) -> list[dict[str, str]]:
    spec = SINGLE_GPU.get(path) or _FALLBACK_SINGLE_GPU.get(path) or {"label": path}
    return [
        {
            "tab": "Connect",
            "heading": "HTTP services",
            "use": "skip",
            "body": "Port 8888 → Jupyter Notebook. Ignore Initializing. Not a Studio box.",
            "example": "",
            "take": "",
        },
        {
            "tab": "Connect",
            "heading": "SSH",
            "use": "skip",
            "body": "No SCP or SFTP. Do not type ssh.runpod.io as Host. Login on this line is often not root.",
            "example": "ssh LOGIN@ssh.runpod.io -i ~/.ssh/id_ed25519",
            "take": "",
        },
        {
            "tab": "Connect",
            "heading": "SSH over exposed TCP",
            "use": "use",
            "body": "Supports SCP and SFTP. Paste this full line into Runpod quick connect; Studio separates it automatically.",
            "example": "ssh root@198.13.252.68 -p 28231 -i ~/.ssh/id_ed25519",
            "take": "Host = 198.13.252.68   SSH user = root   SSH port = 28231",
        },
        {
            "tab": "Connect",
            "heading": "Web terminal",
            "use": "skip",
            "body": "Leave off. Studio uses SSH, not the browser terminal.",
            "example": "",
            "take": "",
        },
        {
            "tab": "Connect",
            "heading": "Direct TCP ports",
            "use": "check",
            "body": "Same Host and SSH port as the TCP line. Confirms the mapping.",
            "example": "SSH → 198.13.252.68:28231 → :22",
            "take": "",
        },
        {
            "tab": "Details",
            "heading": "Pod volume Mount path",
            "use": "use",
            "body": (
                f"This is Vault. Volume disk on {spec.get('label', path)}: Stop keeps it, "
                "Terminate deletes it. Container disk /models is weights — not the vault."
            ),
            "example": VAULT_DEFAULT_GPU,
            "take": f"Vault = {VAULT_DEFAULT_GPU}",
        },
    ]


def _fallback_connect_teach(path: str) -> list[str]:
    spec = SINGLE_GPU.get(path) or _FALLBACK_SINGLE_GPU.get(path) or {"label": path, "gpu_name": path, "vram": "", "start_here": "", "other": "b300"}
    other = SINGLE_GPU.get(spec.get("other", ""), _FALLBACK_SINGLE_GPU.get(spec.get("other", ""), {}))
    other_label = other.get("label", "the other Blackwell site")
    return [
        f"Where? is {spec.get('label', path)}. Details → GPU must be {spec.get('gpu_name', path)} ({spec.get('vram', '')}). {other_label} for this pod is a fail.",
        "Provider is Runpod if you used https://www.runpod.io/. Your name on Site is required.",
        "Open the Connect tab. Copy SSH over exposed TCP (SCP/SFTP; App Continue rsyncs): ssh root@198.13.252.68 -p 28231 -i ~/.ssh/id_ed25519. Paste the full line into Runpod quick connect; Studio fills Host, SSH user (the word before @, not the account email), SSH port, and identity. Manual fields remain below.",
        "Skip HTTP services (Jupyter 8888). Skip SSH to ssh.runpod.io (proxy, no SCP). Skip Web terminal.",
        f"Vault is Details → Pod volume Mount path, usually {VAULT_DEFAULT_GPU}. Stop keeps Volume disk; Terminate deletes it. Container disk /models is weights. Diony mounts /diony/vault.",
    ]


def _fallback_connect_fail(path: str) -> list[str]:
    return [
        "Picking the other Blackwell site for this GPU",
        "Four hostnames. This lease is one GPU.",
        "Pasting the whole ssh line into a manual Host, SSH user, or SSH port field instead of Runpod quick connect",
        "Using ssh.runpod.io as Host (proxy, no SCP — App Continue rsyncs)",
        "Your email as the SSH user",
        "Jupyter 8888 as the SSH port",
        "A public cloud bucket used as the vault",
    ]


def _fallback_connect_commands(path: str) -> list[str]:
    spec = SINGLE_GPU.get(path) or _FALLBACK_SINGLE_GPU.get(path) or {"host": f"{path}-01", "label": path}
    return [
        f"# Site: {spec.get('label', path)} — Details GPU must match",
        "# RunPod Connect tab. Use SSH over exposed TCP. Skip Jupyter 8888, ssh.runpod.io, Web terminal.",
        "#   ssh root@198.13.252.68 -p 28231 -i ~/.ssh/id_ed25519",
        "# Host: 198.13.252.68   SSH user: root   SSH port: 28231",
        f"# Vault: Details → Pod volume Mount path → {VAULT_DEFAULT_GPU}",
        "# Blueprint 14.",
    ]


def _fallback_app_teach(path: str) -> list[str]:
    spec = SINGLE_GPU.get(path) or _FALLBACK_SINGLE_GPU.get(path) or {}
    if path == "b200":
        brain = (
            f"Brain is {spec.get('recipe', 'b200-v1 TP=1')}, max_model_len "
            f"{spec.get('max_model_len', '16384')}. 180 GB. Do not paste B300 1048576 onto B200."
        )
    else:
        brain = (
            f"Brain is {spec.get('recipe', 'b300-v1 TP=1')}, max_model_len "
            f"{spec.get('max_model_len', '1048576')}. Do not paste Spark TP=4 or the B200 16k pin."
        )
    return [
        "Nothing to pick. The pack is pinned: DeepSeek-V4-Flash-0731 + Wan2.2 A14B (720p) plus diony_sr_v1.",
        "Continue starts the pack pull and opens Ready. Never Dashscope. How is the apply log.",
        "HF_TOKEN: open https://huggingface.co/settings/tokens, Create new token +, paste it in HF_TOKEN on this page.",
        brain,
        f"Producer opens at {PRODUCER_URL}. Do not quote the Quad-Spark 2–5 day SLA.",
        "Next is Ready. Test GPU, then Sign Producer.",
    ]


def _fallback_app_fail(path: str) -> list[str]:
    fails = [
        "Picking a different DeepSeek or Wan because a blog said so",
        "Leaving DASH_API_KEY set",
        "Cloud studio / Dashscope as a substitute",
        "Co-resident DeepSeek and Wan A14B",
    ]
    if path == "b200":
        fails.insert(2, "Pasting the B300 1M context command onto this 180 GB card")
    else:
        fails.insert(2, "Pasting Spark TP=4 or the B200 16k pin onto this B300")
    return fails


def _fallback_hosts_commands(host: str) -> list[str]:
    name = (host or "spark-01").strip() or "spark-01"
    return [
        f"# Product URL is {PRODUCER_URL}. If DNS is missing on this laptop:",
        f"# echo \"$(getent hosts {name} | awk '{{print $1}}') producer.diony.local studio.diony.local\" | sudo tee -a /etc/hosts",
    ]


def _fallback_pack_pull(path: str | None) -> list[str]:
    spec = SINGLE_GPU.get(path or "", {}) or _FALLBACK_SINGLE_GPU.get(path or "", {})
    length = spec.get("max_model_len", "16384" if path == "b200" else "1048576")
    util = spec.get("gpu_util", "0.95" if path == "b200" else "0.90")
    return [
        "huggingface-cli download deepseek-ai/DeepSeek-V4-Flash-0731 --local-dir /models/deepseek/DeepSeek-V4-Flash-0731",
        "huggingface-cli download Wan-AI/Wan2.2-T2V-A14B --local-dir /models/wan/Wan2.2-T2V-A14B",
        f"# Brain is auto: {spec.get('recipe', path)}",
        "vllm serve /models/deepseek/DeepSeek-V4-Flash-0731 "
        "--trust-remote-code --tensor-parallel-size 1 "
        f"--max-model-len {length} --gpu-memory-utilization {util}",
    ]


def _fallback_producer_start(*, cluster: bool = False) -> list[str]:
    cmds = ["pip install -e packages/contracts -e apps/wan -e apps/finish -e apps/producer"]
    if cluster:
        cmds.extend(
            [
                "unset DASH_API_KEY DASHSCOPE_API_KEY",
                "export DIONY_VAULT=/diony/vault",
                "export DIONY_BIND=0.0.0.0",
            ]
        )
    cmds.append("diony-producer")
    cmds.append(
        f"# Opens {PRODUCER_URL}. Produce farms Wan2.2 720p negatives. Picture lock writes the 1080 master."
    )
    return cmds


def prove_lesson(path: str) -> Lesson:
    spec = SINGLE_GPU.get(path) or _FALLBACK_SINGLE_GPU.get(path) or {}
    if path == "dual":
        gpu = "DGX Spark GB10 pair"
        vram = "256 GB unified"
    else:
        gpu = spec.get("gpu_name") or path
        vram = spec.get("vram") or ""
    expect = f"{gpu} ({vram})".strip()
    return Lesson(
        "prove",
        "Test GPU. Pack pull shows in progress on this page. If SSH is refused, resume the pod and update Connect. If Pack is red after the pull, Pull pack, then Test GPU again.",
        [
            "Test GPU uses the Connect SSH line and reads nvidia-smi. Dry-run records the same rows without opening a socket.",
            f"Expect {expect}. Wrong GPU is a fail.",
            "HF_TOKEN: open https://huggingface.co/settings/tokens, Create new token +, paste it in HF_TOKEN on this page, then Pull pack.",
            "Connection refused means the pod is Stopped or RunPod remapped the TCP IP/-p. Open Connect (02). Copy the new SSH over exposed TCP line. Then Pull pack.",
            "If Pack is red, Pull pack on this line (Hugging Face onto /models), then Test GPU. Continue when every row is a pass. While the pack is pulling, Ready shows in progress — leave Studio open.",
        ],
        [
            "Wrong GPU name",
            "SSH refused / stale Connect IP or port",
            "DASH_API_KEY set on this GPU",
            "Skipping HF_TOKEN",
            "Skipping Pull pack when /models is empty",
            "Continuing with a red row",
        ],
        [
            Field(
                "gpu_ok",
                "GPU check",
                "select",
                options=["checked"],
                accept=["checked"],
                default="checked",
                auto=True,
            ),
            hf_token_field(),
        ],
        commands=["# Test GPU runs nvidia-smi over SSH over exposed TCP. Pytest never opens a socket."],
        handoff=["Every row is a pass.", "Next: Sign Producer."],
    )


def _gpu_lessons(path: str) -> dict[str, Lesson]:
    spec = SINGLE_GPU.get(path) or _FALLBACK_SINGLE_GPU[path]
    host = spec["host"]
    pack = PACK_LABEL.get(path, _FALLBACK_PACK_LABEL[path])
    return {
        "connect": Lesson(
            "connect",
            "Use SSH over exposed TCP. Vault is on Details.",
            _call("single_gpu_connect_teach", _fallback_connect_teach, path),
            _call("single_gpu_connect_fail", _fallback_connect_fail, path),
            [
                Field(
                    "hosts",
                    "Host",
                    "text",
                    placeholder="198.13.252.68",
                    default=host,
                    hint=_call("single_gpu_host_hint", _fallback_host_hint, path),
                    prefill=False,
                ),
                Field(
                    "ssh_user",
                    "SSH user (login name)",
                    "text",
                    placeholder=SSH_USER_PLACEHOLDER_GPU,
                    default="root",
                    hint=SSH_USER_HINT_GPU,
                ),
                Field(
                    "ssh_port",
                    "SSH port",
                    "text",
                    placeholder="28231",
                    default=SSH_PORT_DEFAULT,
                    hint=SSH_PORT_HINT_GPU,
                    prefill=False,
                ),
                Field(
                    "nas_ip",
                    "Vault",
                    "text",
                    placeholder=VAULT_DEFAULT_GPU,
                    default=VAULT_DEFAULT_GPU,
                    hint="RunPod Details → Mount path. Usually /workspace.",
                ),
            ],
            commands=_call("single_gpu_connect_commands", _fallback_connect_commands, path),
            diagram=RUNPOD_CONNECT_DIAGRAM,
            handoff=[
                "Host, login, SSH port, and vault are recorded. Vault is this share, mounted at /diony/vault.",
                "Next: App. Continue starts the pack pull over SSH over exposed TCP and opens Ready.",
            ],
            guide=_call("runpod_connect_guide", _fallback_guide, path),
        ),
        "app": Lesson(
            "app",
            "Pack is pinned. Continue starts the pull and opens Ready.",
            _call("single_gpu_app_teach", _fallback_app_teach, path),
            _call("single_gpu_app_fail", _fallback_app_fail, path),
            [
                hf_token_field(),
                Field(
                    "pack",
                    "Pack",
                    "select",
                    options=[pack],
                    accept=[pack],
                    default=pack,
                    auto=True,
                    hint="Pinned. Continue starts the pull.",
                ),
            ],
            commands=[
                "# 1. Pack — this GPU, into /models. Hugging Face token is HF_TOKEN.",
                *_call("pack_pull_commands", _fallback_pack_pull, path),
                "# 2. Producer — same environment. App starts this. Not vLLM.",
                *_call("producer_start_commands", _fallback_producer_start, cluster=True),
            ],
            handoff=[
                "Pack and Brain recipe recorded. Producer is running.",
                "Next: Ready. Test GPU, then Sign Producer.",
            ],
        ),
        "prove": prove_lesson(path),
        "producer": Lesson(
            "producer",
            "Sign writes the site lock. Then open Producer.",
            [
                "App already started Producer on this GPU. Sign writes /diony/vault/.cluster/site.json so this Producer knows the site.",
                "Idea writes the brief. Produce expands on DeepSeek if brain is up, drains brain, then farms Wan2.2 720p clips.",
                "Production is the run: farm, 1080 finish, mix. Not a shot list.",
                "Gallery is the inventory of produced clips. Production writes the 1080 finish.",
                "Human gates stay: bible and distribution. Production runs diony_sr_v1 to a 1920×1080 Rec.709 master, then mix.",
                f"Wan is 720p negative. The deliverable is the 1080 program master from Production. Open {PRODUCER_URL}.",
            ],
            [
                "Shipping a 720p preview as the master",
                "Notes only in chat, not on Shot/Take IDs",
                "Cloud prompt extend",
                "Skipping Production and treating Gallery as the run",
            ],
            [
                Field(
                    "floors",
                    "Floors",
                    "select",
                    options=[SHOW_FLOORS],
                    accept=[SHOW_FLOORS],
                    default=SHOW_FLOORS,
                    auto=True,
                ),
                Field(
                    "stitch",
                    "Overlap stitch",
                    "select",
                    options=["yes"],
                    accept=["yes"],
                    default="yes",
                    auto=True,
                ),
            ],
            commands=[
                *_call("producer_start_commands", _fallback_producer_start, cluster=True),
                *_call("hosts_commands", _fallback_hosts_commands, host),
            ],
            handoff=[
                "Sign this line. site.json lands on /diony/vault.",
                f"Open Producer at {PRODUCER_URL}. Write the idea. Produce. Screen the Program.",
            ],
        ),
    }


def gpu_lessons(path: str) -> dict[str, Lesson]:
    try:
        return _gpu_lessons(path)
    except Exception:
        spec = _FALLBACK_SINGLE_GPU.get(path, {"host": f"{path}-01"})
        host = spec.get("host", f"{path}-01")
        return {
            "connect": Lesson(
                "connect",
                "Open the pod → Connect. Use SSH over exposed TCP. Skip the other Connect blocks. Vault is on Details.",
                _fallback_connect_teach(path),
                _fallback_connect_fail(path),
                [
                    Field(
                        "hosts",
                        "Host",
                        "text",
                        placeholder="198.13.252.68",
                        default=host,
                        prefill=False,
                    ),
                    Field("ssh_user", "SSH user (login name)", "text", default="root"),
                    Field(
                        "ssh_port",
                        "SSH port",
                        "text",
                        placeholder="28231",
                        default=SSH_PORT_DEFAULT,
                        prefill=False,
                    ),
                    Field("nas_ip", "Vault", "text", default=VAULT_DEFAULT_GPU),
                ],
                commands=_fallback_connect_commands(path),
                diagram=RUNPOD_CONNECT_DIAGRAM,
                guide=_fallback_guide(path),
            ),
            "app": Lesson(
                "app",
                "Continue starts the pack pull and opens Ready.",
                _fallback_app_teach(path),
                _fallback_app_fail(path),
                [
                    hf_token_field(),
                    Field(
                        "pack",
                        "Pack",
                        "select",
                        options=[PACK_LABEL.get(path, path)],
                        accept=[PACK_LABEL.get(path, path)],
                        default=PACK_LABEL.get(path, path),
                        auto=True,
                    ),
                ],
            ),
            "prove": prove_lesson(path),
            "producer": Lesson(
                "producer",
                "Sign writes the site lock. Then open Producer.",
                [f"Open {PRODUCER_URL}. Wan is 720p. Picture lock is 1080."],
                ["Shipping a 720p preview as the master"],
                [],
            ),
        }
