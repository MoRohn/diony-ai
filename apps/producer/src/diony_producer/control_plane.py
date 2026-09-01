"""Transactional local-first control plane for Diony production.

SQLite is the launch controller store: one authoritative Producer controller per
signed site, WAL journaling, immutable cost allocations/events, and JSON contracts that
remain portable. Heavy media stays in the vault; no model payload enters this DB.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from diony_contracts.launch import (
    AuditEvent,
    BootstrapContext,
    CostAllocationEntry,
    CostCenter,
    CostGovernance,
    Department,
    GenerationJob,
    Membership,
    MembershipRole,
    StoredEvent,
    Team,
    UsageEvent,
    User,
    WorkflowRun,
    Workspace,
)


class ControlPlaneError(RuntimeError):
    pass


class WorkspaceAccessDenied(ControlPlaneError):
    pass


class DocumentNotFound(ControlPlaneError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def control_plane_path() -> Path:
    explicit = os.environ.get("DIONY_CONTROL_PLANE_DB", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    vault = Path(
        os.environ.get("DIONY_PRODUCER_VAULT")
        or os.environ.get("DIONY_VAULT")
        or ".diony-vault"
    ).expanduser()
    return vault / ".control" / "diony.sqlite3"


# Process-wide: serializes ALL control-plane connections in this process (see
# ControlPlane._connect). Reentrant so a caller that briefly nests a read and
# a transaction on one thread never self-deadlocks.
_PLANE_DB_LOCK = threading.RLock()


class _LockedConnection(sqlite3.Connection):
    """A connection that holds _PLANE_DB_LOCK for its lifetime, releasing it
    exactly once on close()."""

    _lock_released = False

    def close(self) -> None:  # type: ignore[override]
        try:
            super().close()
        finally:
            if not self._lock_released:
                self._lock_released = True
                _PLANE_DB_LOCK.release()


class ControlPlane:
    """Small transactional document/event store with strict workspace scoping."""

    SCHEMA_VERSION = 3

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else control_plane_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._migration_lock = threading.Lock()
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        # One connection at a time per process. Two threads using this file
        # through separate connections intermittently wedged each other inside
        # ordinary sqlite calls (faulthandler dumps: one thread frozen in
        # sqlite3.connect, another mid-SELECT, for 60s+ — a GIL/sqlite-mutex
        # interleave, not row contention). Every plane call is milliseconds of
        # local I/O, so the lock costs nothing; the connection releases it on
        # close. Cross-process safety is still sqlite's own file locking.
        _PLANE_DB_LOCK.acquire()
        try:
            conn = sqlite3.connect(
                self.path, timeout=30, isolation_level=None, factory=_LockedConnection
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 30000")
            # journal_mode is NOT set here: WAL is a persistent property of the
            # database file, set once in _migrate. Re-issuing the PRAGMA on
            # every connection takes a lock that fights open transactions.
            conn.execute("PRAGMA synchronous = FULL")
            return conn
        except BaseException:
            _PLANE_DB_LOCK.release()
            raise

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def _migrate(self) -> None:
        with self._migration_lock:
            conn = self._connect()
            try:
                conn.execute("PRAGMA journal_mode = WAL")  # persistent; once per DB
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS control_meta (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS documents (
                        kind TEXT NOT NULL,
                        id TEXT NOT NULL,
                        workspace_id TEXT NOT NULL,
                        project_id TEXT NOT NULL DEFAULT '',
                        body_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        deleted_at TEXT,
                        PRIMARY KEY (kind, id)
                    );
                    CREATE INDEX IF NOT EXISTS idx_documents_scope
                        ON documents(workspace_id, kind, project_id, updated_at);
                    CREATE TABLE IF NOT EXISTS events (
                        seq INTEGER PRIMARY KEY AUTOINCREMENT,
                        workspace_id TEXT NOT NULL,
                        run_id TEXT NOT NULL,
                        project_id TEXT NOT NULL,
                        kind TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_events_run
                        ON events(workspace_id, run_id, seq);
                    CREATE TABLE IF NOT EXISTS audit_events (
                        seq INTEGER PRIMARY KEY AUTOINCREMENT,
                        id TEXT NOT NULL UNIQUE,
                        workspace_id TEXT NOT NULL,
                        object_type TEXT NOT NULL,
                        object_id TEXT NOT NULL,
                        correlation_id TEXT NOT NULL,
                        body_json TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_audit_workspace
                        ON audit_events(workspace_id, seq);
                    CREATE TABLE IF NOT EXISTS encrypted_secrets (
                        workspace_id TEXT NOT NULL,
                        provider_id TEXT NOT NULL,
                        name TEXT NOT NULL,
                        ciphertext TEXT NOT NULL,
                        masked TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (workspace_id, provider_id, name)
                    );
                    CREATE TABLE IF NOT EXISTS workflow_leases (
                        run_id TEXT PRIMARY KEY,
                        owner_id TEXT NOT NULL,
                        expires_at TEXT NOT NULL
                    );
                    """
                )
                conn.execute(
                    "INSERT OR REPLACE INTO control_meta(key, value) VALUES('schema_version', ?)",
                    (str(self.SCHEMA_VERSION),),
                )
            finally:
                conn.close()

    @staticmethod
    def _json(value: Any) -> str:
        if hasattr(value, "model_dump"):
            value = value.model_dump(mode="json")
        return json.dumps(value, separators=(",", ":"), sort_keys=True)

    @staticmethod
    def _row_body(row: sqlite3.Row) -> dict[str, Any]:
        return json.loads(row["body_json"])

    def bootstrap_local(self) -> BootstrapContext:
        user = User(id="local-user", email="", display_name="Local maker")
        workspace = Workspace(
            id="local-workspace",
            name="Local Studio",
            slug="local-studio",
            owner_user_id=user.id,
            controller_origin=os.environ.get(
                "DIONY_PRODUCER_PUBLIC_URL", "http://producer.diony.local"
            ).rstrip("/"),
            controller_fingerprint="DIONY LOCAL CONTROLLER",
        )
        admin_team = Team(
            id="local-admin-team",
            workspace_id=workspace.id,
            name="Studio Administration",
        )
        department = Department(
            id="local-department",
            workspace_id=workspace.id,
            name="Local Production",
            code="LOCAL",
            manager_user_ids=[user.id],
            team_ids=[admin_team.id],
        )
        cost_center = CostCenter(
            id="local-production",
            workspace_id=workspace.id,
            department_id=department.id,
            name="Local Production",
            code="LOCAL-PROD",
            team_ids=[admin_team.id],
            owner_membership_id=f"{workspace.id}:{user.id}",
        )
        governance = CostGovernance(
            id="cost-governance",
            workspace_id=workspace.id,
            admin_user_id=user.id,
            admin_team_id=admin_team.id,
            default_cost_center_id=cost_center.id,
        )
        membership = Membership(
            id="local-workspace:local-user",
            user_id=user.id,
            workspace_id=workspace.id,
            team_ids=[admin_team.id],
            role=MembershipRole.OWNER,
            profile_name=user.display_name,
            profile_name_key=user.display_name.casefold(),
            personal_cost_center_id=cost_center.id,
            assigned_cost_center_ids=[cost_center.id],
        )
        self.put_document("user", user.id, workspace.id, user.model_dump(mode="json"))
        self.put_document("workspace", workspace.id, workspace.id, workspace.model_dump(mode="json"))
        self.put_document(
            "membership", membership.id, workspace.id, membership.model_dump(mode="json")
        )
        defaults = (
            ("team", admin_team),
            ("department", department),
            ("cost_center", cost_center),
            ("cost_governance", governance),
        )
        for kind, document in defaults:
            try:
                self.get_document(kind, document.id, workspace.id)
            except DocumentNotFound:
                self.put_document(
                    kind, document.id, workspace.id, document.model_dump(mode="json")
                )
        return BootstrapContext(user=user, workspace=workspace, membership=membership)

    def put_document(
        self,
        kind: str,
        document_id: str,
        workspace_id: str,
        body: dict[str, Any],
        *,
        project_id: str = "",
        deleted_at: datetime | None = None,
    ) -> dict[str, Any]:
        now = utc_now().isoformat()
        payload = self._json(body)
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT workspace_id, created_at FROM documents WHERE kind=? AND id=?",
                (kind, document_id),
            ).fetchone()
            if row and row["workspace_id"] != workspace_id:
                raise WorkspaceAccessDenied(f"{kind}/{document_id} belongs to another workspace")
            created = row["created_at"] if row else now
            conn.execute(
                """
                INSERT INTO documents(kind,id,workspace_id,project_id,body_json,created_at,updated_at,deleted_at)
                VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(kind,id) DO UPDATE SET
                  project_id=excluded.project_id,
                  body_json=excluded.body_json,
                  updated_at=excluded.updated_at,
                  deleted_at=excluded.deleted_at
                """,
                (
                    kind,
                    document_id,
                    workspace_id,
                    project_id,
                    payload,
                    created,
                    now,
                    deleted_at.isoformat() if deleted_at else None,
                ),
            )
        return body

    def put_documents_atomic(
        self,
        documents: list[tuple[str, str, str, dict[str, Any]]],
    ) -> None:
        """Persist a related document set in one controller transaction."""
        now = utc_now().isoformat()
        with self.transaction() as conn:
            for kind, document_id, workspace_id, body in documents:
                row = conn.execute(
                    "SELECT workspace_id, created_at FROM documents WHERE kind=? AND id=?",
                    (kind, document_id),
                ).fetchone()
                if row and row["workspace_id"] != workspace_id:
                    raise WorkspaceAccessDenied(
                        f"{kind}/{document_id} belongs to another workspace"
                    )
                created = row["created_at"] if row else now
                conn.execute(
                    """
                    INSERT INTO documents(kind,id,workspace_id,project_id,body_json,created_at,updated_at,deleted_at)
                    VALUES(?,?,?,?,?,?,?,NULL)
                    ON CONFLICT(kind,id) DO UPDATE SET
                      body_json=excluded.body_json,
                      updated_at=excluded.updated_at,
                      deleted_at=NULL
                    """,
                    (
                        kind,
                        document_id,
                        workspace_id,
                        "",
                        self._json(body),
                        created,
                        now,
                    ),
                )

    def get_document(
        self,
        kind: str,
        document_id: str,
        workspace_id: str,
        *,
        include_deleted: bool = False,
    ) -> dict[str, Any]:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM documents WHERE kind=? AND id=?", (kind, document_id)
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            raise DocumentNotFound(f"{kind}/{document_id} was not found")
        if row["workspace_id"] != workspace_id:
            raise WorkspaceAccessDenied(f"{kind}/{document_id} belongs to another workspace")
        if row["deleted_at"] and not include_deleted:
            raise DocumentNotFound(f"{kind}/{document_id} was deleted")
        return self._row_body(row)

    def list_documents(
        self,
        kind: str,
        workspace_id: str,
        *,
        project_id: str | None = None,
        include_deleted: bool = False,
    ) -> list[dict[str, Any]]:
        clauses = ["kind=?", "workspace_id=?"]
        args: list[Any] = [kind, workspace_id]
        if project_id is not None:
            clauses.append("project_id=?")
            args.append(project_id)
        if not include_deleted:
            clauses.append("deleted_at IS NULL")
        conn = self._connect()
        try:
            rows = conn.execute(
                f"SELECT body_json FROM documents WHERE {' AND '.join(clauses)} ORDER BY updated_at",
                tuple(args),
            ).fetchall()
        finally:
            conn.close()
        return [json.loads(row["body_json"]) for row in rows]

    def find_document_by_field(
        self, kind: str, field: str, value: str
    ) -> dict[str, Any] | None:
        """Find a public-entry document by an exact indexed-in-payload value.

        Used only for high-entropy invitation digests. Callers must still validate
        document status, expiry, workspace, and identity binding.
        """
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT body_json FROM documents WHERE kind=? AND deleted_at IS NULL",
                (kind,),
            ).fetchall()
        finally:
            conn.close()
        for row in rows:
            body = json.loads(row["body_json"])
            if str(body.get(field, "")) == value:
                return body
        return None

    def add_membership(
        self, user_id: str, workspace_id: str, role: MembershipRole
    ) -> Membership:
        membership = Membership(
            id=f"{workspace_id}:{user_id}",
            user_id=user_id,
            workspace_id=workspace_id,
            role=role,
        )
        self.put_document(
            "membership", membership.id, workspace_id, membership.model_dump(mode="json")
        )
        return membership

    def authorize(
        self, user_id: str, workspace_id: str, minimum_role: MembershipRole
    ) -> Membership:
        try:
            body = self.get_document("membership", f"{workspace_id}:{user_id}", workspace_id)
        except (DocumentNotFound, WorkspaceAccessDenied) as exc:
            raise WorkspaceAccessDenied("You do not belong to this workspace") from exc
        membership = Membership.model_validate(body)
        if (
            membership.deleted_at
            or membership.status.value != "active"
            or membership.role.rank < minimum_role.rank
        ):
            raise WorkspaceAccessDenied(
                f"{minimum_role.value} access is required for this production action"
            )
        return membership

    def save_run(self, run: WorkflowRun) -> WorkflowRun:
        run.updated_at = utc_now()
        self.put_document(
            "workflow_run",
            run.id,
            run.workspace_id,
            run.model_dump(mode="json"),
            project_id=run.project_id,
        )
        return run

    def claim_run(self, run_id: str, *, owner_id: str, lease_seconds: int = 60) -> bool:
        """Acquire a cross-process dispatch lease so a stage is never invoked twice."""
        now = utc_now()
        expires = now + timedelta(seconds=max(10, min(lease_seconds, 3600)))
        with self.transaction() as conn:
            conn.execute("DELETE FROM workflow_leases WHERE expires_at<=?", (now.isoformat(),))
            row = conn.execute(
                "SELECT owner_id FROM workflow_leases WHERE run_id=?", (run_id,)
            ).fetchone()
            if row and row["owner_id"] != owner_id:
                return False
            conn.execute(
                """
                INSERT INTO workflow_leases(run_id,owner_id,expires_at) VALUES(?,?,?)
                ON CONFLICT(run_id) DO UPDATE SET owner_id=excluded.owner_id,expires_at=excluded.expires_at
                """,
                (run_id, owner_id, expires.isoformat()),
            )
        return True

    def release_run(self, run_id: str, *, owner_id: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                "DELETE FROM workflow_leases WHERE run_id=? AND owner_id=?",
                (run_id, owner_id),
            )

    def run(self, workspace_id: str, run_id: str) -> WorkflowRun:
        return WorkflowRun.model_validate(
            self.get_document("workflow_run", run_id, workspace_id)
        )

    def runs(self, workspace_id: str, *, project_id: str | None = None) -> list[WorkflowRun]:
        return [
            WorkflowRun.model_validate(row)
            for row in self.list_documents(
                "workflow_run", workspace_id, project_id=project_id
            )
        ]

    def save_job(self, job: GenerationJob) -> GenerationJob:
        job.updated_at = utc_now()
        self.put_document(
            "generation_job",
            job.id,
            job.workspace_id,
            job.model_dump(mode="json"),
            project_id=job.project_id,
        )
        return job

    def job(self, workspace_id: str, job_id: str) -> GenerationJob:
        return GenerationJob.model_validate(
            self.get_document("generation_job", job_id, workspace_id)
        )

    def jobs(self, workspace_id: str, run_id: str) -> list[GenerationJob]:
        rows = self.list_documents("generation_job", workspace_id)
        return [GenerationJob.model_validate(row) for row in rows if row.get("run_id") == run_id]

    def save_usage(self, usage: UsageEvent) -> UsageEvent:
        self.put_document(
            "usage_event",
            usage.id,
            usage.workspace_id,
            usage.model_dump(mode="json"),
            project_id=usage.project_id,
        )
        return usage

    def save_cost_allocation(self, allocation: CostAllocationEntry) -> CostAllocationEntry:
        self.put_document(
            "cost_allocation",
            allocation.id,
            allocation.workspace_id,
            allocation.model_dump(mode="json"),
            project_id=allocation.project_id,
        )
        return allocation

    def cost_allocations(
        self, workspace_id: str, *, project_id: str | None = None
    ) -> list[CostAllocationEntry]:
        return [
            CostAllocationEntry.model_validate(row)
            for row in self.list_documents(
                "cost_allocation", workspace_id, project_id=project_id
            )
        ]

    def usage(self, workspace_id: str, *, project_id: str | None = None) -> list[UsageEvent]:
        return [
            UsageEvent.model_validate(row)
            for row in self.list_documents(
                "usage_event", workspace_id, project_id=project_id
            )
        ]

    def append_event(
        self,
        *,
        workspace_id: str,
        run_id: str,
        project_id: str,
        kind: str,
        payload: dict[str, Any],
    ) -> StoredEvent:
        now = utc_now()
        with self.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO events(workspace_id,run_id,project_id,kind,payload_json,created_at) VALUES(?,?,?,?,?,?)",
                (workspace_id, run_id, project_id, kind, self._json(payload), now.isoformat()),
            )
            seq = int(cursor.lastrowid)
        return StoredEvent(
            seq=seq,
            workspace_id=workspace_id,
            run_id=run_id,
            project_id=project_id,
            kind=kind,
            payload=payload,
            created_at=now,
        )

    def events(
        self, workspace_id: str, run_id: str, *, after_seq: int = 0, limit: int = 500
    ) -> list[StoredEvent]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT * FROM events
                WHERE workspace_id=? AND run_id=? AND seq>?
                ORDER BY seq LIMIT ?
                """,
                (workspace_id, run_id, after_seq, max(1, min(limit, 2000))),
            ).fetchall()
        finally:
            conn.close()
        return [
            StoredEvent(
                seq=row["seq"],
                workspace_id=row["workspace_id"],
                run_id=row["run_id"],
                project_id=row["project_id"],
                kind=row["kind"],
                payload=json.loads(row["payload_json"]),
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    def audit(
        self,
        *,
        workspace_id: str,
        actor_user_id: str,
        action: str,
        object_type: str,
        object_id: str,
        correlation_id: str,
        before: Any = None,
        after: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> AuditEvent:
        def digest(value: Any) -> str:
            if value is None:
                return ""
            return hashlib.sha256(self._json(value).encode()).hexdigest()

        event = AuditEvent(
            id=f"audit-{uuid.uuid4().hex}",
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            action=action,
            object_type=object_type,
            object_id=object_id,
            correlation_id=correlation_id,
            before_hash=digest(before),
            after_hash=digest(after),
            metadata=metadata or {},
        )
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO audit_events(id,workspace_id,object_type,object_id,correlation_id,body_json,created_at)
                VALUES(?,?,?,?,?,?,?)
                """,
                (
                    event.id,
                    event.workspace_id,
                    event.object_type,
                    event.object_id,
                    event.correlation_id,
                    event.model_dump_json(),
                    event.created_at.isoformat(),
                ),
            )
        return event

    def audit_log(self, workspace_id: str, *, limit: int = 500) -> list[AuditEvent]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT body_json FROM audit_events WHERE workspace_id=? ORDER BY seq DESC LIMIT ?",
                (workspace_id, max(1, min(limit, 2000))),
            ).fetchall()
        finally:
            conn.close()
        return [AuditEvent.model_validate_json(row["body_json"]) for row in rows]

    def erase_project_records(
        self,
        workspace_id: str,
        project_id: str,
        *,
        actor_user_id: str,
        correlation_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> AuditEvent:
        """Remove project-scoped controller payloads, then leave one hash-only erasure receipt."""
        with self.transaction() as conn:
            rows = conn.execute(
                "SELECT id FROM documents WHERE workspace_id=? AND project_id=?",
                (workspace_id, project_id),
            ).fetchall()
            object_ids = [project_id, *(str(row["id"]) for row in rows)]
            conn.execute(
                "DELETE FROM documents WHERE workspace_id=? AND project_id=?",
                (workspace_id, project_id),
            )
            conn.execute(
                "DELETE FROM documents WHERE workspace_id=? AND kind='project' AND id=?",
                (workspace_id, project_id),
            )
            conn.execute(
                "DELETE FROM events WHERE workspace_id=? AND project_id=?",
                (workspace_id, project_id),
            )
            if object_ids:
                placeholders = ",".join("?" for _ in object_ids)
                conn.execute(
                    f"DELETE FROM audit_events WHERE workspace_id=? AND object_id IN ({placeholders})",
                    (workspace_id, *object_ids),
                )
        return self.audit(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            action="project.erased",
            object_type="project",
            object_id=project_id,
            correlation_id=correlation_id,
            metadata=metadata or {},
        )

    def put_encrypted_secret(
        self,
        workspace_id: str,
        provider_id: str,
        name: str,
        ciphertext: str,
        masked: str,
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO encrypted_secrets(workspace_id,provider_id,name,ciphertext,masked,updated_at)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(workspace_id,provider_id,name) DO UPDATE SET
                  ciphertext=excluded.ciphertext,
                  masked=excluded.masked,
                  updated_at=excluded.updated_at
                """,
                (workspace_id, provider_id, name, ciphertext, masked, utc_now().isoformat()),
            )

    def encrypted_secret(
        self, workspace_id: str, provider_id: str, name: str
    ) -> tuple[str, str] | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT ciphertext, masked FROM encrypted_secrets WHERE workspace_id=? AND provider_id=? AND name=?",
                (workspace_id, provider_id, name),
            ).fetchone()
        finally:
            conn.close()
        return (row["ciphertext"], row["masked"]) if row else None

    def delete_encrypted_secret(
        self, workspace_id: str, provider_id: str, name: str
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                "DELETE FROM encrypted_secrets WHERE workspace_id=? AND provider_id=? AND name=?",
                (workspace_id, provider_id, name),
            )

    def encrypted_secret_catalog(self, workspace_id: str) -> list[dict[str, str]]:
        """Return safe BYOK metadata; ciphertext and plaintext never leave the vault."""
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT provider_id,name,masked,updated_at FROM encrypted_secrets
                WHERE workspace_id=? ORDER BY provider_id,name
                """,
                (workspace_id,),
            ).fetchall()
        finally:
            conn.close()
        return [dict(row) for row in rows]

    def metrics(self, workspace_id: str) -> dict[str, int | float]:
        runs = self.runs(workspace_id)
        jobs = [job for run in runs for job in self.jobs(workspace_id, run.id)]
        usage = self.usage(workspace_id)
        allocations = self.cost_allocations(workspace_id)
        active = sum(1 for run in runs if not run.status.terminal)
        succeeded = sum(1 for run in runs if run.status.value == "succeeded")
        failed = sum(1 for run in runs if run.status.value == "failed")
        total_cost = sum(row.cost_usd_micros for row in allocations)
        internal_credits = sum(row.internal_credits for row in allocations)
        terminal = succeeded + failed
        success_rate = (succeeded / terminal) if terminal else 0.0
        video_microunits = sum(
            event.quantity_microunits
            for event in usage
            if event.unit in {"video_second", "video_seconds", "second", "seconds"}
        )
        cost_per_video_minute = (
            (total_cost * 60_000_000 + video_microunits // 2) // video_microunits
            if video_microunits > 0
            else 0
        )
        completed_latencies = [
            (job.completed_at - job.started_at).total_seconds() * 1000
            for job in jobs
            if job.completed_at is not None and job.started_at is not None
        ]
        return {
            "workflow_runs": len(runs),
            "workflow_active": active,
            "workflow_succeeded": succeeded,
            "workflow_failed": failed,
            "workflow_success_rate": round(success_rate, 6),
            "generation_jobs": len(jobs),
            "queue_depth": sum(
                1 for job in jobs if job.status.value in {"queued", "retry_wait", "waiting_provider"}
            ),
            "cost_usd_micros": total_cost,
            "internal_credits": internal_credits,
            "cost_per_video_minute_usd_micros": cost_per_video_minute,
            "unrated_usage_events": sum(1 for event in usage if event.measurement == "unrated"),
            "generation_job_latency_ms_avg": round(
                sum(completed_latencies) / len(completed_latencies), 2
            )
            if completed_latencies
            else 0.0,
        }


def current_control_plane() -> ControlPlane:
    plane = ControlPlane()
    plane.bootstrap_local()
    return plane
