"""Provider-safe lease acquisition for Studio COMPUTE.

Runpod exposes a supported API, so B300/B200 can be quoted, launched, and
connected without copying fields between pages. Enverge currently exposes an
account/allocation surface but no public provisioning API; Studio therefore
pauses at those two human checkpoints and takes over as soon as SSH arrives.

Provider and Hugging Face tokens live only in this process. Public state,
events, logs, workbooks, and signed reports never include them.
"""

from __future__ import annotations

import copy
import json
import os
import re
import threading
from datetime import datetime, timezone
from typing import Any

import httpx

from diony_wizard.ssh_command import SSHCommandError, normalize_identity_file


RUNPOD_SIGNUP_URL = "https://console.runpod.io/signup"
RUNPOD_KEYS_URL = "https://docs.runpod.io/get-started/api-keys"
ENVERGE_SPARK_URL = "https://spark.enverge.ai/"

_RUNPOD_PLANS: dict[str, dict[str, Any]] = {
    "b300": {
        "provider": "Runpod",
        "profile": "b300-v1",
        "label": "Leased B300",
        "gpu_type_id": "NVIDIA B300 SXM6 AC",
        "gpu_count": 1,
        "container_disk_gb": 400,
        "volume_gb": 1000,
        "volume_mount_path": "/workspace",
        "ports": ["22/tcp"],
        "interruptible": False,
    },
    "b200": {
        "provider": "Runpod",
        "profile": "b200-v1",
        "label": "Leased B200",
        "gpu_type_id": "NVIDIA B200",
        "gpu_count": 1,
        "container_disk_gb": 400,
        "volume_gb": 1000,
        "volume_mount_path": "/workspace",
        "ports": ["22/tcp"],
        "interruptible": False,
    },
}

_PROFILE_BY_PATH = {
    "local": "spark-4x-v1",
    "dual": "spark-2x-v1",
    "b300": "b300-v1",
    "b200": "b200-v1",
}

EXISTING_DISCOVERY_PROBE = r"""
python3 - <<'PY'
import json, os, shutil, subprocess
from pathlib import Path

def command(args):
    try:
        return subprocess.check_output(args, text=True, timeout=20, stderr=subprocess.STDOUT).strip()
    except Exception as exc:
        return f"ERR {exc}"

def populated(raw):
    path = Path(raw)
    try:
        return path.is_dir() and next(path.iterdir(), None) is not None
    except OSError:
        return False

def private_vault():
    vault = Path("/diony/vault")
    workspace = Path("/workspace")
    if vault.is_dir():
        mounted = command(["findmnt", "-M", "/diony/vault", "-n", "-o", "SOURCE,FSTYPE"])
        if mounted and not mounted.startswith("ERR "):
            parts = mounted.split()
            source = parts[0] if parts else ""
            fs_type = parts[1].lower() if len(parts) > 1 else ""
            if fs_type.startswith("nfs") and ":/" in source:
                host, export = source.split(":", 1)
                return f"nfs://{host}/{export.lstrip('/')}"
            if source.startswith("/") and source != "/diony/vault":
                return f"bind:{source}"
        return "/diony/vault"
    if workspace.is_dir():
        return "/workspace"
    return ""

deepseek = populated("/models/deepseek/DeepSeek-V4-Flash-0731")
wan = populated("/models/wan/Wan2.2-T2V-A14B")
sr_path = Path("/models/diony/diony_sr_v1")
sr = (sr_path / "RealESRGAN_x2.pth").is_file() or (
    sr_path.is_dir() and bool(list(sr_path.glob("*.pth")))
)
wan_repo = Path("/opt/wan22/.git").is_dir()
producer_installed = bool(shutil.which("diony-producer"))
producer_running = subprocess.run(
    ["pgrep", "-f", "diony-producer"],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    check=False,
).returncode == 0
fabric = command(["bash", "-lc", "rdma link 2>/dev/null || ibstat 2>/dev/null || true"])
vault_source = private_vault()
print(json.dumps({
    "hostname": os.uname().nodename if hasattr(os, "uname") else "",
    "gpu": command(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"]),
    "fabric": fabric,
    "vault": Path("/diony/vault").is_dir(),
    "workspace": Path("/workspace").is_dir(),
    "vault_source": vault_source,
    "dashscope": bool(os.environ.get("DASH_API_KEY") or os.environ.get("DASHSCOPE_API_KEY")),
    "deepseek": deepseek,
    "wan": wan,
    "sr": sr,
    "wan_repo": wan_repo,
    "prepared": deepseek and wan and sr and wan_repo,
    "producer_installed": producer_installed,
    "producer_running": producer_running,
}, separators=(",", ":")))
PY
"""


def runpod_plan(path: str) -> dict[str, Any]:
    try:
        return copy.deepcopy(_RUNPOD_PLANS[path])
    except KeyError as exc:
        raise ValueError("Runpod provisioning is available for B300 or B200.") from exc


class ProvisioningError(RuntimeError):
    pass


def classify_existing_compute(scope: str, probes: list[dict[str, Any]]) -> tuple[str, str]:
    """Identify only the four signed Studio profiles from read-only GPU evidence."""
    if scope not in {"local", "lease"}:
        raise ProvisioningError("Existing compute must be Local or Lease.")
    if scope == "local" and len(probes) != 4:
        raise ProvisioningError("Local existing compute needs exactly four DGX Spark hosts.")
    if scope == "lease" and len(probes) not in {1, 2}:
        raise ProvisioningError("Lease existing compute needs one GPU host or two DGX Spark hosts.")
    gpu_rows = [str(row.get("gpu") or "").upper() for row in probes]
    spark = lambda text: any(token in text for token in ("GB10", "DGX SPARK", "GRACE BLACKWELL"))
    if len(probes) in {2, 4}:
        if not all(spark(text) for text in gpu_rows):
            raise ProvisioningError("Every host in this Spark cluster must report NVIDIA GB10/DGX Spark.")
        if not all(str(row.get("fabric") or "").strip() for row in probes):
            raise ProvisioningError("The Spark hosts have no visible CX-7/RDMA fabric. Two isolated hosts fail.")
        path = "local" if len(probes) == 4 else "dual"
        if scope == "local" and path != "local":
            raise ProvisioningError("Local existing compute needs four fabric-connected DGX Sparks.")
        return path, _PROFILE_BY_PATH[path]
    gpu = gpu_rows[0]
    if "B300" in gpu or "GB300" in gpu:
        return "b300", _PROFILE_BY_PATH["b300"]
    if "B200" in gpu:
        return "b200", _PROFILE_BY_PATH["b200"]
    raise ProvisioningError("This GPU is not a supported Diony existing-compute profile.")


class ExistingComputeInspector:
    """Read-only SSH discovery. It never installs, pulls, or logs credentials."""

    def inspect(self, scope: str, connection: dict[str, str]) -> dict[str, Any]:
        from diony_wizard.prep import bind_ssh_env
        from diony_wizard.remote import exec_host

        hosts = [
            part.strip()
            for part in re.split(r"[\s,;]+", str(connection.get("hosts") or ""))
            if part.strip()
        ]
        bind_ssh_env(connection)
        nodes: list[dict[str, Any]] = []
        for host in hosts:
            result = exec_host(host, EXISTING_DISCOVERY_PROBE, timeout=50)
            if not result.ok:
                raise ProvisioningError(f"SSH discovery failed on {host}: {(result.text or 'no response')[:180]}")
            try:
                payload = json.loads((result.stdout or "").strip().splitlines()[-1])
            except (json.JSONDecodeError, IndexError) as exc:
                raise ProvisioningError(f"{host} returned no readable Diony discovery result.") from exc
            payload["host"] = host
            nodes.append(payload)
        path, profile = classify_existing_compute(scope, nodes)
        checks: list[dict[str, Any]] = [
            {"id": "ssh", "label": "SSH", "ok": True, "detail": f"{len(nodes)} host(s) inspected"},
            {"id": "gpu", "label": "Compute profile", "ok": True, "detail": profile},
        ]
        vault_sources = [str(row.get("vault_source") or "").strip() for row in nodes]
        vault_ok = all(bool(source) for source in vault_sources)
        vault_source = vault_sources[0] if vault_ok else ""
        vault_consistent = vault_ok and all(source == vault_source for source in vault_sources)
        dash_ok = all(not bool(row.get("dashscope")) for row in nodes)
        fabric_ok = path in {"b200", "b300"} or all(bool(str(row.get("fabric") or "").strip()) for row in nodes)
        prepared = all(bool(row.get("prepared")) for row in nodes)
        producer_running = bool(nodes and nodes[0].get("producer_running"))
        checks.extend(
            [
                {"id": "fabric", "label": "Fabric", "ok": fabric_ok, "detail": "CX-7/RDMA visible" if fabric_ok else "No cluster fabric detected"},
                {
                    "id": "vault",
                    "label": "Private vault",
                    "ok": vault_consistent,
                    "detail": (
                        f"Auto-detected {vault_source}"
                        if vault_consistent
                        else "Private storage is missing or differs across hosts"
                    ),
                },
                {"id": "dashscope", "label": "No Dashscope", "ok": dash_ok, "detail": "No cloud prompt keys" if dash_ok else "Remove DASH_API_KEY/DASHSCOPE_API_KEY"},
                {"id": "pack", "label": "Diony pack", "ok": prepared, "detail": "DeepSeek + Wan + diony_sr_v1 ready" if prepared else "Model pack or Wan runtime is missing"},
                {"id": "producer", "label": "Producer", "ok": producer_running, "detail": "Producer is running" if producer_running else "Producer will be started"},
            ]
        )
        critical = [row for row in checks if row["id"] in {"fabric", "vault", "dashscope"} and not row["ok"]]
        if critical:
            raise ProvisioningError(str(critical[0]["detail"]))
        return {
            "path": path,
            "profile": profile,
            "prepared": prepared,
            "producer_running": producer_running,
            "vault_source": vault_source,
            "hosts": hosts,
            "checks": checks,
            "nodes": nodes,
        }


class RunpodClient:
    """Small official-API adapter. It deliberately has no application logging."""

    rest_url = "https://rest.runpod.io/v1"
    graphql_url = "https://api.runpod.io/graphql"

    def __init__(self, timeout: float = 25.0) -> None:
        self.timeout = timeout

    @staticmethod
    def _message(response: httpx.Response) -> str:
        try:
            body = response.json()
        except Exception:
            body = {}
        if isinstance(body, dict):
            value = body.get("error") or body.get("message") or body.get("detail")
            if value:
                return str(value)[:240]
        return f"Provider returned HTTP {response.status_code}."

    def quote(self, path: str, token: str) -> dict[str, Any]:
        plan = runpod_plan(path)
        query = """
        query DionyGpuQuote($id: String!) {
          gpuTypes(input: {id: $id}) {
            id displayName memoryInGb
            lowestPrice(input: {gpuCount: 1}) {
              stockStatus uninterruptablePrice availableGpuCounts
            }
          }
        }
        """
        try:
            response = httpx.post(
                self.graphql_url,
                params={"api_key": token},
                json={"query": query, "variables": {"id": plan["gpu_type_id"]}},
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise ProvisioningError("Runpod could not be reached. Check the network and retry.") from exc
        if response.status_code >= 400:
            raise ProvisioningError(self._message(response))
        payload = response.json()
        if payload.get("errors"):
            message = str(payload["errors"][0].get("message") or "Runpod rejected the quote.")
            raise ProvisioningError(message[:240])
        rows = ((payload.get("data") or {}).get("gpuTypes") or [])
        if not rows:
            raise ProvisioningError(f"{plan['label']} is not in the current Runpod catalog.")
        price = rows[0].get("lowestPrice") or {}
        return {
            "gpu_id": rows[0].get("id") or plan["gpu_type_id"],
            "stock": price.get("stockStatus") or "Unknown",
            "hourly": price.get("uninterruptablePrice"),
            "cloud": os.environ.get("DIONY_RUNPOD_CLOUD", "COMMUNITY").upper(),
        }

    def create(self, path: str, token: str) -> dict[str, Any]:
        plan = runpod_plan(path)
        image = os.environ.get("DIONY_RUNPOD_IMAGE", "runpod/pytorch").strip() or "runpod/pytorch"
        cloud = os.environ.get("DIONY_RUNPOD_CLOUD", "COMMUNITY").upper()
        if cloud not in {"SECURE", "COMMUNITY"}:
            cloud = "COMMUNITY"
        body = {
            "name": f"diony-ai-{path}",
            "cloudType": cloud,
            "computeType": "GPU",
            "gpuTypeIds": [plan["gpu_type_id"]],
            "gpuTypePriority": "custom",
            "gpuCount": plan["gpu_count"],
            "interruptible": plan["interruptible"],
            "imageName": image,
            "containerDiskInGb": plan["container_disk_gb"],
            "volumeInGb": plan["volume_gb"],
            "volumeMountPath": plan["volume_mount_path"],
            "ports": plan["ports"],
            "supportPublicIp": True,
            "allowedCudaVersions": ["13.0", "12.9", "12.8"],
            "env": {
                "DIONY_PROFILE": plan["profile"],
                "DIONY_VAULT": "/diony/vault",
            },
        }
        try:
            response = httpx.post(
                f"{self.rest_url}/pods",
                headers={"Authorization": f"Bearer {token}"},
                json=body,
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise ProvisioningError("Runpod launch could not be reached. Retry when the network is back.") from exc
        if response.status_code >= 400:
            raise ProvisioningError(self._message(response))
        return response.json()

    def get(self, pod_id: str, token: str) -> dict[str, Any]:
        try:
            response = httpx.get(
                f"{self.rest_url}/pods/{pod_id}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise ProvisioningError("Runpod status could not be reached. Diony will retry.") from exc
        if response.status_code >= 400:
            raise ProvisioningError(self._message(response))
        return response.json()


class ProvisioningService:
    def __init__(self, runpod: Any | None = None, inspector: Any | None = None) -> None:
        self.runpod = runpod or RunpodClient()
        self.inspector = inspector or ExistingComputeInspector()
        self._lock = threading.RLock()
        self.reset()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def reset(self) -> None:
        with getattr(self, "_lock", threading.RLock()):
            self._state: dict[str, Any] = {
                "status": "idle",
                "mode": None,
                "scope": None,
                "path": None,
                "profile": None,
                "provider": None,
                "checkpoint": None,
                "headline": "",
                "detail": "",
                "action_url": "",
                "action_label": "",
                "required_inputs": [],
                "quote": None,
                "resource_id": None,
                "connection": None,
                "inspection": None,
                "events": [],
                "error": "",
                "handed_off": False,
                "handoff_url": "",
            }
            self._provider_token = ""
            self._hf_token = ""
            self._signer = ""

    def _event(self, label: str, detail: str, status: str = "passed") -> None:
        self._state["events"].append(
            {"label": label, "detail": detail, "status": status, "at": self._now()}
        )

    def public(self) -> dict[str, Any]:
        with self._lock:
            public = copy.deepcopy(self._state)
            connection = public.get("connection")
            if isinstance(connection, dict):
                connection.pop("ssh_identity_file", None)
            return public

    def _sanitize(self, message: str) -> str:
        clean = str(message or "Provider action failed.")
        for secret in (self._provider_token, self._hf_token):
            if secret:
                clean = clean.replace(secret, "[redacted]")
        return clean[:300]

    def start(self, path: str) -> dict[str, Any]:
        if path not in {"dual", "b300", "b200"}:
            raise ProvisioningError("Lease setup is available for Dual-Spark, B300, or B200.")
        self.reset()
        with self._lock:
            provider = "Enverge" if path == "dual" else "Runpod"
            self._state.update(
                {
                    "status": "attention",
                    "mode": "new",
                    "path": path,
                    "provider": provider,
                    "checkpoint": "provider_account",
                    "headline": f"Connect {provider}",
                    "detail": (
                        "Create or sign in to the provider account. Billing, terms, CAPTCHA, and two-factor approval stay with you."
                    ),
                    "action_url": ENVERGE_SPARK_URL if path == "dual" else RUNPOD_SIGNUP_URL,
                    "action_label": f"Open {provider}",
                    "required_inputs": (
                        ["signer", "hf_token"]
                        if path == "dual"
                        else ["signer", "provider_token", "hf_token"]
                    ),
                }
            )
            self._event("Option confirmed", f"{provider} · {path.upper()}")
            self._event("Provider account", "Waiting for your secure sign-in.", "active")
            return self.public()

    def start_existing(self, scope: str) -> dict[str, Any]:
        if scope not in {"local", "lease"}:
            raise ProvisioningError("Existing compute must be Local or Lease.")
        self.reset()
        with self._lock:
            required = ["hosts", "ssh_user", "ssh_port"]
            if scope == "lease":
                required = ["provider", "signer", *required]
            self._state.update(
                {
                    "status": "attention",
                    "mode": "existing",
                    "scope": scope,
                    "provider": "Existing compute",
                    "checkpoint": "existing_connection",
                    "headline": "Connect existing compute",
                    "detail": "Diony will inspect the hosts read-only, identify the profile, validate the vault and fabric, then reuse or repair the installed pack.",
                    "required_inputs": required,
                }
            )
            self._event("Existing compute selected", scope.title())
            self._event("Read-only discovery", "Waiting for the SSH connection. Private storage will be identified automatically.", "active")
            return self.public()

    def advance(self, values: dict[str, Any]) -> dict[str, Any]:
        action = str(values.get("action") or "").strip()
        with self._lock:
            if self._state["status"] == "idle":
                raise ProvisioningError("Choose a lease option first.")
            try:
                if action == "provider_ready" and self._state["path"] in {"b300", "b200"}:
                    return self._runpod_ready(values)
                if action == "deploy" and self._state["checkpoint"] == "deploy_confirmation":
                    return self._runpod_deploy()
                if action == "allocation_requested" and self._state["path"] == "dual":
                    return self._enverge_requested(values)
                if action == "connection_ready" and self._state["path"] == "dual":
                    return self._enverge_connection(values)
                if action == "inspect_existing" and self._state["checkpoint"] == "existing_connection":
                    return self._inspect_existing(values)
                if action == "repair_existing" and self._state["checkpoint"] == "repair_credentials":
                    return self._repair_existing(values)
            except ProvisioningError as exc:
                self._state["status"] = "attention"
                self._state["error"] = self._sanitize(str(exc))
                if self._state["events"] and self._state["events"][-1].get("status") in {
                    "active",
                    "failed",
                }:
                    self._state["events"][-1]["status"] = "failed"
                    self._state["events"][-1]["detail"] = self._state["error"]
                else:
                    self._event("Needs attention", self._state["error"], "failed")
                raise ProvisioningError(self._state["error"]) from exc
            raise ProvisioningError("That action is not available at this checkpoint.")

    @staticmethod
    def _connection_values(values: dict[str, Any]) -> dict[str, str]:
        hosts = "\n".join(
            part.strip()
            for part in re.split(r"[\s,;]+", str(values.get("hosts") or ""))
            if part.strip()
        )
        user = str(values.get("ssh_user") or "").strip()
        port = str(values.get("ssh_port") or "22").strip()
        identity = str(values.get("ssh_identity_file") or "").strip()
        vault = str(values.get("nas_ip") or "").strip()
        if not hosts or not user:
            raise ProvisioningError("Hosts and SSH user are required.")
        if not port.isdigit() or not (1 <= int(port) <= 65535):
            raise ProvisioningError("SSH port must be between 1 and 65535.")
        if vault.lower().startswith(("http://", "https://", "s3://")):
            raise ProvisioningError("Vault must be a private mount, not a public URL or bucket.")
        connection = {"hosts": hosts, "ssh_user": user, "ssh_port": port, "nas_ip": vault}
        if identity:
            try:
                connection["ssh_identity_file"] = normalize_identity_file(identity)
            except SSHCommandError as exc:
                raise ProvisioningError(str(exc)) from exc
        return connection

    def _inspect_existing(self, values: dict[str, Any]) -> dict[str, Any]:
        scope = str(self._state.get("scope") or "")
        provider = str(values.get("provider") or ("Local" if scope == "local" else "")).strip()
        signer = str(values.get("signer") or "").strip()
        if scope == "lease" and not provider:
            raise ProvisioningError("Enter the provider that owns this existing lease.")
        if scope == "lease" and len(signer) < 2:
            raise ProvisioningError("Enter the named human responsible for this lease.")
        connection = self._connection_values(values)
        inspection = self.inspector.inspect(scope, connection)
        vault_source = str(inspection.get("vault_source") or "").strip()
        if not vault_source:
            raise ProvisioningError("Diony could not identify private storage through this SSH connection.")
        connection["nas_ip"] = vault_source
        self._signer = signer
        self._state.update(
            {
                "path": str(inspection["path"]),
                "profile": str(inspection["profile"]),
                "provider": provider,
                "connection": connection,
                "inspection": copy.deepcopy(inspection),
                "error": "",
            }
        )
        self._state["events"][-1]["status"] = "passed"
        self._state["events"][-1]["detail"] = f"Detected {inspection['profile']} across {len(inspection['hosts'])} host(s)."
        for check in inspection.get("checks") or []:
            self._event(str(check.get("label") or "Check"), str(check.get("detail") or ""), "passed" if check.get("ok") else "active")
        if not inspection.get("prepared"):
            self._state.update(
                {
                    "status": "attention",
                    "checkpoint": "repair_credentials",
                    "headline": "Repair the existing site",
                    "detail": "The connection and hardware passed, but the Diony model pack or runtime is missing. Add a Hugging Face token so the agent can install only what is missing.",
                    "required_inputs": ["hf_token"],
                }
            )
            self._event("Repair authorization", "Waiting for a masked Hugging Face token.", "active")
            return self.public()
        return self._ready(connection)

    def _repair_existing(self, values: dict[str, Any]) -> dict[str, Any]:
        token = str(values.get("hf_token") or os.environ.get("HF_TOKEN") or "").strip()
        if not token:
            raise ProvisioningError("Add the Hugging Face token so Diony can repair the missing pack.")
        self._hf_token = token
        if self._state["events"]:
            self._state["events"][-1]["status"] = "passed"
            self._state["events"][-1]["detail"] = "Repair authorized. Secret removed from the UI."
        return self._ready(dict(self._state["connection"] or {}))

    def _collect_identity(self, values: dict[str, Any], provider_token: bool) -> None:
        signer = str(values.get("signer") or "").strip()
        hf_token = str(values.get("hf_token") or "").strip()
        token = str(values.get("provider_token") or "").strip()
        if len(signer) < 2:
            raise ProvisioningError("Enter the named human responsible for this lease.")
        if not hf_token and not os.environ.get("HF_TOKEN", "").strip():
            raise ProvisioningError("Add the Hugging Face token for the pinned Diony model pack.")
        if provider_token and not token:
            raise ProvisioningError("Add a Runpod API key with Pod read/write access.")
        self._signer = signer
        self._hf_token = hf_token or os.environ.get("HF_TOKEN", "").strip()
        if provider_token:
            self._provider_token = token

    def _runpod_ready(self, values: dict[str, Any]) -> dict[str, Any]:
        self._collect_identity(values, provider_token=True)
        quote = self.runpod.quote(self._state["path"], self._provider_token)
        if str(quote.get("stock") or "").lower() == "none":
            raise ProvisioningError("This GPU has no current Runpod capacity. Retry to re-check the live catalog.")
        self._state.update(
            {
                "status": "attention",
                "checkpoint": "deploy_confirmation",
                "headline": "Confirm live deployment",
                "detail": "Runpod will begin billing when Diony launches this Pod.",
                "action_url": RUNPOD_KEYS_URL,
                "action_label": "API key help",
                "required_inputs": [],
                "quote": quote,
                "error": "",
            }
        )
        self._state["events"][-1]["status"] = "passed"
        self._state["events"][-1]["detail"] = "Account connected. Secret removed from the UI."
        self._event("Capacity and price", f"{quote.get('stock', 'Unknown')} stock · live price loaded")
        self._event("Billing confirmation", "Waiting for Continue before launch.", "active")
        return self.public()

    def _runpod_deploy(self) -> dict[str, Any]:
        created = self.runpod.create(self._state["path"], self._provider_token)
        resource_id = str(created.get("id") or "").strip()
        if not resource_id:
            raise ProvisioningError("Runpod created no Pod ID. Nothing was adopted into Studio.")
        self._state.update(
            {
                "status": "working",
                "checkpoint": "provider_boot",
                "headline": "Starting the Diony site",
                "detail": "Runpod is assigning the GPU, persistent volume, public IP, and SSH port.",
                "action_url": "",
                "action_label": "",
                "required_inputs": [],
                "resource_id": resource_id,
                "error": "",
            }
        )
        self._state["events"][-1]["status"] = "passed"
        self._state["events"][-1]["detail"] = "Billing approved. Pod launch sent."
        self._event("GPU + storage", "400 GB container · 1 TB persistent /workspace", "active")
        return self.public()

    def _enverge_requested(self, values: dict[str, Any]) -> dict[str, Any]:
        self._collect_identity(values, provider_token=False)
        self._state.update(
            {
                "status": "attention",
                "checkpoint": "provider_allocation",
                "headline": "Waiting for the dual-Spark allocation",
                "detail": "Choose 2× NVIDIA DGX Spark. Enverge returns two fabric-connected SSH hosts; paste that connection once issued.",
                "action_url": ENVERGE_SPARK_URL,
                "action_label": "Open Enverge",
                "required_inputs": ["hosts", "ssh_user", "ssh_port", "nas_ip"],
                "error": "",
            }
        )
        self._state["events"][-1]["status"] = "passed"
        self._state["events"][-1]["detail"] = "Account confirmed. Allocation request stays in your provider account."
        self._event("2× Spark allocation", "Waiting for two fabric-connected SSH hosts.", "active")
        return self.public()

    def _enverge_connection(self, values: dict[str, Any]) -> dict[str, Any]:
        hosts = str(values.get("hosts") or "").strip()
        host_rows = [part for part in hosts.replace(",", "\n").splitlines() if part.strip()]
        if len(host_rows) != 2:
            raise ProvisioningError("Dual-Spark needs exactly two fabric-connected hosts.")
        ssh_user = str(values.get("ssh_user") or "").strip()
        ssh_port = str(values.get("ssh_port") or "22").strip()
        vault = str(values.get("nas_ip") or "").strip()
        if not ssh_user or not vault:
            raise ProvisioningError("SSH user and the private vault are required.")
        if not ssh_port.isdigit() or not (1 <= int(ssh_port) <= 65535):
            raise ProvisioningError("SSH port must be between 1 and 65535.")
        return self._ready(
            {
                "hosts": "\n".join(part.strip() for part in host_rows),
                "ssh_user": ssh_user,
                "ssh_port": ssh_port,
                "nas_ip": vault,
            }
        )

    def _ready(self, connection: dict[str, str]) -> dict[str, Any]:
        self._state.update(
            {
                "status": "ready",
                "checkpoint": "studio_handoff",
                "headline": "Compute is connected",
                "detail": "Diony has the SSH and vault contract. Studio will check the site and install only what is missing.",
                "required_inputs": [],
                "connection": connection,
                "error": "",
            }
        )
        if self._state["events"]:
            self._state["events"][-1]["status"] = "passed"
        self._event("Studio handoff", "Connection recorded. Starting the pinned Diony pack.", "active")
        return self.public()

    def refresh(self) -> dict[str, Any]:
        with self._lock:
            if self._state["status"] != "working" or self._state["provider"] != "Runpod":
                return self.public()
            try:
                pod = self.runpod.get(self._state["resource_id"], self._provider_token)
            except ProvisioningError as exc:
                self._state["detail"] = self._sanitize(str(exc))
                return self.public()
            public_ip = str(pod.get("publicIp") or "").strip()
            mappings = pod.get("portMappings") or {}
            ssh_port = mappings.get("22") or mappings.get(22)
            if not public_ip or not ssh_port:
                desired = str(pod.get("desiredStatus") or "RUNNING").title()
                self._state["detail"] = f"{desired}. Waiting for the public IP and SSH port."
                return self.public()
            self._state["events"][-1]["status"] = "passed"
            self._state["events"][-1]["detail"] = "GPU and persistent storage assigned."
            return self._ready(
                {
                    "hosts": public_ip,
                    "ssh_user": "root",
                    "ssh_port": str(ssh_port),
                    "nas_ip": str(pod.get("volumeMountPath") or "/workspace"),
                }
            )

    def consume(self) -> dict[str, Any]:
        with self._lock:
            if self._state["status"] != "ready" or not self._state.get("connection"):
                raise ProvisioningError("Compute is not ready to adopt into Studio.")
            return {
                "path": self._state["path"],
                "mode": self._state.get("mode"),
                "scope": self._state.get("scope"),
                "profile": self._state.get("profile"),
                "provider": self._state["provider"],
                "signer": self._signer,
                "hf_token": self._hf_token,
                "connection": copy.deepcopy(self._state["connection"]),
                "inspection": copy.deepcopy(self._state.get("inspection") or {}),
            }

    def mark_existing_validating(self) -> dict[str, Any]:
        with self._lock:
            self._state.update(
                {
                    "status": "working",
                    "checkpoint": "validating_existing",
                    "headline": "Validating existing compute",
                    "detail": "Checking readiness, repairing only what is missing, and starting Producer.",
                    "required_inputs": [],
                }
            )
            self._event("Validation + setup", "Diony is completing the detected site.", "active")
            return self.public()

    def mark_existing_attention(self, detail: str) -> dict[str, Any]:
        with self._lock:
            self._state.update({"status": "attention", "detail": self._sanitize(detail), "error": self._sanitize(detail)})
            if self._state["events"]:
                self._state["events"][-1].update({"status": "failed", "detail": self._sanitize(detail)})
            return self.public()

    def mark_existing_complete(self, handoff_url: str) -> dict[str, Any]:
        with self._lock:
            self._state.update(
                {
                    "status": "complete",
                    "checkpoint": None,
                    "headline": "Existing compute is ready",
                    "detail": "Validation passed. The site is signed and Producer is ready.",
                    "handoff_url": handoff_url,
                    "handed_off": True,
                    "error": "",
                }
            )
            if self._state["events"]:
                self._state["events"][-1].update({"status": "passed", "detail": "Site signed. Continue to Producer."})
            self._hf_token = ""
            return self.public()

    def mark_handed_off(self) -> dict[str, Any]:
        with self._lock:
            self._state["handed_off"] = True
            self._state["status"] = "complete"
            self._state["checkpoint"] = None
            self._state["headline"] = "Studio is preparing the site"
            self._state["detail"] = "The provider connection is recorded. Check Site is running now."
            if self._state["events"]:
                self._state["events"][-1]["status"] = "passed"
            self._provider_token = ""
            self._hf_token = ""
            return self.public()
