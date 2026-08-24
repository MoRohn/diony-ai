"""FARM loop: one Wan clip at a time. Dual-Spark drains brain first."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import wave
from datetime import datetime, timezone
from pathlib import Path

from diony_contracts.cluster import (
    RETAKE_CAP,
    read_site_lock,
    refuse_concurrent,
    site_status,
    write_mode_lock,
)
from diony_contracts.enums import (
    ArtifactKind,
    ContinuityMode,
    Engine,
    ProjectStatus,
    QCVerdict,
    ReferenceLock,
    TakeOrigin,
)
from diony_contracts.live import inference_live
from diony_contracts.models import (
    Artifact,
    GenerationExecutionReceipt,
    ProductionPauseReceipt,
    Project,
    QCReport,
    Shot,
    Take,
)
from diony_contracts.stitch import assemble_program
from diony_contracts.studio import refresh_reference_shot
from diony_contracts.wan_prompt import prompt_receipt_sha256, repair_wan_prompt_for_qc
from diony_producer.camera import (
    camera_task_for,
    continuity_frame,
    generate_clip,
    live_camera,
    reference_frame,
)
from diony_producer.gallery_join import probe_media, resolve_media_tool
from diony_producer.performance import PerformanceJournal
from diony_producer.quality import MEASUREMENT_VERSION, measure_negative_quality
from diony_producer.vault import persist_project, record_activity, vault_root
from diony_producer.voice import prepare_voice_for_take, verify_voice_animation

_lock = threading.Lock()
_busy = False
_detail = ""
_error = ""
_shot_id = ""
_done = 0
_total = 0
_pending: list[Take] = []
_cancel = False
_generation = 0
_REFERENCE_CHECKPOINTS = frozenset({"first", "quarter", "middle", "three_quarter", "last"})
_AUTO_RETAKE_QC_PREFIXES = (
    "Shot boundary continuity failed",
    "Reference lock failed",
    "Measured motion failed",
    "Measured temporal coherence failed",
    "Measured flicker failed",
    "Voice identity failed",
    "Voice performance failed",
    "Voice audio failed",
)
_INFRASTRUCTURE_QC_PREFIXES = (
    "GPU overlay has less than",
    "GPU overlay has ",
    "Vault has ",
)
_PERFORMANCE = PerformanceJournal()
_ATTENTION_RECEIPT_NAME = "wan-cudnn-sdpa-validation.json"
_VOLUME_USAGE_CACHE: dict[str, float | int] = {"path": "", "at": 0.0, "used": 0}


class CameraResourceBlocked(RuntimeError):
    """Dispatch is safe to retry without creating or rejecting a Take."""


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + ".part")
    pending.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    pending.replace(path)


def _attention_plan(project: Project, take: Take) -> tuple[str | None, bool, Path | None]:
    """Select cuDNN for a promoted run or exactly one automatic Project canary."""
    lock = read_site_lock() or {}
    compute = lock.get("compute") if isinstance(lock.get("compute"), dict) else {}
    compute_path = str(lock.get("compute_path") or compute.get("path") or "").strip().lower()
    if compute_path != "b200":
        return None, False, None
    path = vault_root() / "qualification" / _ATTENTION_RECEIPT_NAME
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None, False, None
    try:
        from diony_wan.attention_qualification import (
            attention_decision,
            bind_project_canary,
        )

        decision = attention_decision(path, project_id=project.id)
        if decision.enabled:
            return decision.backend, False, path
        prior_scope = str(receipt.get("scope_project_id") or "").strip()
        expected_take = str(receipt.get("quality_canary_take_id") or "").strip()
        if prior_scope == project.id and expected_take and expected_take != take.id:
            return None, False, path
        rebound = bind_project_canary(receipt, project_id=project.id, take_id=take.id)
    except (ImportError, ValueError):
        return None, False, path
    if prior_scope and prior_scope != project.id:
        archive = path.parent / "projects" / prior_scope / path.name
        if not archive.is_file():
            _write_json_atomic(archive, receipt)
    if rebound != receipt:
        _write_json_atomic(path, rebound)
        record_activity(
            "gpu",
            f"{take.id} · B200 cuDNN attention quality canary selected",
            level="live",
            shot_id=take.shot_id,
        )
    return "cudnn_sdpa", True, path


def _complete_attention_canary(project: Project, take: Take, receipt_path: Path) -> None:
    """Finish and promote the exact optimized Take before dispatching the remainder."""
    from diony_producer.finish import _finish_one
    from diony_wan.attention_qualification import promote_quality_canary

    qc = take.qc
    if qc is None or qc.verdict != QCVerdict.PASS:
        raise RuntimeError("B200 attention canary did not pass measured AutoQC.")
    reference_verified = qc.reference_verified is True
    temporal_verified = bool(
        qc.measurement_version
        and qc.measured_frame_count > 1
        and qc.morph_ok
        and qc.flicker_ok
    )
    if not reference_verified or not temporal_verified:
        raise RuntimeError("B200 attention canary lacks reference or temporal evidence.")
    record_activity(
        "sr",
        f"{take.id} · qualifying B200 attention at 1080 finish",
        level="live",
        shot_id=take.shot_id,
    )
    _finish_one(project, take)
    negative = next(
        (artifact for artifact in take.artifacts if artifact.kind == ArtifactKind.NEGATIVE_720),
        None,
    )
    master = next(
        (artifact for artifact in take.artifacts if artifact.kind == ArtifactKind.FINISH_1080),
        None,
    )
    master_verified = bool(
        master
        and master.width == 1920
        and master.height == 1080
        and master.bytes > 32
        and len(master.sha256) == 64
        and (vault_root() / master.path).is_file()
    )
    if negative is None or not master_verified:
        raise RuntimeError("B200 attention canary did not land a verified 1080 master.")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    quality_payload = {
        "project_id": project.id,
        "take_id": take.id,
        "negative_sha256": negative.sha256,
        "master_sha256": master.sha256,
        "qc": qc.model_dump(mode="json"),
        "operator_evidence_sha256": receipt.get("operator_evidence_sha256"),
    }
    evidence = {
        "take_id": take.id,
        "autoqc_passed": True,
        "reference_verified": reference_verified,
        "temporal_verified": temporal_verified,
        "master_1080_verified": master_verified,
        "quality_evidence_sha256": hashlib.sha256(
            json.dumps(quality_payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        ).hexdigest(),
    }
    _write_json_atomic(receipt_path, promote_quality_canary(receipt, evidence))
    record_activity(
        "gpu",
        f"{take.id} · B200 cuDNN attention qualified for this Production",
        shot_id=take.shot_id,
    )


def _rebind_attention_canary(
    project: Project,
    failed_take: Take,
    retry_take: Take,
    receipt_path: Path,
) -> None:
    """Preserve operator proof while normal AutoQC replaces a failed canary Take."""
    from diony_wan.attention_qualification import rebind_failed_project_canary

    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    rebound = rebind_failed_project_canary(
        receipt,
        project_id=project.id,
        failed_take_id=failed_take.id,
        retry_take_id=retry_take.id,
    )
    _write_json_atomic(receipt_path, rebound)
    record_activity(
        "gpu",
        f"{retry_take.id} · B200 attention canary rebound after AutoQC retake",
        level="live",
        shot_id=retry_take.shot_id,
    )


def farm_generation() -> int:
    with _lock:
        return _generation


def qc_contract_valid(project: Project, take: Take) -> bool:
    """A PASS is valid only when it proves the currently selected Shot contract."""
    try:
        shot = project.shot(take.shot_id)
    except KeyError:
        return False
    qc = take.qc
    if qc is None or qc.verdict != QCVerdict.PASS or take.engine != shot.engine:
        return False
    dialogue_required = bool(shot.audio_line or shot.dialogue_turns)
    if dialogue_required and (
        take.voice_package is None
        or not take.voice_package.qc.passed
        or take.voice_package.state.value != "verified"
        or qc.voice_ready_ok is not True
        or qc.lipsync_ok is not True
        or qc.loudness_ok is not True
    ):
        return False
    measured_required = os.environ.get("DIONY_MEASURED_QC_REQUIRED", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if measured_required and (
        qc.measurement_version != MEASUREMENT_VERSION
        or qc.measured_frame_count < 3
        or qc.temporal_residual_p95 is None
        or qc.flicker_luma_p95 is None
    ):
        return False
    if shot.reference_lock != ReferenceLock.NONE:
        if qc.reference_verified is not True:
            return False
        if not set(shot.keyframe_asset_ids).issubset(qc.reference_asset_ids):
            return False
        if not _REFERENCE_CHECKPOINTS.issubset(qc.reference_similarities):
            return False
    if shot.continuity_mode == ContinuityMode.PREVIOUS_FRAME:
        try:
            index = next(i for i, item in enumerate(project.shots) if item.id == shot.id)
        except StopIteration:
            return False
        expected = project.shots[index - 1].selected_take_id if index > 0 else None
        if (
            not expected
            or qc.continuity_from_take_id != expected
            or qc.continuity_verified is not True
            or qc.continuity_similarity is None
        ):
            return False
    return True


def farm_cancelled(token: int | None = None) -> bool:
    with _lock:
        if token is None:
            return _cancel
        return _cancel or token != _generation


def reset_farm() -> None:
    """Drop in-memory farm tally so a new show does not inherit the last error."""
    global _busy, _detail, _error, _shot_id, _done, _total, _pending, _cancel
    with _lock:
        _busy = False
        _detail = ""
        _error = ""
        _shot_id = ""
        _done = 0
        _total = 0
        _pending = []
        _cancel = False


def abort_farm() -> None:
    """Stop the queue and the farm-owned generate.py. Never diony-producer."""
    global _cancel, _generation, _pending, _error, _detail, _busy, _shot_id
    with _lock:
        _generation += 1
        _cancel = True
        _pending = []
        _error = ""
        _detail = ""
        _busy = False
        _shot_id = ""
    try:
        from diony_wan.runner import abort_wan, stop_stray_generate

        abort_wan()
        stop_stray_generate()
    except ImportError:
        pass
    clear_camera_lock()

LIVE_SUFFIX = {".mp4", ".mov", ".webm", ".mkv"}
CAMERA_LOCK_REL = Path(".cluster") / "camera.json"


def camera_lock_path() -> Path:
    return vault_root() / CAMERA_LOCK_REL


def read_camera_lock() -> dict:
    path = camera_lock_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_camera_lock(payload: dict) -> None:
    path = camera_lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")


def clear_camera_lock() -> None:
    try:
        camera_lock_path().unlink(missing_ok=True)
    except OSError:
        pass


def _volume_used_bytes(path: Path) -> int | None:
    """Physical Pod-volume use; FUSE statvfs reports the provider's global pool."""
    now = time.monotonic()
    raw = str(path)
    if _VOLUME_USAGE_CACHE["path"] == raw and now - float(_VOLUME_USAGE_CACHE["at"]) < 60:
        return int(_VOLUME_USAGE_CACHE["used"])
    try:
        result = subprocess.run(
            ["du", "-s", "-B1", "--", raw],
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
        used = int((result.stdout or "").split()[0]) if result.returncode == 0 else 0
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None
    if used <= 0:
        return None
    _VOLUME_USAGE_CACHE.update({"path": raw, "at": now, "used": used})
    return used


def camera_disk_block() -> str:
    """Refuse a clip when the vault or overlay cannot hold a 720p negative."""
    try:
        vault = shutil.disk_usage(str(vault_root()))
    except OSError:
        return ""
    try:
        configured_capacity_gb = float(os.environ.get("DIONY_VOLUME_CAPACITY_GB", "0"))
        vault_floor_gb = max(2, int(os.environ.get("DIONY_VOLUME_MIN_FREE_GB", "100")))
    except ValueError:
        configured_capacity_gb = 0
        vault_floor_gb = 100
    if configured_capacity_gb > 0:
        used = _volume_used_bytes(vault_root())
        if used is not None:
            free = max(0, int(configured_capacity_gb * 1024**3) - used)
            if free < vault_floor_gb * 1024**3:
                gb = max(0, free // (1024**3))
                return (
                    f"Vault has {gb} GB free; the {vault_floor_gb} GB safety floor is required. "
                    "Models and masters share the persistent Pod volume."
                )
    elif vault.free < 2 * 1024**3:
        gb = max(0, vault.free // (1024**3))
        return f"Vault has {gb} GB free. Wan needs room for a 720p negative on /diony/vault."
    try:
        overlay = shutil.disk_usage("/")
    except OSError:
        return ""
    try:
        overlay_floor_gb = max(1, int(os.environ.get("DIONY_OVERLAY_MIN_FREE_GB", "15")))
    except ValueError:
        overlay_floor_gb = 15
    if overlay.free < overlay_floor_gb * 1024**3:
        free_gb = max(0, overlay.free // (1024**3))
        return (
            f"GPU overlay has {free_gb} GB free; the {overlay_floor_gb} GB safety floor is required. "
            "Scratch stays on the vault. Free container disk or use the 800 GB B200/B300 "
            "minimum before Wan loads another clip."
        )
    return ""


def _infrastructure_retry_seconds() -> float:
    try:
        return min(300.0, max(0.01, float(os.environ.get("DIONY_INFRA_RETRY_SECONDS", "30"))))
    except ValueError:
        return 30.0


def _wait_for_infrastructure_retry(project: Project) -> bool:
    """Wait interruptibly so Stop and pause remain responsive during a storage hold."""
    deadline = time.monotonic() + _infrastructure_retry_seconds()
    while time.monotonic() < deadline:
        if farm_cancelled() or project.production_pause is not None:
            return False
        time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))
    return not farm_cancelled() and project.production_pause is None


def resume_camera(project: Project) -> Project:
    """Restore camera ownership and resume a bounded AutoQC retake after restart."""
    if project.production_stop is not None:
        abort_farm()
        return project
    matched = project_matching_generate(project)
    if matched is not None:
        project = matched
        persist_project(project)
    if project.production_pause is not None:
        adopt_live_farm(project)
        reclaim_landed_negatives(project)
        if not list_generate_jobs():
            clear_camera_lock()
        return project
    adopt_live_farm(project)
    reclaim_landed_negatives(project)
    jobs = list_generate_jobs()
    if not jobs:
        clear_camera_lock()
        if live_camera():
            for shot in project.shots:
                if not shot.selected_take_id:
                    continue
                try:
                    selected = project.take(shot.selected_take_id)
                except KeyError:
                    continue
                retry = auto_retake_for_qc_failure(project, selected)
                if retry is not None:
                    queued = [retry]
                    queued.extend(
                        _selected_missing_takes(
                            project,
                            exclude_ids={selected.id, retry.id},
                        )
                    )
                    enter_farm()
                    start_farm(project, queued)
                    break
            else:
                queued = _selected_missing_takes(project, exclude_ids=set())
                for take in queued:
                    note = take.qc.notes.strip() if take.qc else ""
                    if any(note.startswith(prefix) for prefix in _INFRASTRUCTURE_QC_PREFIXES):
                        _clear_fail_for_reroll(take)
                recoverable = bool(
                    project.production_started_at is not None
                    and queued
                    and all(
                        take.qc is None or take.qc.verdict != QCVerdict.FAIL
                        for take in queued
                    )
                )
                if recoverable:
                    project.production_ended_at = None
                    persist_project(project)
                    enter_farm()
                    start_farm(project, queued)
    return project


def negative_rel(project: Project, take: Take) -> str:
    """One show per folder. `takes/sh-010-t1` collides across Ideas."""
    return f"projects/{project.id}/takes/{take.id}/negative_720.mp4"


def _load_generation_receipt(take: Take, negative: Path) -> GenerationExecutionReceipt:
    """Bind runner evidence only when it addresses this exact immutable output."""
    from diony_wan.runner import generation_receipt_path

    path = generation_receipt_path(negative)
    try:
        receipt = GenerationExecutionReceipt.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            "Wan generation completed without a valid execution-lane receipt."
        ) from exc
    if receipt.status != "succeeded":
        raise RuntimeError(f"Wan execution lane reported {receipt.status}: {receipt.error}")
    if not receipt.quality_contract_matched:
        raise RuntimeError(
            "Wan execution receipt does not match the pinned 720p BF16 quality contract."
        )
    actual_sha256 = _sha256_file(negative)
    actual_bytes = negative.stat().st_size
    if receipt.output_sha256 != actual_sha256 or receipt.output_bytes != actual_bytes:
        raise RuntimeError("Wan execution receipt does not match the generated negative.")
    take.generation_receipt = receipt
    return receipt


def new_generation_take(
    project: Project,
    shot: Shot,
    *,
    attempt: int | None = None,
    live: bool | None = None,
) -> Take:
    """Create one immutable generation attempt for manual or automatic retakes."""
    n = attempt or (len(project.takes_for(shot.id)) + 1)
    tid = f"{shot.id}-t{n}"
    camera_live = live_camera() if live is None else live
    rel = (
        f"projects/{project.id}/takes/{tid}/negative_720.mp4"
        if camera_live
        else f"takes/{tid}/negative_720.txt"
    )
    return Take(
        id=tid,
        shot_id=shot.id,
        attempt=n,
        engine=shot.engine,
        seed=shot.seed + (n - 1) * 19,
        asset_720p_id=f"neg-{tid}",
        version=n,
        origin=TakeOrigin.GENERATION,
        label="print" if n == 1 else f"retake {n}",
        artifacts=[
            Artifact(
                id=f"art-{tid}-720",
                take_id=tid,
                kind=ArtifactKind.NEGATIVE_720,
                path=rel,
                duration_frames=shot.duration_frames,
                width=1280,
                height=720,
            )
        ],
        qc=QCReport(
            id=f"qc-{tid}",
            take_id=tid,
            shot_id=shot.id,
            verdict=QCVerdict.WARN,
            identity_ok=True,
            morph_ok=True,
            flicker_ok=True,
            duration_ok=True,
            legal_ok=True,
            notes=(
                "Queued for Wan2.2 on this pair. 720p negative, not a 1080 master."
                if camera_live
                else "Laptop stub. No /models/wan on this machine. Dual-Spark FARM writes mp4."
            ),
        ),
    )


def _auto_retake_quality_failure(take: Take) -> bool:
    qc = take.qc
    if qc is None or qc.verdict != QCVerdict.FAIL:
        return False
    note = qc.notes.strip()
    return any(note.startswith(prefix) for prefix in _AUTO_RETAKE_QC_PREFIXES)


def auto_retake_for_qc_failure(project: Project, take: Take) -> Take | None:
    """Replace a rejected selected print until the four-generation Take cap."""
    if project.production_stop is not None or project.production_pause is not None:
        return None
    if not _auto_retake_quality_failure(take):
        return None
    shot = project.shot(take.shot_id)
    if shot.selected_take_id != take.id:
        return None
    generations = [
        item
        for item in project.takes_for(shot.id)
        if item.origin == TakeOrigin.GENERATION
    ]
    if len(generations) >= RETAKE_CAP:
        return None
    refresh_reference_shot(project, shot)
    repair_wan_prompt_for_qc(shot, take.qc.notes if take.qc else "")
    retry = new_generation_take(project, shot, live=True)
    project.takes.append(retry)
    shot.selected_take_id = retry.id
    assemble_program(project)
    project.status = ProjectStatus.SHOOTING
    project.production_ended_at = None
    persist_project(project)
    record_activity(
        "qc",
        (
            f"{shot.id} · AutoQC rejected {take.id}; retaking automatically "
            f"(attempt {retry.attempt} of {RETAKE_CAP})"
        ),
        level="live",
        shot_id=shot.id,
    )
    return retry


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _prompt_sha256(project: Project, take: Take) -> str:
    return prompt_receipt_sha256(project.shot(take.shot_id))


def _reference_sha256(project: Project, take: Take) -> str:
    shot = project.shot(take.shot_id)
    rows: list[str] = []
    for asset_id in shot.keyframe_asset_ids:
        try:
            asset = project.asset(asset_id)
        except KeyError:
            rows.append(f"{asset_id}:missing")
            continue
        rows.append(f"{asset.id}:{asset.sha256}")
    return hashlib.sha256("|".join(rows).encode("utf-8")).hexdigest() if rows else ""


def _ssim_at(negative: Path, reference: Path, *, seconds: float = 0.0) -> float | None:
    ffmpeg = resolve_media_tool("ffmpeg")
    if not ffmpeg:
        return None
    command = [
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-ss",
        f"{max(0.0, seconds):.6f}",
        "-i",
        str(negative),
        "-loop",
        "1",
        "-i",
        str(reference),
        "-filter_complex",
        r"[0:v:0]scale=1280:720,setpts=PTS-STARTPTS[a];[1:v:0]scale=1280:720,setpts=PTS-STARTPTS[b];[a][b]ssim",
        "-frames:v",
        "1",
        "-f",
        "null",
        "-",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=90, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.findall(r"All:([0-9.]+)", result.stderr or result.stdout or "")
    return round(float(match[-1]), 4) if match else None


def _reference_similarities(
    project: Project,
    take: Take,
    negative: Path,
    media: dict,
) -> dict[str, float]:
    shot = project.shot(take.shot_id)
    if not shot.keyframe_asset_ids:
        return {}
    reference = reference_frame(project, shot)
    if reference is None:
        return {}
    fps = float(media.get("fps") or shot.fps or 23.976)
    frames = int(media.get("frames") or shot.duration_frames)
    duration = max(0.0, (frames - 1) / fps) if fps else 0.0
    positions = {"first": 0.0}
    if shot.reference_lock != ReferenceLock.NONE:
        positions.update(
            {
                "quarter": duration / 4,
                "middle": duration / 2,
                "three_quarter": duration * 3 / 4,
                "last": duration,
            }
        )
    scores: dict[str, float] = {}
    for label, seconds in positions.items():
        score = _ssim_at(negative, reference, seconds=seconds)
        if score is not None:
            scores[label] = score
    return scores


def _reference_similarity(project: Project, take: Take, negative: Path) -> float | None:
    """Compatibility read: strict QC uses the minimum across sampled frames."""
    shot = project.shot(take.shot_id)
    media = probe_media(
        negative,
        fallback_fps=shot.fps,
        fallback_frames=shot.duration_frames,
        fallback_width=0,
        fallback_height=0,
    )
    scores = _reference_similarities(project, take, negative, media)
    return min(scores.values()) if scores else None


def _continuity_evidence(
    project: Project,
    take: Take,
    negative: Path,
) -> tuple[str | None, float | None]:
    shot = project.shot(take.shot_id)
    if shot.continuity_mode != ContinuityMode.PREVIOUS_FRAME:
        return None, None
    previous_frame, previous_take_id = continuity_frame(project, shot)
    if previous_frame is None or previous_take_id is None:
        return None, None
    return previous_take_id, _ssim_at(negative, previous_frame, seconds=0.0)


def _quality_ssim_floor(name: str) -> float:
    """Allow stricter site policy, never a weaker binding-reference floor."""
    try:
        configured = float(os.environ.get(name, "0.75"))
    except ValueError:
        configured = 0.75
    return max(0.75, configured)


def negative_lineage_valid(project: Project, take: Take, *, verify_hash: bool = False) -> bool:
    expected = negative_rel(project, take)
    prompt_sha = _prompt_sha256(project, take)
    reference_sha = _reference_sha256(project, take)
    for art in take.artifacts:
        if art.kind != ArtifactKind.NEGATIVE_720 or art.path != expected:
            continue
        if art.project_id != project.id or art.shot_id != take.shot_id:
            continue
        if art.prompt_sha256 != prompt_sha or art.reference_sha256 != reference_sha:
            continue
        path = vault_root() / art.path
        if not path.is_file() or path.stat().st_size <= 32:
            continue
        if verify_hash and (not art.sha256 or _sha256_file(path) != art.sha256):
            continue
        return True
    return False


def _is_wan_generate_command(args: str) -> bool:
    """Match the process Diony starts, not shells or monitors quoting its text."""
    parts = args.split()
    if len(parts) < 3:
        return False
    interpreter = Path(parts[0]).name.casefold()
    if not re.fullmatch(r"python(?:3(?:\.\d+)*)?", interpreter):
        return False
    return Path(parts[1]).name == "generate.py" and "--task" in parts[2:]


def _s2v_expected_passes(task: str, audio_path: str, infer_frames: int) -> int:
    """Mirror Wan S2V's audio-bucket repeat count without loading its model stack."""
    if task != "s2v-14B" or not audio_path or infer_frames <= 0:
        return 1
    try:
        with wave.open(audio_path, "rb") as audio:
            duration = audio.getnframes() / audio.getframerate()
    except (OSError, EOFError, wave.Error, ZeroDivisionError):
        return 1
    return max(1, int(duration * 16 / infer_frames) + 1)


def list_generate_jobs() -> list[dict]:
    """Live official generate.py --task processes. Pytest never scans the laptop."""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return []
    try:
        blob = subprocess.check_output(["ps", "-eo", "pid,etime,args"], text=True, timeout=5)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return []
    jobs: list[dict] = []
    for line in blob.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        args = parts[2]
        if not _is_wan_generate_command(args):
            continue
        save = ""
        hit = re.search(r"--save_file\s+(\S+)", args)
        if hit:
            save = hit.group(1)
        seed = 0
        seed_hit = re.search(r"--base_seed\s+(\d+)", args)
        if seed_hit:
            seed = int(seed_hit.group(1))
        take_id = ""
        take_hit = re.search(r"takes/([^/]+)/", save)
        if take_hit:
            take_id = take_hit.group(1)
        project_id = ""
        proj_hit = re.search(r"projects/([^/]+)/takes/", save)
        if proj_hit:
            project_id = proj_hit.group(1)
        task_hit = re.search(r"--task\s+(\S+)", args)
        task = task_hit.group(1) if task_hit else ""
        audio_hit = re.search(r"--audio\s+(\S+)", args)
        audio_path = audio_hit.group(1) if audio_hit else ""
        frames_hit = re.search(r"--infer_frames\s+(\d+)", args)
        infer_frames = int(frames_hit.group(1)) if frames_hit else 0
        jobs.append(
            {
                "pid": pid,
                "etime": parts[1],
                "save": save,
                "seed": seed,
                "take_id": take_id,
                "project_id": project_id,
                "task": task,
                "expected_passes": _s2v_expected_passes(task, audio_path, infer_frames),
                "args": args,
            }
        )
    return jobs


def camera_rolling() -> bool:
    """True when this farm is busy or a generate.py --task is already on the GPU."""
    with _lock:
        if _busy:
            return True
    return bool(list_generate_jobs())


def _progress_for_save(save: str, *, expected_passes: int = 1) -> dict:
    if not save:
        return {}
    log = Path(save).with_suffix(".wan.log")
    if not log.is_file():
        return {}
    try:
        from diony_wan.runner import parse_wan_progress

        return parse_wan_progress(
            log.read_text(errors="replace")[-65_536:],
            expected_passes=expected_passes,
        )
    except (OSError, ImportError):
        return {}


def take_for_job(project: Project, job: dict) -> Take | None:
    take_id = str(job.get("take_id") or "")
    seed = int(job.get("seed") or 0)
    for take in project.takes:
        if take_id and take.id == take_id:
            if seed and take.seed != seed:
                continue
            return take
        if seed and take.seed == seed:
            return take
    return None


def project_matching_generate(current: Project) -> Project | None:
    """If generate.py belongs to another vault show, return that project."""
    jobs = list_generate_jobs()
    if not jobs:
        return None
    job = jobs[0]
    if take_for_job(current, job):
        return None
    root = vault_root() / "projects"
    if not root.is_dir():
        return None
    for path in root.glob("*/project.json"):
        try:
            other = Project.model_validate_json(path.read_text())
        except (OSError, ValueError):
            continue
        if take_for_job(other, job):
            return other
    return None


def generate_process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def generate_output_finalized(pid: int, destination: Path) -> bool:
    """A growing MP4 is not landed media; its container metadata may be unreadable."""
    return (
        not generate_process_alive(pid)
        and destination.is_file()
        and destination.stat().st_size > 32
    )


def reclaim_landed_negatives(project: Project) -> int:
    """Attach 720p files that landed after Producer died."""
    n = 0
    inflight = {
        Path(str(job.get("save") or ""))
        for job in list_generate_jobs()
        if str(job.get("save") or "").strip()
    }
    for take in project.takes:
        if take_has_live_negative(take, project) and take.qc and take.qc.verdict == QCVerdict.PASS:
            continue
        shot = project.shot(take.shot_id)
        rel = negative_rel(project, take)
        dest = vault_root() / rel
        if dest in inflight:
            continue
        if dest.is_file() and dest.stat().st_size > 32:
            attach_negative(project, take, rel, shot.duration_frames, live=True)
            n += 1
    if n:
        persist_project(project)
    return n


def adopt_live_farm(project: Project) -> None:
    """Watch a generate.py that outlived Producer. Then farm the rest of the rundown."""
    global _busy, _detail, _shot_id, _done, _total, _error
    if project.production_stop is not None:
        return
    jobs = list_generate_jobs()
    if not jobs:
        return
    job = jobs[0]
    take = take_for_job(project, job)
    if take is None:
        return
    with _lock:
        if _busy:
            return
        _busy = True
        _error = ""
        _shot_id = take.shot_id
        _done = 0
        _total = max(len(project.shots), 1)
        _detail = f"Generating 720p · {take.shot_id}"
    record_activity("wan", _detail, level="live", shot_id=take.shot_id)

    def watch() -> None:
        global _busy, _detail, _shot_id, _done, _error
        pid = int(job["pid"])
        dest = Path(str(job.get("save") or ""))
        rel = str(dest.relative_to(vault_root())) if dest.is_absolute() and vault_root() in dest.parents else negative_rel(project, take)
        retry: Take | None = None
        while True:
            if farm_cancelled() or project.production_stop is not None:
                return
            progress = _progress_for_save(
                str(dest), expected_passes=int(job.get("expected_passes") or 1)
            )
            if progress:
                from diony_wan.runner import progress_label

                _detail = f"{progress_label(progress)} · {take.shot_id}"
            if not generate_process_alive(pid):
                break
            time.sleep(2)
        # The process has exited, but the vault may still be flushing the final
        # moov atom. Require two identical size observations before probing.
        previous_size = -1
        stable = False
        for _ in range(30):
            size = dest.stat().st_size if dest.is_file() else 0
            if size > 32 and size == previous_size:
                stable = True
                break
            previous_size = size
            time.sleep(1)
        if farm_cancelled() or project.production_stop is not None:
            return
        if stable:
            attach_negative(project, take, rel, project.shot(take.shot_id).duration_frames, live=True)
            persist_project(project)
            integrity_error = _take_integrity_error(take)
            if integrity_error:
                _error = integrity_error[:400]
                _detail = _error[:240]
                record_activity("qc", f"{take.shot_id} · {_detail}", level="fail", shot_id=take.shot_id)
                retry = auto_retake_for_qc_failure(project, take)
                if retry is not None:
                    _error = ""
                    _detail = (
                        f"AutoQC retake {retry.attempt} of {RETAKE_CAP} · "
                        f"{retry.shot_id}"
                    )
            else:
                _error = ""
                record_activity("wan", f"{take.shot_id} · 720p negative landed", shot_id=take.shot_id)
        else:
            from diony_wan.runner import classify_wan_log

            blob = ""
            log = dest.with_suffix(".wan.log") if dest else Path()
            if log.is_file():
                try:
                    blob = log.read_text(errors="replace")[-8000:]
                except OSError:
                    blob = ""
            msg = classify_wan_log(blob, 1)[:400]
            _error = msg
            attach_negative(project, take, rel, project.shot(take.shot_id).duration_frames, live=True, err=msg)
            persist_project(project)
            record_activity("qc", f"{take.shot_id} · {msg[:240]}", level="fail", shot_id=take.shot_id)
        leftover: list[Take] = []
        with _lock:
            if retry is None:
                _done += 1
            _pending.clear()
            if not _error:
                exclude = {take.id}
                if retry is not None:
                    leftover.append(retry)
                    exclude.add(retry.id)
                leftover.extend(_selected_missing_takes(project, exclude_ids=exclude))
            _busy = False
            _shot_id = ""
        if leftover:
            start_farm(project, leftover)
        else:
            from diony_producer.advance import maybe_advance_run

            maybe_advance_run(project)

    threading.Thread(target=watch, name="diony-adopt", daemon=True).start()


def _selected_missing_takes(
    project: Project,
    *,
    exclude_ids: set[str],
    candidates: list[Take] | None = None,
) -> list[Take]:
    """Resume only the current cut after a Producer restart.

    Older aborted or rejected Take rows remain in the Project for audit. They
    are never work queues merely because their negative is absent.
    """
    selected_ids = {
        shot.selected_take_id
        for shot in project.shots
        if shot.selected_take_id
    }
    return [
        item
        for item in (project.takes if candidates is None else candidates)
        if item.id in selected_ids
        and item.id not in exclude_ids
        and not take_has_live_negative(item, project)
    ]


def take_has_live_negative(take: Take, project: Project | None = None) -> bool:
    if project is not None:
        return negative_lineage_valid(project, take)
    for art in take.artifacts:
        if art.kind != ArtifactKind.NEGATIVE_720:
            continue
        dest = vault_root() / art.path
        if dest.suffix.lower() in LIVE_SUFFIX and dest.is_file() and dest.stat().st_size > 32:
            return True
        if dest.suffix.lower() in LIVE_SUFFIX and art.bytes > 32:
            return True
    return False


def farm_status() -> dict:
    site = site_status()
    with _lock:
        busy = _busy
        detail = _detail
        error = _error
        shot_id = _shot_id
        done = _done
        total = _total
        queued = len(_pending)
    jobs = list_generate_jobs()
    progress: dict = {}
    if jobs:
        job = jobs[0]
        progress = _progress_for_save(
            str(job.get("save") or ""),
            expected_passes=int(job.get("expected_passes") or 1),
        )
        if not busy:
            busy = True
            take_id = str(job.get("take_id") or "")
            shot_id = take_id.rsplit("-t", 1)[0] if take_id else shot_id
            total = max(total, 1)
        if progress:
            try:
                from diony_wan.runner import progress_label

                label = progress_label(progress)
            except ImportError:
                label = ""
            if label:
                detail = f"{label} · {shot_id}" if shot_id else label
    lock = read_camera_lock()
    return {
        "busy": busy,
        "detail": detail,
        "error": error,
        "shot_id": shot_id,
        "done": done,
        "total": total,
        "queued": queued,
        "progress": progress,
        "camera": lock,
        "live": inference_live() and live_camera(),
        "sequential": site["sequential"],
        "negative": "720p Wan",
        "master": "1080 Diony finish",
    }


def enter_farm() -> str | None:
    """Dual-Spark/B300: drain brain if a command is set, then lock FARM/wan."""
    site = site_status()
    warning = None
    if site["sequential"]:
        held = refuse_concurrent("FARM", "wan")
        drain = os.environ.get("DIONY_DRAIN_BRAIN", "").strip()
        if drain:
            subprocess.run(drain, shell=True, timeout=180, check=False)
        elif held:
            warning = held
    write_mode_lock("FARM", "wan", reason="producer farm")
    return warning


def _clear_fail_for_reroll(take: Take) -> None:
    """A 0-byte tqdm 'fail' is not a clip QC fail. Re-queue as queued."""
    if take.qc and take.qc.verdict == QCVerdict.FAIL:
        take.qc.verdict = QCVerdict.WARN
        take.qc.notes = "Queued for Wan2.2 on this pair. 720p negative, not a 1080 master."


def _voice_performance_failure(voice_package) -> str:
    """Describe the measured voice gate that failed instead of hiding it."""
    if voice_package is None:
        return "Voice performance failed: audiovisual timing evidence is missing."
    qc = voice_package.qc
    failures: list[str] = []
    if qc.lip_sync_confidence < qc.lip_sync_confidence_min:
        failures.append(
            f"lip-sync confidence {qc.lip_sync_confidence:.4f} is below "
            f"{qc.lip_sync_confidence_min:.2f}"
        )
    if qc.audiovisual_offset_ms_p95 > qc.audiovisual_offset_ms_max:
        failures.append(
            f"audiovisual offset p95 {qc.audiovisual_offset_ms_p95:.3f} ms exceeds "
            f"{qc.audiovisual_offset_ms_max:g} ms"
        )
    if qc.performance_consistency < qc.performance_consistency_min:
        failures.append(
            f"performance consistency {qc.performance_consistency:.4f} is below "
            f"{qc.performance_consistency_min:.2f}"
        )
    detail = "; ".join(failures) or "audiovisual timing evidence did not pass"
    return f"Voice performance failed: {detail}."


def attach_negative(
    project: Project,
    take: Take,
    dest_rel: str,
    shot_frames: int,
    *,
    live: bool,
    err: str = "",
    measured_qc_required: bool | None = None,
) -> None:
    dest = vault_root() / dest_rel
    size = dest.stat().st_size if dest.is_file() else 0
    addressed = take.generation_receipt
    digest = (
        addressed.output_sha256
        if addressed is not None and addressed.output_bytes == size
        else _sha256_file(dest)
        if dest.is_file()
        else ""
    )
    shot = project.shot(take.shot_id)
    media = probe_media(
        dest,
        fallback_fps=shot.fps,
        fallback_frames=shot.duration_frames,
        fallback_width=0,
        fallback_height=0,
    ) if dest.is_file() else {
        "width": 0,
        "height": 0,
        "fps": shot.fps,
        "frames": 0,
    }
    dimensions_ok = int(media.get("width") or 0) == 1280 and int(media.get("height") or 0) == 720
    temporal = (
        measure_negative_quality(dest)
        if not err and dest.is_file()
        else None
    )
    if measured_qc_required is None:
        measured_qc_required = os.environ.get("DIONY_MEASURED_QC_REQUIRED", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
    morph_ok = bool(temporal and temporal.measured and temporal.morph_ok) if measured_qc_required else True
    flicker_ok = (
        bool(temporal and temporal.measured and temporal.flicker_ok)
        if measured_qc_required
        else True
    )
    motion_ok = (
        bool(temporal and temporal.measured and temporal.motion_ok)
        if measured_qc_required
        else True
    )
    camera_ok = morph_ok
    lighting_ok = flicker_ok
    dialogue_required = bool(shot.audio_line or shot.dialogue_turns)
    voice_failure = ""
    voice_package = take.voice_package
    if dialogue_required and not err and dest.is_file():
        try:
            voice_package = verify_voice_animation(project, take, dest)
        except Exception as exc:  # noqa: BLE001 — missing AV evidence is a hard clip gate
            voice_failure = f"Voice performance failed: {exc}"
    voice_ready_ok = (
        bool(voice_package and voice_package.qc.passed)
        if dialogue_required
        else None
    )
    audio_ready_ok = (
        bool(shot.audio_asset_ids and voice_package and voice_package.qc.audio_passed)
        if dialogue_required
        else shot.engine != Engine.S2V_14B or bool(shot.audio_asset_ids)
    )
    lipsync_ok = bool(voice_package and voice_package.qc.animation_passed) if dialogue_required else None
    loudness_ok = bool(voice_package and voice_package.qc.audio_passed) if dialogue_required else None
    # Camera negatives are legalized by Finish. At this boundary `legal_ok`
    # means the native media contract was measured and is structurally valid;
    # final Rec.709 tags/range are verified after the mandatory 1080 pass.
    legal_ok = (
        dimensions_ok
        and int(media.get("frames") or 0) > 0
        and str(media.get("codec") or "unknown") != "unknown"
        if measured_qc_required
        else True
    )
    similarities = (
        {} if err or not dest.is_file() else _reference_similarities(project, take, dest, media)
    )
    if not similarities and not err and dest.is_file():
        compatibility_score = _reference_similarity(project, take, dest)
        if compatibility_score is not None:
            similarities = {"first": compatibility_score}
    similarity = min(similarities.values()) if similarities else None
    reference_ids = list(shot.keyframe_asset_ids)
    # Background-dominant SSIM can otherwise let an unrelated subject pass. A
    # binding character or world reference must retain at least 0.75 at every
    # sampled checkpoint; camera motion is carried by previous-frame handoff.
    threshold = _quality_ssim_floor("DIONY_REFERENCE_SSIM_MIN")
    reference_verified = not reference_ids or (similarity is not None and similarity >= threshold)
    continuity_take_id, continuity_similarity = (
        (None, None) if err or not dest.is_file() else _continuity_evidence(project, take, dest)
    )
    continuity_required = shot.continuity_mode == ContinuityMode.PREVIOUS_FRAME
    continuity_threshold = _quality_ssim_floor("DIONY_CONTINUITY_SSIM_MIN")
    continuity_verified = not continuity_required or (
        continuity_take_id is not None
        and continuity_similarity is not None
        and continuity_similarity >= continuity_threshold
    )
    try:
        task = camera_task_for(project, shot)
    except RuntimeError:
        task = shot.engine.value
    take.artifacts = [
        Artifact(
            id=f"art-{take.id}-720",
            take_id=take.id,
            kind=ArtifactKind.NEGATIVE_720,
            path=dest_rel,
            project_id=project.id,
            shot_id=take.shot_id,
            bytes=size,
            sha256=digest,
            prompt_sha256=_prompt_sha256(project, take),
            reference_asset_ids=reference_ids,
            reference_sha256=_reference_sha256(project, take),
            continuity_from_take_id=continuity_take_id,
            continuity_sha256=(
                hashlib.sha256(f"{continuity_take_id}:{continuity_similarity}".encode("utf-8")).hexdigest()
                if continuity_take_id and continuity_similarity is not None
                else ""
            ),
            generation_task=task,
            width=int(media.get("width") or 0),
            height=int(media.get("height") or 0),
            fps=float(media.get("fps") or shot.fps),
            duration_frames=int(media.get("frames") or shot_frames),
        )
    ]
    ok = (
        live
        and size > 32
        and not err
        and dimensions_ok
        and reference_verified
        and continuity_verified
        and morph_ok
        and flicker_ok
        and motion_ok
        and camera_ok
        and lighting_ok
        and audio_ready_ok
        and voice_ready_ok is not False
        and legal_ok
    )
    production_clean_ok = bool(
        dimensions_ok
        and reference_verified
        and continuity_verified
        and morph_ok
        and flicker_ok
        and legal_ok
    )
    failure = err or voice_failure
    if not failure and dialogue_required and not voice_ready_ok:
        if voice_package and voice_package.qc.speaker_similarity < voice_package.qc.speaker_similarity_min:
            failure = (
                "Voice identity failed: speaker similarity "
                f"{voice_package.qc.speaker_similarity:.4f} is below "
                f"{voice_package.qc.speaker_similarity_min:.2f}."
            )
        elif voice_package and not voice_package.qc.audio_passed:
            failure = "Voice audio failed locked studio-audio QC."
        else:
            failure = _voice_performance_failure(voice_package)
    if not failure and size > 32 and not dimensions_ok:
        width = int(media.get("width") or 0)
        height = int(media.get("height") or 0)
        failure = (
            "Wan negative dimensions could not be measured; install FFmpeg or FFprobe."
            if not width or not height
            else f"Wan negative is {width}×{height}; Diony requires native 1280×720."
        )
    if not failure and not reference_verified:
        measured = "missing" if similarity is None else f"{similarity:.4f}"
        failure = (
            f"Reference lock failed across the clip (minimum sampled SSIM {measured}; "
            f"required {threshold:.2f})."
        )
    if not failure and not continuity_verified:
        measured = "missing" if continuity_similarity is None else f"{continuity_similarity:.4f}"
        failure = (
            f"Shot boundary continuity failed (first/last SSIM {measured}; "
            f"required {continuity_threshold:.2f})."
        )
    if not failure and measured_qc_required and (temporal is None or not temporal.measured):
        failure = "Measured temporal QC is unavailable; the camera negative cannot pass blindly."
    if not failure and not motion_ok:
        measured = temporal.temporal_delta_p95 if temporal else 0.0
        failure = f"Measured motion failed (delta p95 {measured:.4f}); the plate is effectively frozen."
    if not failure and not morph_ok:
        measured = temporal.temporal_residual_p95 if temporal else 0.0
        failure = f"Measured temporal coherence failed (residual p95 {measured:.4f})."
    if not failure and not flicker_ok:
        measured = temporal.flicker_luma_p95 if temporal else 0.0
        failure = f"Measured flicker failed (luma-change p95 {measured:.4f})."
    if not failure and not legal_ok:
        failure = "Measured 720p media integrity failed before the mandatory Rec.709 finish."
    take.qc = QCReport(
        id=f"qc-{take.id}",
        take_id=take.id,
        shot_id=take.shot_id,
        verdict=QCVerdict.PASS if ok else (QCVerdict.FAIL if failure else QCVerdict.WARN),
        identity_ok=reference_verified,
        morph_ok=morph_ok,
        flicker_ok=flicker_ok,
        duration_ok=bool(media.get("frames")) if live else True,
        legal_ok=legal_ok,
        motion_ok=motion_ok,
        camera_ok=camera_ok,
        lighting_ok=lighting_ok,
        production_clean_ok=production_clean_ok,
        audio_ready_ok=audio_ready_ok,
        voice_ready_ok=voice_ready_ok,
        lipsync_ok=lipsync_ok,
        loudness_ok=loudness_ok,
        voice_measurement_version=(voice_package.qc.measurement_version if voice_package else ""),
        speaker_similarity=(voice_package.qc.speaker_similarity if voice_package else None),
        voice_project_consistency=(voice_package.qc.project_consistency if voice_package else None),
        voice_performance_consistency=(
            voice_package.qc.performance_consistency if voice_package else None
        ),
        lip_sync_confidence=(voice_package.qc.lip_sync_confidence if voice_package else None),
        audiovisual_offset_ms_p95=(
            voice_package.qc.audiovisual_offset_ms_p95 if voice_package else None
        ),
        reference_asset_ids=reference_ids,
        reference_similarity=similarity,
        reference_similarities=similarities,
        reference_verified=reference_verified,
        continuity_from_take_id=continuity_take_id,
        continuity_similarity=continuity_similarity,
        continuity_verified=continuity_verified,
        measurement_version=MEASUREMENT_VERSION if temporal and temporal.measured else "",
        temporal_delta_p95=(temporal.temporal_delta_p95 if temporal and temporal.measured else None),
        temporal_residual_p95=(
            temporal.temporal_residual_p95 if temporal and temporal.measured else None
        ),
        flicker_luma_p95=(temporal.flicker_luma_p95 if temporal and temporal.measured else None),
        measured_frame_count=(temporal.frame_count if temporal and temporal.measured else 0),
        notes=(
            failure
            or (
                "Wan2.2 720p negative. Multi-frame reference and boundary checks passed. Not a 1080 master."
                if ok
                else "Queued for Wan2.2 on this pair."
            )
        ),
    )


def _take_integrity_error(take: Take) -> str:
    if take.qc and take.qc.verdict == QCVerdict.FAIL:
        return take.qc.notes.strip() or "Generated media failed Diony integrity checks."
    return ""


def run_one(
    project: Project,
    take: Take,
    *,
    attention_backend: str | None = None,
    measured_qc_required: bool | None = None,
) -> None:
    global _detail, _shot_id, _error
    token = farm_generation()
    started = time.monotonic()
    shot = project.shot(take.shot_id)
    refresh_reference_shot(project, shot)
    prepare_voice_for_take(project, take)
    persist_project(project)
    _shot_id = shot.id
    task = camera_task_for(project, shot)
    _detail = f"Generating 720p · {task} · {shot.id}"
    blocked = camera_disk_block()
    if blocked:
        raise CameraResourceBlocked(blocked)
    _clear_fail_for_reroll(take)
    record_activity("wan", _detail, level="live", shot_id=shot.id)
    rel = negative_rel(project, take)
    dest = vault_root() / rel
    write_camera_lock(
        {
            "project_id": project.id,
            "take_id": take.id,
            "shot_id": shot.id,
            "save": str(dest),
            "task": task,
            "at": datetime.now(timezone.utc).isoformat(),
        }
    )
    try:
        generate_clip(
            project,
            shot,
            dest,
            take.seed,
            attention_backend=attention_backend,
        )
        if farm_cancelled(token):
            return
        if dest.is_file():
            _load_generation_receipt(take, dest)
        attach_negative(
            project,
            take,
            rel,
            shot.duration_frames,
            live=True,
            measured_qc_required=measured_qc_required,
        )
        persist_project(project)
        integrity_error = _take_integrity_error(take)
        if integrity_error:
            _error = integrity_error[:400]
            _detail = _error[:240]
            record_activity("qc", f"{shot.id} · {_detail}", level="fail", shot_id=shot.id)
        else:
            _error = ""
            lane = (
                take.generation_receipt.selected_lane
                if take.generation_receipt is not None
                else "unreported lane"
            )
            record_activity(
                "wan",
                f"{shot.id} · 720p negative landed · {lane}",
                shot_id=shot.id,
            )
    except Exception as exc:  # noqa: BLE001 — farm must record QC fail
        if farm_cancelled(token):
            return
        from diony_wan.runner import classify_wan_log

        msg = classify_wan_log(str(exc), 1)[:400]
        _error = msg
        _detail = msg[:240]
        attach_negative(
            project,
            take,
            rel,
            shot.duration_frames,
            live=True,
            err=msg,
            measured_qc_required=measured_qc_required,
        )
        persist_project(project)
        record_activity("qc", f"{shot.id} · {_detail}", level="fail", shot_id=shot.id)
    finally:
        if not list_generate_jobs():
            clear_camera_lock()
        cancelled = farm_cancelled(token)
        outcome = (
            "cancelled"
            if cancelled
            else "succeeded"
            if take.qc is not None and take.qc.verdict == QCVerdict.PASS
            else "failed"
        )
        _PERFORMANCE.record(
            stage="wan_camera",
            project_id=project.id,
            take_id=take.id,
            duration_seconds=time.monotonic() - started,
            outcome=outcome,
            retry=take.attempt > 1,
            metadata={
                "task": task,
                "measured_qc": bool(take.qc and take.qc.measurement_version),
                "generation_lane": (
                    take.generation_receipt.selected_lane
                    if take.generation_receipt is not None
                    else "unreported"
                ),
                "quality_contract_matched": bool(
                    take.generation_receipt
                    and take.generation_receipt.quality_contract_matched
                ),
            },
        )


def start_farm(project: Project, takes: list[Take]) -> None:
    """Queue clips. Mark busy on this thread so Produce poll does not miss FARM.

    prove_generate loads torch. Do it on the farm thread so Produce can open
    Production before generate.py --help returns.
    """
    global _busy, _detail, _done, _total, _error, _cancel
    if project.production_stop is not None or project.production_pause is not None:
        return
    if list_generate_jobs() and not _busy:
        adopt_live_farm(project)
        inflight = {str(job.get("take_id") or "") for job in list_generate_jobs()}
        takes = [item for item in takes if item.id not in inflight]
        if not takes:
            return
    spawn = False
    with _lock:
        if not _busy:
            _error = ""
            _cancel = False
            if _total == 0:
                _done = 0
        _pending.extend(takes)
        _total = max(_total, _done + len(_pending))
        if not _busy:
            _busy = True
            _detail = f"Farming {len(_pending)} Wan2.2 720p clips"
            spawn = True
    if not spawn:
        return
    record_activity("wan", _detail, level="live")

    def loop() -> None:
        global _busy, _detail, _shot_id, _done, _error
        storage_hold = ""
        try:
            from diony_wan.runner import prove_generate

            prove_generate()
        except Exception as exc:  # noqa: BLE001 — one import miss, not one per shot
            from diony_wan.runner import classify_wan_log

            msg = classify_wan_log(str(exc), 1)[:400]
            leftover: list[Take] = []
            with _lock:
                leftover = list(_pending)
                _pending.clear()
                _error = msg
                _detail = msg.splitlines()[-1][:240] if msg.strip() else "Wan2.2 failed"
                _done = _total
                _busy = False
                _shot_id = ""
            if farm_cancelled():
                return
            for take in leftover:
                rel = negative_rel(project, take)
                attach_negative(
                    project,
                    take,
                    rel,
                    project.shot(take.shot_id).duration_frames,
                    live=True,
                    err=msg,
                )
            persist_project(project)
            record_activity("qc", msg.splitlines()[-1][:240] if msg else "Wan2.2 failed", level="fail")
            return
        try:
            while True:
                with _lock:
                    if _cancel or project.production_pause is not None or not _pending:
                        if project.production_pause is not None:
                            _pending.clear()
                        break
                    next_shot_id = _pending[0].shot_id
                blocked = camera_disk_block()
                if blocked:
                    storage_hold = blocked
                    with _lock:
                        _error = ""
                        _shot_id = next_shot_id
                        _detail = f"Storage hold · {blocked} Retrying automatically."
                    record_activity(
                        "wan",
                        f"{_shot_id or 'Camera'} · storage hold; retrying automatically · {blocked}",
                        level="info",
                        shot_id=_shot_id,
                    )
                    if not _wait_for_infrastructure_retry(project):
                        break
                    continue
                if storage_hold:
                    record_activity(
                        "wan",
                        f"{_shot_id or 'Camera'} · GPU storage recovered; resuming Wan2.2 automatically",
                        level="live",
                        shot_id=_shot_id,
                    )
                    storage_hold = ""
                with _lock:
                    if _cancel or project.production_pause is not None or not _pending:
                        if project.production_pause is not None:
                            _pending.clear()
                        break
                    take = _pending.pop(0)
                if farm_cancelled():
                    break
                active_task = ""
                active_checkpoint: Path | None = None
                try:
                    from diony_contracts.live import ckpt_dir
                    from diony_wan.storage import prepare_checkpoint, prefetch_checkpoint

                    active_task = camera_task_for(project, project.shot(take.shot_id))
                    _detail = f"Staging {active_task} on the GPU hot tier · {take.shot_id}"
                    source_checkpoint = ckpt_dir(active_task)
                    active_checkpoint = prepare_checkpoint(active_task, source=source_checkpoint)
                    if active_checkpoint != source_checkpoint:
                        record_activity(
                            "wan",
                            f"{take.shot_id} · {active_task} verified in bounded overlay hot cache",
                            level="live",
                            shot_id=take.shot_id,
                        )
                    with _lock:
                        upcoming = _pending[0] if _pending else None
                    if upcoming is not None:
                        upcoming_task = camera_task_for(
                            project,
                            project.shot(upcoming.shot_id),
                        )
                        if upcoming_task != active_task:
                            prefetch_checkpoint(upcoming_task, source=ckpt_dir(upcoming_task))
                except (ImportError, OSError, RuntimeError, ValueError):
                    # The persistent checkpoint remains the exact fallback. A
                    # speculative hot-tier miss must never consume the queue.
                    pass
                attention_backend, attention_canary, attention_receipt = _attention_plan(
                    project,
                    take,
                )
                rel = negative_rel(project, take)
                existing = vault_root() / rel
                if existing.is_file() and existing.stat().st_size > 32:
                    attach_negative(
                        project,
                        take,
                        rel,
                        project.shot(take.shot_id).duration_frames,
                        live=True,
                    )
                    if (
                        negative_lineage_valid(project, take)
                        and take.qc
                        and take.qc.verdict == QCVerdict.PASS
                        and not attention_canary
                    ):
                        persist_project(project)
                        with _lock:
                            _done += 1
                        continue
                    existing.unlink(missing_ok=True)
                if active_task and active_checkpoint is not None:
                    from diony_wan.runner import prewarm_validated_resident

                    if prewarm_validated_resident(
                        task=active_task,
                        checkpoint=active_checkpoint,
                        identifier=str(existing),
                        attention_backend=attention_backend,
                    ):
                        record_activity(
                            "wan",
                            f"{take.shot_id} · validated resident BF16 pipeline prewarmed",
                            level="live",
                            shot_id=take.shot_id,
                        )
                try:
                    run_one(
                        project,
                        take,
                        attention_backend=attention_backend,
                        measured_qc_required=True if attention_canary else None,
                    )
                except CameraResourceBlocked as exc:
                    blocked = str(exc)
                    storage_hold = blocked
                    with _lock:
                        _pending.insert(0, take)
                        _error = ""
                        _detail = f"Storage hold · {blocked} Retrying automatically."
                    record_activity(
                        "wan",
                        f"{take.shot_id} · storage hold; retrying automatically · {blocked}",
                        level="info",
                        shot_id=take.shot_id,
                    )
                    if not _wait_for_infrastructure_retry(project):
                        break
                    continue
                except Exception as exc:  # noqa: BLE001 — never consume the rundown silently
                    from diony_wan.runner import classify_wan_log

                    message = classify_wan_log(str(exc), 1)[:400]
                    with _lock:
                        _pending.insert(0, take)
                        _error = message
                        _detail = message.splitlines()[-1][:240]
                    record_activity(
                        "qc",
                        f"{take.shot_id} · {_detail}",
                        level="fail",
                        shot_id=take.shot_id,
                    )
                    break
                if farm_cancelled():
                    break
                integrity_error = _take_integrity_error(take)
                if attention_canary:
                    if integrity_error:
                        retry = auto_retake_for_qc_failure(project, take)
                        if retry is not None and attention_receipt is not None:
                            try:
                                _rebind_attention_canary(
                                    project,
                                    take,
                                    retry,
                                    attention_receipt,
                                )
                            except Exception as exc:  # noqa: BLE001 — receipt binding is a hard gate
                                with _lock:
                                    _error = (
                                        "B200 attention canary could not bind its AutoQC retake · "
                                        f"{exc}"
                                    )[:400]
                                    _detail = _error[:240]
                                    _pending.clear()
                                record_activity(
                                    "qc",
                                    _detail,
                                    level="fail",
                                    shot_id=take.shot_id,
                                )
                                break
                            with _lock:
                                _pending.insert(0, retry)
                                _error = ""
                                _detail = (
                                    f"B200 canary AutoQC retake {retry.attempt} of "
                                    f"{RETAKE_CAP} · {retry.shot_id}"
                                )
                            continue
                        with _lock:
                            _done += 1
                            _error = (
                                "B200 attention canary stopped before activation · "
                                f"{integrity_error}"
                            )[:400]
                            _detail = _error[:240]
                            _pending.clear()
                        record_activity(
                            "qc",
                            _detail,
                            level="fail",
                            shot_id=take.shot_id,
                        )
                        break
                    try:
                        if attention_receipt is None:
                            raise RuntimeError("B200 attention receipt disappeared before promotion.")
                        _detail = f"Qualifying {take.id} · measured QC + 1080 finish"
                        _complete_attention_canary(project, take, attention_receipt)
                    except Exception as exc:  # noqa: BLE001 — qualification is a hard gate
                        with _lock:
                            _done += 1
                            _error = f"B200 attention canary stopped before activation · {exc}"[:400]
                            _detail = _error[:240]
                            _pending.clear()
                        record_activity(
                            "qc",
                            _detail,
                            level="fail",
                            shot_id=take.shot_id,
                        )
                        break
                    with _lock:
                        _done += 1
                        _error = ""
                        _detail = f"B200 acceleration active · {take.id} verified"
                    continue
                retry = auto_retake_for_qc_failure(project, take)
                with _lock:
                    if retry is not None:
                        _pending.insert(0, retry)
                        _error = ""
                        _detail = (
                            f"AutoQC retake {retry.attempt} of {RETAKE_CAP} · "
                            f"{retry.shot_id}"
                        )
                    else:
                        _done += 1
                    if integrity_error and retry is None:
                        _error = integrity_error[:400]
                        _detail = _error[:240]
                        _pending.clear()
                        break
        finally:
            again = False
            failed = False
            cancelled = farm_cancelled()
            with _lock:
                if _pending and not _cancel and not _error:
                    again = True
                else:
                    _busy = False
                    _shot_id = ""
                    if _cancel:
                        _detail = ""
                    elif _error:
                        failed = True
                        _detail = _error.splitlines()[-1][:240]
                    else:
                        _detail = "Farm idle"
            if again:
                threading.Thread(target=loop, name="diony-farm", daemon=True).start()
            elif not cancelled:
                from diony_producer.advance import maybe_advance_run

                if failed:
                    project.mark_production_end()
                    persist_project(project)
                maybe_advance_run(project)

    threading.Thread(target=loop, name="diony-farm", daemon=True).start()


def request_pause_after_current(
    project: Project,
    *,
    reason: str,
    after_take_id: str,
) -> ProductionPauseReceipt:
    """Let the active Take land, then durably refuse every later dispatch."""
    clean_reason = reason.strip()
    active_take_id = after_take_id.strip()
    selected_ids = [
        shot.selected_take_id
        for shot in project.shots
        if shot.selected_take_id
    ]
    if active_take_id not in selected_ids:
        raise ValueError("Pause boundary must be a selected Take on this Project.")
    remaining = [
        take_id
        for take_id in selected_ids
        if take_id != active_take_id
        and not take_has_live_negative(project.take(take_id), project)
    ]
    receipt = ProductionPauseReceipt(
        reason=clean_reason,
        after_take_id=active_take_id,
        remaining_take_ids=remaining,
    )
    project.production_pause = receipt
    persist_project(project)
    record_activity(
        "run",
        f"Holding after {active_take_id} · {len(remaining)} selected Takes remain",
        level="info",
        shot_id=project.take(active_take_id).shot_id,
    )
    return receipt


def clear_production_pause(project: Project) -> None:
    project.production_pause = None
    project.production_ended_at = None
    persist_project(project)
