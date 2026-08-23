"""Single-authority durable workflow worker for a Diony workspace."""

from __future__ import annotations

import os
import signal
import time

from diony_contracts.launch import JobStatus
from diony_producer.control_plane import ControlPlane
from diony_producer.observability import log_event
from diony_producer.providers import ProviderRegistry
from diony_producer.workflow import DurableWorkflowEngine


ACTIVE = {
    JobStatus.QUEUED,
    JobStatus.RUNNING,
    JobStatus.RETRY_WAIT,
    JobStatus.WAITING_PROVIDER,
}


def run_once(
    plane: ControlPlane,
    registry: ProviderRegistry,
    *,
    workspace_id: str,
) -> int:
    """Advance each eligible run by at most one persisted stage checkpoint."""
    engine = DurableWorkflowEngine(plane, registry)
    advanced = 0
    for run in plane.runs(workspace_id):
        if run.status not in ACTIVE:
            continue
        engine.advance(run.id, workspace_id=workspace_id)
        advanced += 1
    return advanced


def main() -> None:
    workspace_id = os.environ.get("DIONY_WORKSPACE_ID", "local-workspace").strip()
    poll_seconds = max(0.25, min(float(os.environ.get("DIONY_WORKER_POLL_SECONDS", "1")), 30))
    plane = ControlPlane()
    if workspace_id == "local-workspace" and os.environ.get("DIONY_AUTH_MODE", "local") == "local":
        plane.bootstrap_local()
    registry = ProviderRegistry.with_diony_defaults()
    stopping = False

    def stop(*_: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    while not stopping:
        advanced = run_once(plane, registry, workspace_id=workspace_id)
        if advanced:
            log_event(
                "worker.cycle.completed",
                workspace_id=workspace_id,
                runs_advanced=advanced,
            )
        time.sleep(poll_seconds)
