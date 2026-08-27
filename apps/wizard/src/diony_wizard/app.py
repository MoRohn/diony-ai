from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from diony_contracts.cluster import (
    SEQUENTIAL_PATHS,
    WIZARD_APP_PATHS,
    bind_host,
    clear_local_locks,
    read_site_lock,
    site_lock_path,
    write_mode_lock,
    write_site_lock,
)
from diony_contracts.enums import ComputePath
from diony_contracts.live import wizard_live
from diony_contracts.models import ClusterSite
from diony_wizard.apply import (
    apply_allowed,
    bind_hf_from_answers,
    ensure_apply,
    ensure_producer,
    hf_token_set,
    load_hf_token,
    public_apply,
    public_progress,
    reset_apply,
    retry_apply,
    should_apply,
    should_start_producer,
    write_remote_site_lock,
)
from diony_wizard.curriculum import PATH_LESSON, lesson_for, validate_answers, with_defaults
from diony_wizard.prep import (
    VAULT_MOUNT,
    bind_ssh_env,
    expand_lease_connect,
    expand_site_models,
    health_probe,
    hosts_commands,
    lease_plan,
    manifest_text,
    model_pack,
    producer_href,
    producer_url,
    profile_rel,
    profile_text,
    serving_rel,
    serving_text,
    site_nodes,
    site_pack,
    snippet_text,
    unbind_ssh_env,
    vault_export,
    vault_fstab_text,
)
from diony_wizard.provisioning import (
    ProvisioningError,
    ProvisioningService,
)
from diony_wizard.prove import (
    connect_confirm,
    prove_block_reason,
    prove_clicks_live,
    prove_must_pass,
    public_prove,
    record_dry_run,
    run_probe,
)
from diony_wizard.ssh_command import SSHCommandError, parse_runpod_ssh_command
from diony_wizard.steps import ALL_STEP_IDS, Step, attach_rail, initial_steps, rail_ids

STATIC = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Diony Studio", version="0.20.0")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.middleware("http")
async def no_store_pages(request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.startswith("/static/") or path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
    return response

_steps: list[Step] = initial_steps()
_dry_run = True
_cluster_name = "spark-4x-v1"
_compute_path: str | None = None
_site: ClusterSite | None = None
_sync_ips: list[str] = []
_workbook: dict[str, dict[str, str]] = {}
_completed_at: str | None = None
_probe: dict = {}
_provisioner = ProvisioningService()
load_hf_token()


class AdvanceBody(BaseModel):
    note: str = ""
    answers: dict[str, str] = Field(default_factory=dict)
    sync_ips: list[str] = Field(default_factory=list)


class ProveBody(BaseModel):
    scope: str = "gpu"


class ProvisionStartBody(BaseModel):
    path: str


class ExistingProvisionStartBody(BaseModel):
    scope: str


class ProvisionAdvanceBody(BaseModel):
    action: str
    provider: str = ""
    signer: str = ""
    provider_token: str = ""
    hf_token: str = ""
    hosts: str = ""
    ssh_user: str = ""
    ssh_port: str = "22"
    ssh_identity_file: str = ""
    nas_ip: str = ""


class SSHCommandBody(BaseModel):
    command: str


def _public_workbook(book: dict | None = None) -> dict:
    """Workbook for APIs and the signed report. Never include a Hugging Face token."""
    src = book if book is not None else _workbook
    out: dict = {}
    for key, row in (src or {}).items():
        if not isinstance(row, dict):
            out[key] = row
            continue
        out[key] = {
            k: v
            for k, v in row.items()
            if k not in {"hf_token", "ssh_identity_file"}
        }
    return out


def _refresh_dry() -> None:
    global _dry_run
    _dry_run = not wizard_live()


def _by_id(step_id: str) -> Step:
    for s in _steps:
        if s.id == step_id:
            return s
    raise HTTPException(404, f"unknown step {step_id}")


def _active_id() -> str | None:
    active = next((s.id for s in _steps if s.status == "active"), None)
    if active is None:
        active = next((s.id for s in _steps if s.status == "failed"), None)
    return active


def _complete() -> bool:
    return all(s.status == "passed" for s in _steps)


def restore_signed_site() -> bool:
    """Rehydrate a signed site from ``{vault}/.cluster/site.json`` at Studio start.

    Sign is durable: Producer, Conductor, and the desk all read the lock, but
    Studio kept the fact only in memory, so every Studio restart (each desk
    rebuild or reopen) sent the operator back through Setup and a fresh
    existing-compute validation. A signed lock now restores the signed state.
    Returns True when a signed lock was restored.
    """
    global _steps, _completed_at, _probe
    if _completed_at is not None or any(s.status == "passed" for s in _steps):
        return False
    lock = read_site_lock()
    if not isinstance(lock, dict):
        return False
    path = str(lock.get("compute_path") or "").strip()
    signed_at = str(lock.get("signed_at") or "").strip()
    if not path or not signed_at:
        return False
    compute = lock.get("compute") if isinstance(lock.get("compute"), dict) else {}
    _bind_site(
        {
            "site": path,
            "provider": str(compute.get("provider") or ""),
            "gpu_class": str(compute.get("gpu_class") or ""),
            "signer": str(lock.get("exception_by") or ""),
        }
    )
    connect = lock.get("connect") if isinstance(lock.get("connect"), dict) else {}
    host = str(connect.get("host") or "").strip()
    if host:
        _workbook["connect"] = {
            "hosts": host,
            "ssh_user": str(connect.get("ssh_user") or "root").strip(),
            "ssh_port": str(connect.get("ssh_port") or "22").strip(),
            "nas_ip": str(connect.get("vault") or "").strip(),
        }
        bind_ssh_env(_workbook["connect"])
    if lock.get("gpu_ready"):
        _probe = {
            "ok": True,
            "live": not bool(lock.get("dry_run")),
            "host": host,
            "user": str(connect.get("ssh_user") or ""),
            "port": str(connect.get("ssh_port") or ""),
            "scope": "gpu",
            "checks": [],
            "source": "site-lock",
        }
        _workbook["gpu_prove"] = dict(_probe)
        _workbook["prove"] = {"agent": "passed"}
    _workbook["producer"] = {
        "url": str(lock.get("producer_url") or "http://producer.diony.local"),
        "ready": "Ready",
        "floors": "Idea, Production, Gallery",
        "source": "site-lock",
    }
    site_step = _steps[0]
    site_step.status = "passed"
    _steps = attach_rail(path, site_step)
    for step in _steps:
        step.status = "passed"
        step.log.append(f"Restored from the signed site lock ({signed_at}).")
    _completed_at = signed_at
    _refresh_dry()
    return True


def _pack(include_texts: bool = False) -> dict | None:
    if not _compute_path:
        return None
    return site_pack(
        _compute_path,
        _cluster_name,
        _workbook,
        _dry_run,
        include_texts=include_texts,
    )


def _report() -> dict:
    site = _site.model_dump(mode="json") if _site else None
    pack = _pack(include_texts=False)
    recipe = ((_workbook.get("brain_smoke") or {}).get("recipe")) or {
        "local": "spark-4x-v1 TP=4",
        "dual": "spark-2x-v1 TP=2",
        "b300": "b300-v1 TP=1",
        "b200": "b200-v1 TP=1",
    }.get(_compute_path or "local", "")
    connect = _workbook.get("connect") or {}
    export = vault_export(_workbook) or connect.get("nas_ip") or (_workbook.get("vault") or {}).get("nas_ip") or ""
    return {
        "sku": _cluster_name,
        "compute_path": _compute_path,
        "compute": site,
        "dry_run": _dry_run,
        "signed_at": _completed_at,
        "sync_ips": list(_sync_ips),
        "producer_url": producer_url(_compute_path, _dry_run),
        "producer_href": producer_href(_dry_run, _compute_path, _workbook),
        "pack": pack,
        "workbook": _public_workbook(),
        "nodes": site_nodes(_compute_path, _workbook) if _compute_path else [],
        "vault": VAULT_MOUNT,
        "vault_export": export,
        "recipe": recipe,
        "sequential": _compute_path in SEQUENTIAL_PATHS,
        "gpu_ready": bool((_probe or _workbook.get("gpu_prove") or {}).get("ok")),
        "prove": public_prove(_probe or _workbook.get("gpu_prove") or {}),
        "site_lock": str(site_lock_path()) if _completed_at else None,
        "hosts_commands": hosts_commands(
            (site_nodes(_compute_path, _workbook) or ["spark-01"])[0]
        )
        if _compute_path
        else [],
        "steps": [s.as_public() for s in _steps],
        "mandate": (
            "Wan is 720p negative. Deliverables are Diony 1080. "
            "Never cloud-by-default. Leased Dual-Spark, Leased B300, and Leased B200 are signed exceptions."
        ),
    }


def _seal_site() -> None:
    """Write `{vault}/.cluster/site.json` so Producer and Conductor share this Sign."""
    if not _complete() or not _site or not _compute_path:
        return
    payload = _report()
    connect = _workbook.get("connect") or {}
    host = ""
    if payload["nodes"]:
        host = str(payload["nodes"][0])
    if not host:
        host = str(connect.get("hosts") or "").split()[0].strip()
    lock = {
        "sku": payload["sku"],
        "compute_path": payload["compute_path"],
        "compute": payload["compute"],
        "nodes": payload["nodes"],
        "vault": VAULT_MOUNT,
        "vault_export": payload["vault_export"],
        "recipe": payload["recipe"],
        "sequential": payload["sequential"],
        "gpu_ready": bool(payload.get("gpu_ready")),
        "producer_url": payload["producer_url"],
        "producer_href": payload["producer_href"],
        "dry_run": (
            False
            if payload.get("gpu_ready") and _compute_path in WIZARD_APP_PATHS
            else payload["dry_run"]
        ),
        "signed_at": payload["signed_at"],
        "exception_by": _site.exception_by,
        "connect": {
            "host": host,
            "ssh_user": str(connect.get("ssh_user") or "").strip(),
            "ssh_port": str(connect.get("ssh_port") or "22").strip(),
            "vault": str(connect.get("nas_ip") or payload.get("vault_export") or ""),
        },
    }
    write_site_lock(lock)
    engine = "brain"
    write_mode_lock("SPLIT", engine, reason="wizard sign")
    hosts = payload["nodes"] or []
    if hosts:
        bind_ssh_env(_workbook.get("connect") or {})
        write_remote_site_lock(hosts[0], lock)


def _path_lesson() -> dict | None:
    try:
        return PATH_LESSON.as_public()
    except Exception:
        try:
            return lesson_for("path").as_public()
        except Exception:
            return None


def _safe_state(err: str = "") -> dict:
    """Minimal payload so GET /api/state stays 200 when a lesson book fails."""
    try:
        active = _active_id()
    except Exception:
        active = "path"
    try:
        steps = [s.as_public() for s in _steps]
    except Exception:
        steps = [
            {
                "id": "path",
                "name": "Site",
                "pass_criteria": "",
                "operator": "",
                "status": "active",
                "log": [],
            }
        ]
    try:
        apply = public_apply()
    except Exception:
        apply = {
            "running": False,
            "ok": False,
            "error": "",
            "detail": "",
            "log": [],
            "host": "",
            "live": False,
            "user": "",
            "port": "",
        }
    return {
        "sku": _cluster_name if isinstance(_cluster_name, str) else "spark-4x-v1",
        "compute_path": _compute_path,
        "compute": None,
        "dry_run": bool(_dry_run),
        "active_step": active,
        "complete": False,
        "sync_ips": list(_sync_ips) if isinstance(_sync_ips, list) else [],
        "producer_url": "http://producer.diony.local",
        "producer_href": "http://producer.diony.local",
        "vault": "/diony/vault",
        "vault_export": "",
        "pack": None,
        "plan": None,
        "model_pack": None,
        "steps": steps,
        "lesson": _path_lesson(),
        "answers": _public_workbook().get(active or "", {}) if isinstance(_workbook, dict) else {},
        "workbook": _public_workbook() if isinstance(_workbook, dict) else {},
        "mandate": (
            "Wan is 720p negative. Deliverables are Diony 1080. "
            "Never cloud-by-default. Leased Dual-Spark, Leased B300, and Leased B200 are signed exceptions."
        ),
        "report": None,
        "apply": apply,
        "prove": public_prove(_probe),
        "gpu_ready": bool(_probe.get("ok")) if isinstance(_probe, dict) else False,
        "can_pull": False,
        "hf_token": hf_token_set(),
        "confirm": None,
        "error": (err or "")[:400],
    }


def _state() -> dict:
    try:
        _refresh_dry()
        active = _active_id()
        done = _complete()
        lesson = None
        err = ""
        if active:
            try:
                lesson = lesson_for(active, _compute_path).as_public()
            except Exception as exc:
                err = str(exc)[:400]
                lesson = _path_lesson()
        report = _report()
        gpu = _site.gpu_class if _site else ""
        try:
            plan = lease_plan(_compute_path, gpu, _workbook)
        except Exception as exc:
            plan = None
            err = err or str(exc)[:400]
        try:
            pack = model_pack(_compute_path)
        except Exception as exc:
            pack = None
            err = err or str(exc)[:400]
        try:
            apply = public_apply()
        except Exception as exc:
            apply = {"running": False, "ok": False, "error": "", "detail": "", "log": []}
            err = err or str(exc)[:400]
        return {
            "sku": _cluster_name,
            "compute_path": _compute_path,
            "compute": report["compute"],
            "dry_run": _dry_run,
            "active_step": active,
            "complete": done,
            "sync_ips": _sync_ips,
            "producer_url": report["producer_url"],
            "producer_href": report["producer_href"],
            "vault": report["vault"],
            "vault_export": report["vault_export"],
            "pack": report["pack"],
            "plan": plan,
            "model_pack": pack,
            "steps": [s.as_public() for s in _steps],
            "lesson": lesson,
            "answers": _public_workbook().get(active or "", {}),
            "workbook": _public_workbook(),
            "mandate": report["mandate"],
            "report": report if done else None,
            "apply": apply,
            "prove": public_prove(_probe or _workbook.get("gpu_prove") or {}),
            "gpu_ready": bool((_probe or _workbook.get("gpu_prove") or {}).get("ok")),
            "can_pull": apply_allowed(_compute_path, _workbook),
            "hf_token": hf_token_set(),
            "confirm": (
                connect_confirm(_compute_path, _workbook.get("connect") or {})
                if _compute_path in WIZARD_APP_PATHS and active in {"connect", "prove", "app"}
                else None
            ),
            "error": err,
        }
    except Exception as exc:
        return _safe_state(str(exc))


def _bind_site(answers: dict[str, str]) -> None:
    global _site, _cluster_name, _compute_path
    site = answers.get("site", "")
    if site == ComputePath.LEASE:
        _site = ClusterSite(
            path=ComputePath.LEASE,
            profile="lease-gpu-v1",
            provider=answers.get("provider", ""),
            gpu_class=answers.get("gpu_class", ""),
            exception_by=answers.get("signer", ""),
            exception_at=datetime.now(timezone.utc),
        )
        _cluster_name = "lease-gpu-v1"
    elif site == ComputePath.DUAL:
        _site = ClusterSite(
            path=ComputePath.DUAL,
            profile="spark-2x-v1",
            provider=answers.get("provider", ""),
            gpu_class="2×GB10",
            exception_by=answers.get("signer", ""),
            exception_at=datetime.now(timezone.utc),
        )
        _cluster_name = "spark-2x-v1"
    elif site == ComputePath.B300:
        _site = ClusterSite(
            path=ComputePath.B300,
            profile="b300-v1",
            provider=answers.get("provider", ""),
            gpu_class="1×B300",
            exception_by=answers.get("signer", ""),
            exception_at=datetime.now(timezone.utc),
        )
        _cluster_name = "b300-v1"
    elif site == ComputePath.B200:
        _site = ClusterSite(
            path=ComputePath.B200,
            profile="b200-v1",
            provider=answers.get("provider", ""),
            gpu_class="1×B200",
            exception_by=answers.get("signer", ""),
            exception_at=datetime.now(timezone.utc),
        )
        _cluster_name = "b200-v1"
    else:
        _site = ClusterSite(path=ComputePath.LOCAL, profile="spark-4x-v1")
        _cluster_name = "spark-4x-v1"
    _compute_path = site


def _record_connect(answers: dict[str, str]) -> None:
    """Write Connect hosts/user/port into the workbook and SSH env (including re-saves)."""
    gpu = _site.gpu_class if _site else ""
    _workbook.update(expand_lease_connect(_compute_path, gpu, answers))
    bind_ssh_env(_workbook.get("connect") or {})


@app.get("/")
def index() -> FileResponse:
    return FileResponse(
        STATIC / "index.html",
        headers={"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"},
    )


@app.get("/api/state")
def state() -> dict:
    try:
        return _state()
    except Exception as exc:
        return _safe_state(str(exc))


@app.get("/api/provision")
def provision_state() -> dict:
    return _provisioner.refresh()


@app.post("/api/ssh/parse")
def parse_ssh_command(body: SSHCommandBody) -> dict:
    try:
        return {"connection": parse_runpod_ssh_command(body.command)}
    except SSHCommandError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/provision/start")
def provision_start(body: ProvisionStartBody) -> dict:
    try:
        return _provisioner.start(body.path)
    except ProvisioningError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/provision/existing/start")
def provision_existing_start(body: ExistingProvisionStartBody) -> dict:
    try:
        return _provisioner.start_existing(body.scope)
    except ProvisioningError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/provision/advance")
def provision_advance(body: ProvisionAdvanceBody) -> dict:
    try:
        return _provisioner.advance(body.model_dump())
    except ProvisioningError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/provision/adopt")
def provision_adopt() -> dict:
    """Record provider output and hand it to the existing idempotent App engine."""
    try:
        lease = _provisioner.consume()
        path = str(lease["path"])
        provider = str(lease["provider"])
        signer = str(lease["signer"])
        connection = dict(lease["connection"])
        if lease.get("mode") == "existing":
            return _adopt_existing_compute(lease)
        site = pass_step(
            "path",
            AdvanceBody(
                answers={"site": path, "provider": provider, "signer": signer}
            ),
        )
        if site.get("active_step") != "connect":
            raise ProvisioningError("Studio did not open the lease connection line.")
        connected = pass_step("connect", AdvanceBody(answers=connection))
        if connected.get("active_step") != "app":
            raise ProvisioningError("Studio did not accept the provider connection.")
        prepared = pass_step("app", AdvanceBody())
        _provisioner.mark_handed_off()
        prepared["provision"] = _provisioner.public()
        return prepared
    except (ProvisioningError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


def _adopt_existing_compute(existing: dict) -> dict:
    """Bind a discovered site, then let the idempotent apply engine validate/repair it."""
    global _steps
    path = str(existing.get("path") or "")
    if path not in {"local", *WIZARD_APP_PATHS}:
        raise ProvisioningError("Existing compute did not resolve to a signed Diony profile.")
    provider = str(existing.get("provider") or ("Local" if path == "local" else ""))
    signer = str(existing.get("signer") or "")
    connection = dict(existing.get("connection") or {})

    site_step = _by_id("path")
    _bind_site({"site": path, "provider": provider, "signer": signer})
    _workbook["path"] = {
        "site": path,
        "provider": provider,
        "signer": signer,
        "source": "existing-compute-agent",
    }
    site_step.status = "passed"
    site_step.log.append("LIVE pass. Existing compute detected by the read-only setup agent.")
    _steps = attach_rail(path, site_step)
    if path == "local":
        hosts = [part for part in str(connection.get("hosts") or "").splitlines() if part.strip()]
        _workbook["connect"] = dict(connection)
        _workbook["inventory"] = {
            **{f"s{index}": host.strip() for index, host in enumerate(hosts, start=1)},
            "fabric": "CX-7 RoCE · agent validated",
            "ssh_user": str(connection.get("ssh_user") or "spark"),
            "ssh_port": str(connection.get("ssh_port") or "22"),
        }
        _workbook["vault"] = {
            "nas_ip": str(connection.get("nas_ip") or ""),
            "export": VAULT_MOUNT,
        }
        bind_ssh_env(_workbook["connect"])
    else:
        _record_connect(connection)
    _workbook["existing_compute"] = {
        "profile": str(existing.get("profile") or ""),
        "scope": str(existing.get("scope") or ""),
        "validated": "agent",
    }
    _workbook.update(expand_site_models(path, {}))
    _workbook["app"] = {"existing": "yes", "repair": "idempotent"}
    hosts = site_nodes(path, _workbook)
    bind_ssh_env(_workbook.get("connect") or {})
    ensure_apply(
        path,
        hosts,
        nas=vault_export(_workbook),
        workbook=_workbook,
        existing=True,
    )
    _provisioner.mark_existing_validating()
    _provisioner.mark_existing_progress("", public_progress())
    payload = _state()
    payload["provision"] = _provisioner.public()
    return payload


@app.post("/api/provision/existing/finalize")
def provision_existing_finalize() -> dict:
    """Finish setup only after apply and every required existing-site check pass."""
    global _completed_at, _probe
    current = _provisioner.public()
    if current.get("mode") != "existing" or current.get("checkpoint") != "validating_existing":
        raise HTTPException(400, "Existing compute is not in validation.")
    apply = public_apply()
    if apply.get("running"):
        detail = str(apply.get("detail") or "Diony is completing the detected site.")
        current = _provisioner.mark_existing_progress(detail, public_progress(apply))
        payload = _state()
        payload["provision"] = current
        return payload
    if not apply.get("ok"):
        detail = str(apply.get("error") or apply.get("detail") or "Existing compute setup did not complete.")
        _provisioner.mark_existing_attention(detail, public_progress(apply))
        payload = _state()
        payload["provision"] = _provisioner.public()
        return payload

    path = str(current.get("path") or "")
    bind_ssh_env(_workbook.get("connect") or {})
    if prove_clicks_live():
        _probe = run_probe(path, _workbook, scope="gpu")
    else:
        _probe = record_dry_run(path, _workbook, scope="gpu")
    if not _probe.get("ok"):
        detail = prove_block_reason(_probe, applying=False) or "Existing compute failed final GPU validation."
        _provisioner.mark_existing_attention(detail)
        payload = _state()
        payload["provision"] = _provisioner.public()
        payload["prove"] = public_prove(_probe)
        return payload

    _workbook["gpu_prove"] = _probe
    _workbook["prove"] = {"agent": "passed"}
    _workbook["producer"] = {
        "url": producer_url(path, _dry_run),
        "ready": "Ready",
        "floors": "Idea, Production, Gallery",
        "source": "existing-compute-agent",
    }
    for step in _steps:
        step.status = "passed"
        step.log.append("LIVE pass. Existing compute agent validated this line.")
    _completed_at = datetime.now(timezone.utc).isoformat()
    _seal_site()
    handoff = producer_href(_dry_run, path, _workbook)
    _provisioner.mark_existing_complete(handoff)
    payload = _state()
    payload["provision"] = _provisioner.public()
    return payload


@app.post("/api/provision/existing/retry")
def provision_existing_retry() -> dict:
    """Restart a failed existing-site apply without discarding the connection."""
    current = _provisioner.public()
    if current.get("mode") != "existing" or current.get("checkpoint") != "validating_existing":
        raise HTTPException(400, "Existing compute is not in validation.")
    if current.get("status") != "attention":
        raise HTTPException(400, "Existing compute validation is not waiting for a retry.")
    hosts = site_nodes(_compute_path, _workbook)
    bind_ssh_env(_workbook.get("connect") or {})
    job = retry_apply(
        _compute_path,
        hosts,
        nas=vault_export(_workbook),
        workbook=_workbook,
    )
    _provisioner.mark_existing_validating()
    _provisioner.mark_existing_progress(str(job.get("detail") or ""), public_progress(job))
    payload = _state()
    payload["provision"] = _provisioner.public()
    return payload


@app.get("/api/lessons/{step_id}")
def lesson(step_id: str) -> dict:
    if step_id not in ALL_STEP_IDS:
        raise HTTPException(404, f"unknown step {step_id}")
    if step_id != "path":
        if step_id not in rail_ids(_compute_path):
            raise HTTPException(400, "not on this site’s rail")
    try:
        path = None if step_id == "path" else _compute_path
        body = lesson_for(step_id, path).as_public()
        return JSONResponse(body, headers={"Cache-Control": "no-store"})
    except KeyError as exc:
        raise HTTPException(400, "sign Site first") from exc


@app.get("/api/snippets/{sku}")
def snippet(sku: str) -> dict:
    try:
        rel, text = snippet_text(sku)
    except KeyError as exc:
        raise HTTPException(404, f"unknown switch SKU {sku}") from exc
    return {"sku": sku, "path": rel, "text": text}


@app.get("/api/apply")
def get_apply() -> dict:
    return public_apply()


@app.post("/api/apply/retry")
def post_apply_retry() -> dict:
    if _compute_path not in WIZARD_APP_PATHS:
        raise HTTPException(400, "Pack apply is Dual-Spark, B300, and B200.")
    if _active_id() not in {"app", "prove"}:
        raise HTTPException(400, "Pull pack on App or Ready.")
    if not apply_allowed(_compute_path, _workbook):
        raise HTTPException(
            400,
            "Pack pull needs the RunPod IP on Connect. Placeholder names do not SSH.",
        )
    if not hf_token_set():
        raise HTTPException(400, "Diony-Enterprise HF Token is empty. Set DIONY_ENTERPRISE_HF_TOKEN, then Pull pack.")
    hosts = site_nodes(_compute_path, _workbook)
    bind_ssh_env(_workbook.get("connect") or {})
    job = retry_apply(_compute_path, hosts, nas=vault_export(_workbook), workbook=_workbook)
    payload = _state()
    payload["apply"] = job
    return payload


@app.post("/api/prove")
def post_prove(body: ProveBody) -> dict:
    global _probe
    if _compute_path not in WIZARD_APP_PATHS:
        raise HTTPException(400, "Ready is Dual-Spark, B300, and B200.")
    active = _active_id()
    on_ready = any(s.id == "prove" and s.status != "pending" for s in _steps)
    if active != "prove" and not on_ready:
        raise HTTPException(400, "Ready after App.")
    bind_ssh_env(_workbook.get("connect") or {})
    scope = body.scope if body.scope in {"connect", "gpu"} else "gpu"
    if prove_clicks_live():
        _probe = run_probe(_compute_path, _workbook, scope=scope)
    else:
        _probe = record_dry_run(_compute_path, _workbook, scope=scope)
    _workbook["gpu_prove"] = _probe
    payload = _state()
    payload["prove"] = public_prove(_probe)
    return payload


@app.get("/api/health")
def health() -> dict:
    return health_probe()


@app.get("/api/files/manifest")
def manifest() -> dict:
    return {"path": "infra/models/manifest.yaml", "text": manifest_text()}


@app.get("/api/files/fstab")
def fstab() -> dict:
    return {"path": "infra/storage/vault.fstab.example", "text": vault_fstab_text()}


@app.get("/api/files/profile")
def profile() -> dict:
    if not _compute_path:
        raise HTTPException(400, "sign Site first")
    return {"path": profile_rel(_cluster_name), "text": profile_text(_cluster_name)}


@app.get("/api/files/serving")
def serving() -> dict:
    if not _compute_path:
        raise HTTPException(400, "sign Site first")
    return {"path": serving_rel(_cluster_name), "text": serving_text(_cluster_name)}


@app.get("/api/files/pack")
def pack() -> dict:
    if not _compute_path:
        raise HTTPException(400, "sign Site first")
    return _pack(include_texts=True) or {}


@app.get("/api/report")
def report() -> dict:
    if not _complete():
        raise HTTPException(400, "finish every line before the report exists")
    payload = _report()
    payload["health"] = health_probe()
    payload["pack"] = _pack(include_texts=True)
    return payload


@app.post("/api/steps/{step_id}/save")
def save_step(step_id: str, body: AdvanceBody) -> dict:
    _by_id(step_id)
    packed = bind_hf_from_answers(dict(body.answers))
    _workbook[step_id] = packed
    if step_id == "connect" and _compute_path in ("lease", *WIZARD_APP_PATHS) and packed:
        try:
            filled = with_defaults("connect", packed, _compute_path)
            _record_connect(filled)
        except ValueError:
            bind_ssh_env(packed)
    return _state()


@app.post("/api/steps/{step_id}/pass")
def pass_step(step_id: str, body: AdvanceBody) -> dict:
    global _completed_at, _steps, _probe
    step = _by_id(step_id)
    if step.status == "passed":
        if step_id == "connect" and _compute_path in ("lease", *WIZARD_APP_PATHS):
            answers = with_defaults(step_id, dict(body.answers), _compute_path)
            miss = validate_answers(step_id, answers, _compute_path)
            if miss:
                raise HTTPException(400, miss)
            try:
                _record_connect(answers)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
        return _state()
    if step.status not in ("active", "failed"):
        raise HTTPException(400, "step is not active")
    answers = with_defaults(step_id, dict(body.answers), None if step_id == "path" else _compute_path)
    miss = validate_answers(step_id, answers, _compute_path)
    if miss:
        raise HTTPException(400, miss)
    answers = bind_hf_from_answers(answers)
    if step_id == "path":
        try:
            _bind_site(answers)
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc
    if step_id == "connect" and _compute_path in ("lease", *WIZARD_APP_PATHS):
        try:
            _record_connect(answers)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    elif step_id == "app" and _compute_path in WIZARD_APP_PATHS:
        try:
            _workbook.update(expand_site_models(_compute_path, answers))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        _workbook["app"] = answers
        if should_apply(_compute_path, _workbook):
            hosts = site_nodes(_compute_path, _workbook)
            bind_ssh_env(_workbook.get("connect") or {})
            ensure_apply(
                _compute_path, hosts, nas=vault_export(_workbook), workbook=_workbook
            )
    elif step_id == "prove" and _compute_path in WIZARD_APP_PATHS:
        bind_ssh_env(_workbook.get("connect") or {})
        if public_apply().get("running"):
            raise HTTPException(400, "Pack is still pulling.")
        if not _probe:
            if prove_clicks_live() and prove_must_pass(_compute_path, _workbook):
                raise HTTPException(400, "Test GPU first.")
            _probe = record_dry_run(_compute_path, _workbook)
            _workbook["gpu_prove"] = _probe
        if prove_must_pass(_compute_path, _workbook) and not _probe.get("ok"):
            raise HTTPException(
                400,
                prove_block_reason(_probe, applying=False)
                or "Fix the red row, then Test GPU.",
            )
        _workbook["prove"] = answers
        _workbook["gpu_prove"] = _probe
    elif step_id == "producer" and _compute_path == "local":
        packed = dict(answers)
        packed["url"] = packed.get("url") or producer_url(_compute_path, _dry_run)
        packed["ready"] = "Ready"
        try:
            _workbook.update(expand_site_models(_compute_path, packed))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        _workbook["producer"] = packed
        if should_start_producer(_compute_path):
            hosts = site_nodes(_compute_path, _workbook)
            job = ensure_producer(_compute_path, hosts, nas=vault_export(_workbook))
            if job["running"]:
                payload = _state()
                payload["apply"] = job
                return payload
            if not job["ok"]:
                raise HTTPException(400, job["error"] or "Could not start Producer.")
    elif step_id == "producer" and _compute_path in ("lease", *WIZARD_APP_PATHS):
        packed = dict(_workbook.get("producer") or {})
        packed.update(answers)
        packed["url"] = packed.get("url") or producer_url(_compute_path, _dry_run)
        packed["ready"] = "Ready"
        _workbook["producer"] = packed
    else:
        _workbook[step_id] = answers
    if body.sync_ips:
        _sync_ips.clear()
        _sync_ips.extend(body.sync_ips)
    elif step_id == "sync":
        ips = [p.strip() for p in answers.get("ips", "").split(",") if p.strip()]
        _sync_ips.clear()
        _sync_ips.extend(ips)
    step.status = "passed"
    step.log.append(
        ("LIVE pass. Pack applied on this pair. " if wizard_live() else "DRY-RUN pass. ")
        + (body.note or "Workbook signed.")
    )
    if step_id == "path":
        _steps = attach_rail(_compute_path or "local", step)
    else:
        for s in _steps:
            if s.status == "pending":
                s.status = "active"
                break
    if _complete():
        _completed_at = datetime.now(timezone.utc).isoformat()
        _seal_site()
    return _state()


@app.post("/api/steps/{step_id}/fail")
def fail_step(step_id: str, body: AdvanceBody) -> dict:
    step = _by_id(step_id)
    if step.status != "active":
        raise HTTPException(400, "step is not active")
    step.status = "failed"
    step.log.append(f"DRY-RUN hold. {body.note or 'See the fail-if list.'}")
    return _state()


@app.post("/api/reset")
def reset() -> dict:
    global _steps, _workbook, _completed_at, _compute_path, _site, _cluster_name, _probe
    _steps = initial_steps()
    _sync_ips.clear()
    _workbook = {}
    _completed_at = None
    _compute_path = None
    _site = None
    _cluster_name = "spark-4x-v1"
    _probe = {}
    unbind_ssh_env()
    reset_apply()
    try:
        # A deliberate Start Over must not be undone by the next Studio start.
        site_lock_path().unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass
    _provisioner.reset()
    clear_local_locks()
    return _state()


def main() -> None:
    import sys

    import uvicorn

    from diony_wizard.gateway import ensure_product_urls, gateway_healthy, start_product_gateway

    start_product_gateway()
    restore_signed_site()
    skip = os.environ.get("DIONY_NAMES_SKIP", "").strip().lower() in {"1", "true", "yes"}
    if not skip and sys.stdin.isatty() and not gateway_healthy():
        ensure_product_urls()
    reload_flag = os.environ.get("DIONY_WIZARD_RELOAD", "").strip().lower()
    reload = reload_flag in {"1", "true", "yes"}
    extra: dict = {"reload": reload, "log_level": "info"}
    if reload:
        extra["reload_dirs"] = [str(Path(__file__).resolve().parent)]
    uvicorn.run(
        "diony_wizard.app:app",
        host=bind_host(),
        port=int(os.environ.get("DIONY_WIZARD_PORT", "8787")),
        **extra,
    )


if __name__ == "__main__":
    main()
