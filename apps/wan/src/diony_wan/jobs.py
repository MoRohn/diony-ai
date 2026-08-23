"""Durable, collision-safe Diony-owned Wan job queue.

ComfyUI remains the model executor. Diony owns admission, destination locks,
status, restart recovery, and service-level metrics around it.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from diony_wan.comfy import ComfyError, GenerationConfig

JobExecutor = Callable[[dict[str, Any]], Path]
ResumeExecutor = Callable[[dict[str, Any]], Path | None]

_TERMINAL = {"completed", "failed", "cancelled"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ComfyError(f"job record is unreadable: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ComfyError(f"job record is not an object: {path}")
    return payload


class WanJobManager:
    def __init__(
        self,
        *,
        root: Path,
        execute: JobExecutor,
        resume: ResumeExecutor | None = None,
        max_workers: int = 1,
    ) -> None:
        self.root = root
        self.execute = execute
        self.resume = resume
        self.root.mkdir(parents=True, exist_ok=True)
        self._locks = self.root / ".destination-locks"
        self._locks.mkdir(parents=True, exist_ok=True)
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, max_workers), thread_name_prefix="diony-wan-job"
        )
        self._guard = threading.RLock()
        self._futures: dict[str, Future[None]] = {}
        self.last_recovery: dict[str, Any] | None = None

    def _path(self, job_id: str) -> Path:
        if not job_id or "/" in job_id or "\\" in job_id:
            raise ComfyError("invalid Wan job id")
        return self.root / f"{job_id}.json"

    def _destination_lock_path(self, destination: Path) -> Path:
        digest = hashlib.sha256(str(destination.resolve()).encode("utf-8")).hexdigest()
        return self._locks / f"{digest}.lock"

    def _claim_destination(self, job_id: str, destination: Path) -> Path:
        path = self._destination_lock_path(destination)
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            try:
                owner = path.read_text(encoding="utf-8").strip()
            except OSError:
                owner = "another job"
            raise ComfyError(f"destination is already owned by active Wan job {owner or 'unknown'}")
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(job_id)
        return path

    def _release_destination(self, job: dict[str, Any]) -> None:
        destination = Path(str(job["destination"]))
        path = self._destination_lock_path(destination)
        try:
            if path.read_text(encoding="utf-8").strip() == str(job["id"]):
                path.unlink(missing_ok=True)
        except OSError:
            pass

    def _ensure_destination_claim(self, job: dict[str, Any], destination: Path) -> None:
        path = self._destination_lock_path(destination)
        if not path.exists():
            self._claim_destination(str(job["id"]), destination)
            return
        try:
            owner = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ComfyError(f"destination lock is unreadable for {destination}: {exc}") from exc
        if owner != str(job["id"]):
            raise ComfyError(f"destination is already owned by active Wan job {owner or 'unknown'}")

    def _save(self, job: dict[str, Any]) -> dict[str, Any]:
        job["updated_at"] = _now()
        _write_json(self._path(str(job["id"])), job)
        return job

    def status(self, job_id: str) -> dict[str, Any]:
        return _read_json(self._path(job_id))

    def update_progress(self, job_id: str, event: dict[str, Any]) -> dict[str, Any]:
        """Persist backend progress without letting it mutate the job lifecycle."""
        with self._guard:
            job = self.status(job_id)
            if job.get("state") in _TERMINAL:
                return job
            progress = {**dict(event), "updated_at": _now()}
            job["progress"] = progress
            peak = progress.get("vram_peak_bytes")
            if isinstance(peak, (int, float)):
                metrics = dict(job.get("metrics") or {})
                metrics["vram_peak_bytes"] = max(
                    int(metrics.get("vram_peak_bytes") or 0), int(peak)
                )
                job["metrics"] = metrics
            events = job.setdefault("events", [])
            previous = events[-1].get("backend_state") if events else None
            backend_state = progress.get("state")
            if backend_state != previous:
                events.append(
                    {
                        "state": job["state"],
                        "backend_state": backend_state,
                        "prompt_id": progress.get("prompt_id"),
                        "at": _now(),
                    }
                )
                if len(events) > 200:
                    job["events"] = events[-200:]
            return self._save(job)

    def submit(
        self,
        *,
        config: GenerationConfig,
        destination: Path,
        quality_tier: str,
        image: Path | None = None,
        last_image: Path | None = None,
        audio: Path | None = None,
        animate_root: Path | None = None,
    ) -> dict[str, Any]:
        destination = destination.resolve()
        job_id = f"wan-{uuid.uuid4().hex}"
        with self._guard:
            self._claim_destination(job_id, destination)
            job = {
                "schema_version": 1,
                "id": job_id,
                "state": "queued",
                "quality_tier": quality_tier,
                "config": asdict(config),
                "destination": str(destination),
                "image": str(image.resolve()) if image else None,
                "last_image": str(last_image.resolve()) if last_image else None,
                "audio": str(audio.resolve()) if audio else None,
                "animate_root": str(animate_root.resolve()) if animate_root else None,
                "submitted_at": _now(),
                "submitted_monotonic": time.monotonic(),
                "updated_at": _now(),
                "events": [{"state": "queued", "at": _now()}],
                "metrics": {"queue_ms": None, "elapsed_ms": None, "vram_peak_bytes": None},
            }
            self._save(job)
            self._futures[job_id] = self._executor.submit(self._execute, job_id, False)
        return self.status(job_id)

    def _transition(self, job: dict[str, Any], state: str, **extra: Any) -> dict[str, Any]:
        job["state"] = state
        job.update(extra)
        job.setdefault("events", []).append({"state": state, "at": _now(), **extra})
        return self._save(job)

    def _complete(self, job: dict[str, Any], destination: Path, *, recovered: bool = False) -> None:
        if not destination.is_file() or destination.stat().st_size <= 32:
            raise ComfyError(f"Wan executor reported success but wrote no usable clip at {destination}")
        started = float(job.get("started_monotonic", time.monotonic()))
        metrics = dict(job.get("metrics") or {})
        metrics["elapsed_ms"] = round((time.monotonic() - started) * 1000, 2)
        self._transition(
            job,
            "completed",
            output={"path": str(destination), "bytes": destination.stat().st_size},
            metrics=metrics,
            recovered=recovered,
            completed_at=_now(),
        )
        self._release_destination(job)

    def _execute(self, job_id: str, recovering: bool) -> None:
        with self._guard:
            job = self.status(job_id)
            if job["state"] == "cancelled":
                self._release_destination(job)
                return
            started = time.monotonic()
            submitted = job.get("submitted_monotonic")
            metrics = dict(job.get("metrics") or {})
            metrics["queue_ms"] = (
                round((started - float(submitted)) * 1000, 2) if submitted is not None else None
            )
            self._transition(
                job,
                "running",
                started_at=_now(),
                started_monotonic=started,
                metrics=metrics,
                recovered=recovering,
            )
        try:
            if recovering and self.resume is not None:
                result = self.resume(job)
                if result is not None:
                    with self._guard:
                        self._complete(self.status(job_id), result, recovered=True)
                    return
            result = self.execute(self.status(job_id))
            with self._guard:
                current = self.status(job_id)
                if current["state"] == "cancelling":
                    self._transition(current, "cancelled", cancelled_at=_now())
                    self._release_destination(current)
                else:
                    self._complete(current, result, recovered=recovering)
        except Exception as exc:  # executor boundaries must become durable state
            with self._guard:
                current = self.status(job_id)
                if current["state"] == "cancelling":
                    self._transition(current, "cancelled", cancelled_at=_now(), error=str(exc))
                else:
                    self._transition(current, "failed", failed_at=_now(), error=str(exc))
                self._release_destination(current)

    def wait(self, job_id: str, *, timeout: float) -> dict[str, Any]:
        future = self._futures.get(job_id)
        if future is not None:
            future.result(timeout=timeout)
        return self.status(job_id)

    def cancel(self, job_id: str, *, cancel_running: Callable[[], None] | None = None) -> dict[str, Any]:
        with self._guard:
            job = self.status(job_id)
            if job["state"] in _TERMINAL:
                return job
            future = self._futures.get(job_id)
            if job["state"] == "queued" and future is not None and future.cancel():
                self._transition(job, "cancelled", cancelled_at=_now())
                self._release_destination(job)
                return self.status(job_id)
            self._transition(job, "cancelling", cancellation_requested_at=_now())
        if cancel_running is not None:
            cancel_running()
        return self.status(job_id)

    def recover(self) -> dict[str, int]:
        report = {"adopted": 0, "resumed": 0, "failed": 0}
        for path in sorted(self.root.glob("wan-*.json")):
            job: dict[str, Any] | None = None
            try:
                job = _read_json(path)
                if job.get("state") in _TERMINAL:
                    continue
                destination = Path(str(job["destination"]))
                self._ensure_destination_claim(job, destination)
                if destination.is_file() and destination.stat().st_size > 32:
                    self._complete(job, destination, recovered=True)
                    report["adopted"] += 1
                    continue
                self._transition(job, "queued", recovery_requested_at=_now())
                self._futures[str(job["id"])] = self._executor.submit(
                    self._execute, str(job["id"]), True
                )
                report["resumed"] += 1
            except Exception as exc:
                report["failed"] += 1
                if job is not None and job.get("id"):
                    try:
                        self._transition(
                            job,
                            "failed",
                            failed_at=_now(),
                            error=f"restart recovery failed: {exc}",
                            recovered=True,
                        )
                    except Exception:
                        pass
        self.last_recovery = {**report, "at": _now()}
        return report

    def metrics(self) -> dict[str, Any]:
        jobs = [_read_json(path) for path in self.root.glob("wan-*.json")]
        states = ("queued", "running", "cancelling", "completed", "failed", "cancelled")
        counts = {state: sum(job.get("state") == state for job in jobs) for state in states}
        completed = counts["completed"]
        terminal = completed + counts["failed"] + counts["cancelled"]
        elapsed = [
            float(job.get("metrics", {}).get("elapsed_ms"))
            for job in jobs
            if job.get("state") == "completed" and job.get("metrics", {}).get("elapsed_ms") is not None
        ]
        queue = [
            float(job.get("metrics", {}).get("queue_ms"))
            for job in jobs
            if job.get("metrics", {}).get("queue_ms") is not None
        ]
        vram = [
            int(job.get("metrics", {}).get("vram_peak_bytes"))
            for job in jobs
            if job.get("metrics", {}).get("vram_peak_bytes") is not None
        ]
        return {
            "total": len(jobs),
            **counts,
            "success_rate": round(completed / terminal, 4) if terminal else None,
            "mean_elapsed_ms": round(sum(elapsed) / len(elapsed), 2) if elapsed else None,
            "mean_queue_ms": round(sum(queue) / len(queue), 2) if queue else None,
            "peak_vram_bytes": max(vram) if vram else None,
            "last_recovery": self.last_recovery,
        }
