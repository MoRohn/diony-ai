"""Validated persistent Wan worker client.

The resident lane changes process and device residency only. It is deliberately
unavailable until an isolated benchmark writes a passing validation receipt for
the exact quality contract below. A feature flag without that receipt is a
no-op, so a package deployment cannot accidentally alter an active production.
"""

from __future__ import annotations

import hashlib
import json
import os
import selectors
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any


QUALITY_CONTRACT = {
    "native_size": "1280*720",
    "frame_num": 81,
    "sample_steps": 24,
    "sample_solver": "unipc",
    "precision": "bfloat16",
    "negative_conditioning": True,
    "prompt_extension": False,
    "finish_required": True,
}
QUALITY_CONTRACT_SHA256 = hashlib.sha256(
    json.dumps(QUALITY_CONTRACT, separators=(",", ":"), sort_keys=True).encode("utf-8")
).hexdigest()
RESIDENT_CANDIDATE = "persistent-bf16-resident-v1"
REGIONAL_COMPILE_CANDIDATE = "persistent-bf16-regional-compile-v1"
EAGER_KERNEL_MODE = "eager"
REGIONAL_COMPILE_KERNEL_MODE = "regional_compile"
KERNEL_CANDIDATES = {
    EAGER_KERNEL_MODE: RESIDENT_CANDIDATE,
    REGIONAL_COMPILE_KERNEL_MODE: REGIONAL_COMPILE_CANDIDATE,
}
RESIDENT_TASKS = frozenset({"t2v-A14B", "i2v-A14B"})
_SUPPRESSION_LOCK = threading.Lock()
_SUPPRESSED_RECEIPTS: dict[str, str] = {}


class ResidentError(RuntimeError):
    pass


@dataclass(frozen=True)
class ResidentDecision:
    enabled: bool
    reason: str
    receipt_sha256: str = ""
    kernel_mode: str = EAGER_KERNEL_MODE


def configured_kernel_mode() -> str:
    mode = os.environ.get("DIONY_WAN_KERNEL_MODE", EAGER_KERNEL_MODE).strip().lower()
    if mode not in KERNEL_CANDIDATES:
        raise ResidentError(
            f"Unsupported DIONY_WAN_KERNEL_MODE={mode!r}; use eager or regional_compile."
        )
    return mode


def _receipt_decision(path: Path, *, kernel_mode: str) -> ResidentDecision:
    try:
        raw = path.read_bytes()
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError, TypeError):
        return ResidentDecision(False, "Resident Wan validation receipt is missing or unreadable.")
    if not isinstance(payload, dict):
        return ResidentDecision(False, "Resident Wan validation receipt must be a JSON object.")
    expected_candidate = KERNEL_CANDIDATES[kernel_mode]
    if payload.get("schema_version") != 1 or payload.get("candidate") != expected_candidate:
        return ResidentDecision(False, "Resident Wan validation receipt names an unsupported candidate.")
    if payload.get("quality_contract_sha256") != QUALITY_CONTRACT_SHA256:
        return ResidentDecision(False, "Resident Wan validation receipt does not match the quality contract.")
    if int(payload.get("sample_count") or 0) < 30:
        return ResidentDecision(False, "Resident Wan validation requires at least 30 clips.")
    baseline_p50 = float(payload.get("baseline_p50_seconds") or 0)
    candidate_p50 = float(payload.get("candidate_p50_seconds") or 0)
    if baseline_p50 <= 0 or candidate_p50 <= 0:
        return ResidentDecision(False, "Resident Wan validation receipt has invalid p50 timing.")
    improvement = (baseline_p50 - candidate_p50) / baseline_p50
    if improvement < 0.15:
        return ResidentDecision(False, "Resident Wan p50 improvement is below the required 15%.")
    baseline_p95 = float(payload.get("baseline_p95_seconds") or 0)
    candidate_p95 = float(payload.get("candidate_p95_seconds") or 0)
    if baseline_p95 <= 0 or candidate_p95 <= 0 or candidate_p95 > baseline_p95:
        return ResidentDecision(False, "Resident Wan p95 latency regressed.")
    if int(payload.get("candidate_ooms") or 0) != 0:
        return ResidentDecision(False, "Resident Wan validation recorded an out-of-memory failure.")
    if int(payload.get("candidate_retries") or 0) > int(payload.get("baseline_retries") or 0):
        return ResidentDecision(False, "Resident Wan validation recorded a retry regression.")
    if payload.get("quality_passed") is not True:
        return ResidentDecision(False, "Resident Wan quality equivalence did not pass.")
    evidence_sha = str(payload.get("quality_evidence_sha256") or "")
    if len(evidence_sha) != 64 or any(ch not in "0123456789abcdef" for ch in evidence_sha.lower()):
        return ResidentDecision(False, "Resident Wan validation has no complete quality evidence hash.")
    try:
        max_canary = int(payload.get("max_canary_percent") or 0)
        configured_canary = int(os.environ.get("DIONY_WAN_RESIDENT_CANARY_PERCENT", "10"))
    except ValueError:
        return ResidentDecision(False, "Resident Wan canary percentage is invalid.")
    if max_canary < 1 or max_canary > 100:
        return ResidentDecision(False, "Resident Wan validation receipt has an invalid canary cap.")
    if configured_canary < 0 or configured_canary > max_canary:
        return ResidentDecision(
            False,
            f"Resident Wan canary {configured_canary}% exceeds the receipt cap {max_canary}%.",
        )
    if not str(payload.get("approved_by") or "").strip():
        return ResidentDecision(False, "Resident Wan validation has no named approver.")
    return ResidentDecision(
        True,
        f"Validated resident BF16 worker approved ({kernel_mode}).",
        hashlib.sha256(raw).hexdigest(),
        kernel_mode,
    )


def resident_decision(*, task: str, offload: bool) -> ResidentDecision:
    mode = os.environ.get("DIONY_WAN_RESIDENT", "off").strip().lower()
    if mode != "validated":
        return ResidentDecision(False, "Resident Wan is held until DIONY_WAN_RESIDENT=validated.")
    if task not in RESIDENT_TASKS:
        return ResidentDecision(False, f"Resident Wan does not support {task}; native CLI fallback required.")
    if offload:
        return ResidentDecision(False, "Resident Wan is disabled on sites that require model offload.")
    receipt = os.environ.get("DIONY_WAN_RESIDENT_RECEIPT", "").strip()
    if not receipt:
        return ResidentDecision(False, "Resident Wan validation receipt is not configured.")
    try:
        kernel_mode = configured_kernel_mode()
    except ResidentError as exc:
        return ResidentDecision(False, str(exc))
    decision = _receipt_decision(Path(receipt).expanduser(), kernel_mode=kernel_mode)
    if decision.enabled:
        with _SUPPRESSION_LOCK:
            reason = _SUPPRESSED_RECEIPTS.get(decision.receipt_sha256, "")
            if not reason:
                try:
                    rollback = json.loads(resident_suppression_path().read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError, TypeError):
                    rollback = {}
                if rollback.get("receipt_sha256") == decision.receipt_sha256:
                    reason = str(rollback.get("reason") or "runtime failure")
        if reason:
            return ResidentDecision(
                False,
                f"Resident Wan automatic rollback is open for this receipt: {reason}",
                decision.receipt_sha256,
                decision.kernel_mode,
            )
    return decision


def resident_canary_selected(identifier: str) -> bool:
    try:
        percent = int(os.environ.get("DIONY_WAN_RESIDENT_CANARY_PERCENT", "10"))
    except ValueError:
        percent = 10
    percent = max(0, min(100, percent))
    if percent == 0:
        return False
    if percent == 100:
        return True
    bucket = int(hashlib.sha256(identifier.encode("utf-8")).hexdigest()[:8], 16) % 100
    return bucket < percent


def suppress_resident(receipt_sha256: str, reason: str) -> None:
    if not receipt_sha256:
        return
    clean_reason = reason.strip()[:300] or "runtime failure"
    with _SUPPRESSION_LOCK:
        _SUPPRESSED_RECEIPTS[receipt_sha256] = clean_reason
        path = resident_suppression_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            pending = path.with_suffix(path.suffix + ".part")
            pending.write_text(
                json.dumps(
                    {"schema_version": 1, "receipt_sha256": receipt_sha256, "reason": clean_reason},
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            pending.replace(path)
        except OSError:
            pass


def resident_suppression_path() -> Path:
    vault = Path(
        os.environ.get("DIONY_PRODUCER_VAULT")
        or os.environ.get("DIONY_VAULT")
        or ".diony-vault"
    ).expanduser()
    return vault / ".cluster" / "wan-resident-rollback.json"


def clear_resident_suppression() -> None:
    with _SUPPRESSION_LOCK:
        _SUPPRESSED_RECEIPTS.clear()
        try:
            resident_suppression_path().unlink(missing_ok=True)
        except OSError:
            pass


def resident_job_payload(
    *,
    task: str,
    checkpoint: Path,
    prompt: str,
    negative_prompt: str | None,
    seed: int,
    save_file: Path,
    frame_num: int,
    image: Path | None,
    log_path: Path,
) -> dict[str, Any]:
    """Build the worker request without exposing an alternate quality knob."""
    kernel_mode = configured_kernel_mode()
    return {
        "protocol": 1,
        "task": task,
        "checkpoint": str(checkpoint),
        "size": QUALITY_CONTRACT["native_size"],
        "frame_num": int(frame_num),
        "sample_steps": QUALITY_CONTRACT["sample_steps"],
        "sample_solver": QUALITY_CONTRACT["sample_solver"],
        "prompt": prompt,
        "negative_prompt": (negative_prompt or "").strip(),
        "seed": int(seed),
        "save_file": str(save_file),
        "image": str(image) if image is not None else "",
        "log_path": str(log_path),
        "offload_model": False,
        "init_on_cpu": False,
        "quality_contract_sha256": QUALITY_CONTRACT_SHA256,
        "kernel_mode": kernel_mode,
    }


class ResidentWanProcess:
    """One JSON-lines worker shared by sequential FARM calls in this process."""

    def __init__(self) -> None:
        self._process: subprocess.Popen[str] | None = None
        self._python = ""
        self._lock = threading.Lock()

    @property
    def process(self) -> subprocess.Popen[str] | None:
        return self._process

    def _start(self, *, python: str, cwd: str, env: dict[str, str]) -> subprocess.Popen[str]:
        process = self._process
        if process is not None and process.poll() is None and self._python == python:
            return process
        self._stop_locked()
        worker = Path(__file__).with_name("resident_worker.py")
        process = subprocess.Popen(
            [python, str(worker)],
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        self._process = process
        self._python = python
        return process

    @staticmethod
    def _response(process: subprocess.Popen[str], timeout: int) -> dict[str, Any]:
        if process.stdout is None:
            raise ResidentError("Resident Wan worker has no response stream.")
        selector = selectors.DefaultSelector()
        try:
            selector.register(process.stdout, selectors.EVENT_READ)
            if not selector.select(timeout=max(1, timeout)):
                raise ResidentError("Resident Wan timed out before the 720p negative landed.")
            line = process.stdout.readline()
        finally:
            selector.close()
        if not line:
            raise ResidentError(f"Resident Wan worker exited unexpectedly ({process.poll()}).")
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ResidentError("Resident Wan returned an invalid response.") from exc
        if not isinstance(payload, dict):
            raise ResidentError("Resident Wan returned an invalid response object.")
        return payload

    def run(
        self,
        payload: dict[str, Any],
        *,
        python: str,
        cwd: str,
        env: dict[str, str],
        timeout: int,
    ) -> dict[str, Any]:
        with self._lock:
            process = self._start(python=python, cwd=cwd, env=env)
            if process.stdin is None:
                self._stop_locked()
                raise ResidentError("Resident Wan worker has no request stream.")
            try:
                process.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
                process.stdin.flush()
                response = self._response(process, timeout)
            except (BrokenPipeError, OSError, ResidentError):
                self._stop_locked()
                raise
            if response.get("ok") is not True:
                error = str(response.get("error") or "Resident Wan generation failed.")
                if response.get("fatal"):
                    self._stop_locked()
                raise ResidentError(error)
            return response

    def _stop_locked(self) -> None:
        process = self._process
        self._process = None
        self._python = ""
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()


_RESIDENT = ResidentWanProcess()


def resident_process() -> subprocess.Popen[str] | None:
    return _RESIDENT.process


def run_resident(
    payload: dict[str, Any],
    *,
    python: str,
    cwd: str,
    env: dict[str, str],
    timeout: int,
) -> dict[str, Any]:
    return _RESIDENT.run(payload, python=python, cwd=cwd, env=env, timeout=timeout)


def stop_resident() -> None:
    _RESIDENT.stop()
