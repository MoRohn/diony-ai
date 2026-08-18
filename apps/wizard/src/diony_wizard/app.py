from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from diony_contracts.cluster import bind_host, site_lock_path, write_mode_lock, write_site_lock
from diony_contracts.enums import ComputePath
from diony_contracts.live import wizard_live
from diony_contracts.models import ClusterSite
from diony_wizard.apply import ensure_apply, public_apply, reset_apply, should_apply
from diony_wizard.curriculum import lesson_for, validate_answers, with_defaults
from diony_wizard.prep import (
    expand_lease_connect,
    expand_site_models,
    health_probe,
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
    vault_fstab_text,
)
from diony_wizard.steps import ALL_STEP_IDS, Step, attach_rail, initial_steps, rail_ids

STATIC = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Diony Wizard", version="0.16.0")
app.mount("/static", StaticFiles(directory=STATIC), name="static")

_steps: list[Step] = initial_steps()
_dry_run = True
_cluster_name = "spark-4x-v1"
_compute_path: str | None = None
_site: ClusterSite | None = None
_sync_ips: list[str] = []
_workbook: dict[str, dict[str, str]] = {}
_completed_at: str | None = None


class AdvanceBody(BaseModel):
    note: str = ""
    answers: dict[str, str] = Field(default_factory=dict)
    sync_ips: list[str] = Field(default_factory=list)


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
    }.get(_compute_path or "local", "")
    connect = _workbook.get("connect") or {}
    vault = connect.get("nas_ip") or (_workbook.get("vault") or {}).get("nas_ip") or "/diony/vault"
    return {
        "sku": _cluster_name,
        "compute_path": _compute_path,
        "compute": site,
        "dry_run": _dry_run,
        "signed_at": _completed_at,
        "sync_ips": list(_sync_ips),
        "producer_url": producer_url(_compute_path, _dry_run),
        "producer_href": producer_href(_dry_run),
        "pack": pack,
        "workbook": _workbook,
        "nodes": site_nodes(_compute_path, _workbook) if _compute_path else [],
        "vault": vault,
        "recipe": recipe,
        "sequential": _compute_path in ("dual", "b300"),
        "site_lock": str(site_lock_path()) if _completed_at else None,
        "steps": [s.as_public() for s in _steps],
        "mandate": (
            "Wan is 720p negative. Deliverables are Diony 1080. "
            "Never cloud-by-default. Leased Dual-Spark and Leased B300 are signed exceptions."
        ),
    }


def _seal_site() -> None:
    """Write `{vault}/.cluster/site.json` so Producer and Conductor share this Sign."""
    if not _complete() or not _site or not _compute_path:
        return
    payload = _report()
    write_site_lock(
        {
            "sku": payload["sku"],
            "compute_path": payload["compute_path"],
            "compute": payload["compute"],
            "nodes": payload["nodes"],
            "vault": payload["vault"],
            "recipe": payload["recipe"],
            "sequential": payload["sequential"],
            "producer_url": payload["producer_url"],
            "dry_run": payload["dry_run"],
            "signed_at": payload["signed_at"],
            "exception_by": _site.exception_by,
        }
    )
    engine = "brain"
    write_mode_lock("SPLIT", engine, reason="wizard sign")


def _state() -> dict:
    _refresh_dry()
    active = _active_id()
    done = _complete()
    lesson = None
    if active:
        try:
            lesson = lesson_for(active, _compute_path).as_public()
        except KeyError:
            lesson = lesson_for("path").as_public()
    report = _report()
    gpu = _site.gpu_class if _site else ""
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
        "pack": report["pack"],
        "plan": lease_plan(_compute_path, gpu, _workbook),
        "model_pack": model_pack(_compute_path),
        "steps": [s.as_public() for s in _steps],
        "lesson": lesson,
        "answers": _workbook.get(active or "", {}),
        "workbook": _workbook,
        "mandate": report["mandate"],
        "report": report if done else None,
        "apply": public_apply(),
    }


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
    else:
        _site = ClusterSite(path=ComputePath.LOCAL, profile="spark-4x-v1")
        _cluster_name = "spark-4x-v1"
    _compute_path = site


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/state")
def state() -> dict:
    return _state()


@app.get("/api/lessons/{step_id}")
def lesson(step_id: str) -> dict:
    if step_id not in ALL_STEP_IDS:
        raise HTTPException(404, f"unknown step {step_id}")
    if step_id != "path":
        if step_id not in rail_ids(_compute_path):
            raise HTTPException(400, "not on this site’s rail")
    try:
        path = None if step_id == "path" else _compute_path
        return lesson_for(step_id, path).as_public()
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
    _workbook[step_id] = dict(body.answers)
    return _state()


@app.post("/api/steps/{step_id}/pass")
def pass_step(step_id: str, body: AdvanceBody) -> dict:
    global _completed_at, _steps
    step = _by_id(step_id)
    if step.status not in ("active", "failed"):
        raise HTTPException(400, "step is not active")
    answers = with_defaults(step_id, dict(body.answers), None if step_id == "path" else _compute_path)
    miss = validate_answers(step_id, answers, _compute_path)
    if miss:
        raise HTTPException(400, miss)
    if step_id == "path":
        try:
            _bind_site(answers)
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc
    if step_id == "connect" and _compute_path in ("lease", "dual", "b300"):
        gpu = _site.gpu_class if _site else ""
        try:
            _workbook.update(expand_lease_connect(_compute_path, gpu, answers))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    elif step_id == "app" and _compute_path in ("b300", "dual"):
        try:
            _workbook.update(expand_site_models(_compute_path, answers))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        _workbook["app"] = answers
        if should_apply(_compute_path):
            hosts = site_nodes(_compute_path, _workbook)
            job = ensure_apply(_compute_path, hosts)
            if job["running"]:
                payload = _state()
                payload["apply"] = job
                return payload
            if not job["ok"]:
                raise HTTPException(400, job["error"] or "Pack apply failed on this pair.")
    elif step_id == "producer" and _compute_path == "local":
        try:
            _workbook.update(expand_site_models(_compute_path, answers))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        _workbook["producer"] = answers
    elif step_id == "producer" and _compute_path in ("lease", "dual", "b300"):
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
    global _steps, _workbook, _completed_at, _compute_path, _site, _cluster_name
    _steps = initial_steps()
    _sync_ips.clear()
    _workbook = {}
    _completed_at = None
    _compute_path = None
    _site = None
    _cluster_name = "spark-4x-v1"
    reset_apply()
    return _state()


def main() -> None:
    import uvicorn

    uvicorn.run(
        "diony_wizard.app:app",
        host=bind_host(),
        port=int(os.environ.get("DIONY_WIZARD_PORT", "8787")),
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
