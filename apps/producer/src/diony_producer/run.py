"""Production floor: process status, not a shot picker."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from diony_contracts.cluster import RETAKE_CAP, site_status
from diony_contracts.enums import ArtifactKind, GateName, QCVerdict
from diony_contracts.models import Project, Take
from diony_contracts.studio import allotment_label
from diony_producer.camera import live_camera
from diony_producer.farm import farm_status, take_has_live_negative
from diony_producer.finish import finish_status, live_finish, selected_masters_ready
from diony_producer.gpu import (
    connect_from_lock,
    gpu_block_copy,
    gpu_monitor,
    last_gpu_error,
    signed_gpu_site,
    wizard_pack_on_gpu,
)
from diony_producer.mix import mix_evidence_blockers, mix_status


def format_elapsed(seconds: int) -> str:
    """Stopwatch: 3:07 or 1:02:04."""
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def production_clock(
    project: Project,
    *,
    rolling: bool,
    complete: bool,
) -> dict:
    """Elapsed while the run is live. Total after mix lock."""
    started = _aware(project.production_started_at)
    if started is None:
        stamps = [_aware(take.created_at) for take in project.takes if take.created_at]
        stamps = [item for item in stamps if item is not None]
        if stamps:
            started = min(stamps)
    ended = _aware(project.production_ended_at)
    now = datetime.now(timezone.utc)
    if started is None:
        return {
            "started_at": None,
            "ended_at": None,
            "elapsed_s": 0,
            "running": False,
            "complete": False,
            "label": "Elapsed",
            "display": "0:00",
        }
    done = bool(complete and not rolling)
    if ended is None and done:
        for gate in project.gates:
            if gate.name == GateName.MIX and not gate.open:
                ended = _aware(gate.locked_at)
                break
    running = ended is None and not done
    stop = ended if ended is not None else now
    elapsed_s = max(0, int((stop - started).total_seconds()))
    label = "Total" if done else "Elapsed"
    return {
        "started_at": started.isoformat(),
        "ended_at": ended.isoformat() if ended else None,
        "elapsed_s": elapsed_s,
        "running": running,
        "complete": done,
        "label": label,
        "display": format_elapsed(elapsed_s),
    }


def _gate_open(project: Project, name: GateName) -> bool:
    for gate in project.gates:
        if gate.name == name:
            return gate.open
    return False


def _selected_take(project: Project, shot_id: str) -> Take | None:
    shot = next((item for item in project.shots if item.id == shot_id), None)
    takes = [take for take in project.takes if take.shot_id == shot_id]
    if shot and shot.selected_take_id:
        hit = next((take for take in takes if take.id == shot.selected_take_id), None)
        if hit:
            return hit
    return takes[-1] if takes else None


def _live_negative(project: Project, take: Take) -> bool:
    return take_has_live_negative(take, project)


def _farmed_count(project: Project) -> int:
    count = 0
    for shot in project.shots:
        take = _selected_take(project, shot.id)
        if take and _live_negative(project, take):
            count += 1
    return count


def _short_fail(note: str) -> str:
    lines = [ln.strip() for ln in (note or "").splitlines() if ln.strip()]
    if not lines:
        return "Wan2.2 failed"
    for ln in reversed(lines):
        if "Error" in ln or ln.startswith("Wan"):
            return ln[:240]
    return lines[-1][:240]


def _humanize_fail(note: str) -> str:
    text = (note or "").strip()
    missing = re.search(r"No module named ['\"]([^'\"]+)['\"]", text)
    if missing:
        return f"Host Python is missing {missing.group(1)}"
    if "has no attribute 'long'" in text or ("numpy" in text and "long" in text):
        return "Host numpy does not match the torch stack"
    try:
        from diony_wan.runner import classify_wan_log

        return classify_wan_log(text, 1)
    except ImportError:
        return _short_fail(text)


def _verified_master(take: Take) -> bool:
    """Clip-level finish proof for the Production read model."""
    return any(
        artifact.kind == ArtifactKind.FINISH_1080
        and artifact.width == 1920
        and artifact.height == 1080
        and artifact.bytes > 32
        for artifact in take.artifacts
    )


def _measured_progress_value(value: object) -> str:
    clean = str(value or "").strip()
    if not clean or "?" in clean or clean.lower() in {"n/a", "na", "none", "--"}:
        return ""
    return clean


def _clean_progress_detail(value: object) -> str:
    """Remove tqdm placeholders from showrunner copy while preserving evidence elsewhere."""
    text = re.sub(r"\s+", " ", str(value or "").strip())
    text = re.sub(r"\bDenoise\s+0/\d+\b", "Denoise starting", text, flags=re.I)
    parts = [part.strip() for part in text.split("·")]
    visible = [part for part in parts if part and _measured_progress_value(part)]
    return " · ".join(visible)


def _clip_progress(row: dict, *, stage: str) -> dict:
    if not isinstance(row, dict):
        return {}
    if stage == "camera":
        done = int(row.get("step") or 0)
        total = int(row.get("total") or 0)
        pct = int(row.get("pct") or (round(done / total * 100) if total else 0))
        label = (f"Denoise step {done} of {total}" if done else "Denoise is starting") if total else ""
    else:
        done = int(row.get("done") or 0)
        total = int(row.get("total") or 0)
        pct = int(row.get("pct") or (round(done / total * 100) if total else 0))
        label = f"Frame {done} of {total}" if total else ""
    if not total:
        return {}
    progress = {
        "done": done,
        "total": total,
        "pct": min(100, max(0, pct)),
        "label": label,
    }
    eta = _measured_progress_value(row.get("eta"))
    rate = _measured_progress_value(row.get("rate"))
    if eta:
        progress["eta"] = eta
    if rate:
        progress["rate"] = rate
    return progress


def _milestones(stage: str, *, failed_at: str = "") -> list[dict]:
    ids = ("camera", "qc", "finish", "ready")
    labels = ("Camera 720", "QC", "Finish 1080", "Ready")
    if failed_at:
        failed_index = ids.index(failed_at) if failed_at in ids else 0
        states = ["done" if index < failed_index else "failed" if index == failed_index else "next" for index in range(4)]
    else:
        active_index = ids.index(stage) if stage in ids else -1
        states = [
            "done" if active_index == 3 or index < active_index else "active" if index == active_index else "next"
            for index in range(4)
        ]
    return [
        {"id": ident, "label": label, "state": state}
        for ident, label, state in zip(ids, labels, states, strict=True)
    ]


def _finish_take_id(finish: dict) -> str:
    current = str(finish.get("current_take_id") or "").strip()
    if current:
        return current
    match = re.search(r"diony_sr_v1\s+([^\s·]+)", str(finish.get("detail") or ""))
    return match.group(1) if match else ""


def _generation_lane_label(lane: str, fallback_reason: str) -> str:
    if lane.startswith("resident_bf16"):
        return "Validated resident BF16"
    if "cudnn_sdpa" in lane:
        return "Qualified B200 cuDNN"
    if lane.startswith("wan22_quality_cli"):
        return "Quality CLI fallback" if fallback_reason else "Wan2.2 quality CLI"
    if lane == "comfyui":
        return "ComfyUI"
    return lane.replace("_", " ").strip().title()


def _retake_reason(project: Project, take: Take | None) -> str:
    """Why the previous generation of this Shot was rejected, if this is a retake."""
    if take is None or take.attempt <= 1:
        return ""
    previous = next(
        (item for item in project.takes_for(take.shot_id) if item.attempt == take.attempt - 1),
        None,
    )
    if previous is None or previous.qc is None or previous.qc.verdict != QCVerdict.FAIL:
        return ""
    return _humanize_fail(previous.qc.notes)[:200]


def _clip_rows(project: Project, farm: dict, finish: dict | None = None) -> list[dict]:
    finish = finish or {}
    stop = project.production_stop
    stopped_take_ids = set(stop.completed_take_ids) if stop else set()
    rolling = farm.get("shot_id") if farm.get("busy") else ""
    finishing = _finish_take_id(finish) if finish.get("busy") else ""
    rows: list[dict] = []
    camera = farm.get("camera") if isinstance(farm.get("camera"), dict) else {}
    rolling_take_id = str(camera.get("take_id") or "") if farm.get("busy") else ""
    for shot in project.shots:
        take = _selected_take(project, shot.id)
        if farm.get("busy") and shot.id == rolling and rolling_take_id:
            # The camera may be rolling a retake or a hero sibling while the
            # shot's selected take is an earlier reject: show the live take.
            try:
                take = project.take(rolling_take_id)
            except KeyError:
                pass
        negative_ready = bool(take and _live_negative(project, take))
        master_ready = bool(take and _verified_master(take))
        progress: dict = {}
        failed_at = ""
        if farm.get("busy") and shot.id == rolling and take is not None and not master_ready:
            state = "rolling"
            stage = "camera"
            status = "Building 720p negative"
            progress = _clip_progress(farm.get("progress") or {}, stage="camera")
            note = progress.get("label") or _clean_progress_detail(
                farm.get("detail") or "Wan2.2 is making the camera negative"
            )
            retake_reason = _retake_reason(project, take)
            if retake_reason:
                status = f"Retake {take.attempt} of {RETAKE_CAP}"
                note = f"{note} · take {take.attempt - 1}: {retake_reason}"
        elif take and take.qc and take.qc.verdict == QCVerdict.FAIL:
            state = "failed"
            stage = "qc" if negative_ready else "camera"
            failed_at = stage
            status = "Stopped at quality control" if negative_ready else "Camera stopped"
            raw = (take.qc.notes if take.qc else "") or "Wan2.2 failed"
            note = _humanize_fail(raw)
        elif master_ready:
            state = "ready"
            stage = "ready"
            status = "1080 master ready"
            note = "1920×1080 finish landed"
        elif stop is not None:
            stage = "stopped"
            if take and take.id in stopped_take_ids and stop.disposition.value == "delete":
                state = "deleted"
                status = "Completed clip deleted"
                note = "Deleted when Production stopped"
            elif take and take.id in stopped_take_ids and negative_ready:
                state = "live"
                status = "720p negative kept"
                note = "Saved in the incomplete result"
            else:
                state = "stopped"
                status = "Not generated"
                note = "Production stopped before this clip"
        elif take and finishing == take.id:
            state = "rolling"
            stage = "finish"
            status = "Finishing at 1080p"
            progress = _clip_progress(finish.get("progress") or {}, stage="finish")
            note = progress.get("label") or "diony_sr_v1 is building the 1080 master"
        elif farm.get("busy") and shot.id == rolling:
            state = "rolling"
            stage = "camera"
            status = "Building 720p negative"
            progress = _clip_progress(farm.get("progress") or {}, stage="camera")
            note = progress.get("label") or _clean_progress_detail(
                farm.get("detail") or "Wan2.2 is making the camera negative"
            )
            retake_reason = _retake_reason(project, take)
            if retake_reason:
                # A retake is not a first attempt: say what AutoQC rejected.
                status = f"Retake {take.attempt} of {RETAKE_CAP}"
                note = f"{note} · take {take.attempt - 1}: {retake_reason}"
        elif negative_ready and take and take.qc and take.qc.settled_by_policy:
            state = "live"
            stage = "qc"
            status = f"Best of {take.qc.settled_best_of} attempts"
            note = f"Proceeding by policy · {_humanize_fail(take.qc.notes)[:160]}"
        elif negative_ready:
            state = "live"
            stage = "qc"
            status = "720p negative ready"
            note = "Quality passed · waiting for 1080 finish"
        else:
            state = "queued"
            stage = "queued"
            status = "Waiting for camera"
            note = "This site works one clip at a time" if project.shots else "Waiting for the rundown"
        receipt = take.generation_receipt if take is not None else None
        generation_lane = receipt.selected_lane if receipt is not None else ""
        generation_fallback = receipt.fallback_reason if receipt is not None else ""
        rows.append(
            {
                "shot_id": shot.id,
                "take_id": take.id if take else "",
                "attempt_label": (
                    f"attempt {take.attempt} of {RETAKE_CAP}" if take is not None else ""
                ),
                "speech_evidence_label": (
                    "Wan2.2 speech measured"
                    if take and take.qc and take.qc.speech_backend
                    else ""
                ),
                # A stated voice verdict (pre-camera hold or measured speech
                # gate) preserves the Take for review; it is not a stopped Wan.
                "voice_hold": bool(
                    take
                    and take.qc
                    and take.qc.verdict == QCVerdict.FAIL
                    and take.qc.notes.startswith("Voice")
                    and "failed" in take.qc.notes
                ),
                "engine": shot.engine.value if shot.engine else "",
                "state": state,
                "stage": stage,
                "status": status,
                "note": note,
                "progress": progress,
                "negative_ready": negative_ready,
                "master_ready": master_ready,
                "generation_lane": generation_lane,
                "generation_lane_label": _generation_lane_label(
                    generation_lane,
                    generation_fallback,
                ) if generation_lane else "",
                "generation_fallback": generation_fallback,
                "generation_elapsed_seconds": (
                    receipt.elapsed_seconds if receipt is not None else None
                ),
                "inference_frames_per_pass": (
                    receipt.inference_frames_per_pass if receipt is not None else None
                ),
                "denoise_passes": receipt.denoise_passes if receipt is not None else None,
                "effective_denoise_steps": (
                    receipt.effective_denoise_steps if receipt is not None else None
                ),
                "quality_contract_matched": (
                    receipt.quality_contract_matched if receipt is not None else None
                ),
                "attention_backend": receipt.attention_backend if receipt is not None else "",
                "milestones": _milestones(stage, failed_at=failed_at),
            }
        )
    return rows


def _activity_copy(src: str, text: str, *, level: str, shot_id: str) -> tuple[str, str]:
    lower = text.lower()
    clip = shot_id or (re.search(r"\bsh-[a-z0-9-]+", lower).group(0) if re.search(r"\bsh-[a-z0-9-]+", lower) else "")
    if "loading weights" in lower:
        return "GPU is loading the camera model", "The first clip starts after the model is in GPU memory."
    if "weights loaded" in lower:
        return "Camera model is ready", "The GPU can begin the next camera pass."
    if src == "mix" and "measured and verified" in lower:
        return "Mix measured and verified", "Loudness and true-peak evidence are attached to the master."
    if src == "mix" and "mixing" in lower:
        return "Measuring the final mix", "Diony is measuring loudness and true peak before lock."
    if src in {"sr", "finish"} and "rebuilding overlap stitch" in lower:
        return "Rebuilding the overlap stitch", "The 1080 program master is being conformed and legalized."
    if "negative landed" in lower:
        return f"{clip or 'Clip'} has a 720p camera negative", "Quality control can now pass it to 1080 finish."
    if "1080 master" in lower and "failed" not in lower:
        return f"{clip or 'Clip'} has a 1080 master", "The finished clip is retained independently in the vault."
    if "diony_sr_v1" in lower or "1080 finish" in lower or re.search(r"\bframe\s*[=:]?\s*\d+", lower):
        frame = re.search(r"frame\s*[=:]?\s*(\d+)(?:/(\d+))?", lower)
        detail = "diony_sr_v1 is creating the 1920×1080 clip."
        if frame:
            detail = f"Frame {frame.group(1)} of {frame.group(2)} is complete." if frame.group(2) else f"Frame {frame.group(1)} is complete."
        return f"Finishing {clip or 'the current clip'} at 1080p", detail
    if "denoise" in lower or "generating 720p" in lower or "generate.py" in lower:
        step = re.search(r"(?:denoise\s*)?(\d+)/(\d+)", lower)
        detail = "Wan2.2 is creating the 720p camera negative."
        if step:
            detail = (
                f"Denoise step {step.group(1)} of {step.group(2)} is complete."
                if int(step.group(1)) > 0
                else "Denoise is starting."
            )
        return f"Building {clip or 'the current clip'} at 720p", detail
    if level == "fail":
        return f"{clip or 'Production'} needs attention", _humanize_fail(text)
    if src == "gpu":
        return "GPU heartbeat received", "The signed production site is responding."
    if src in {"sr", "finish"}:
        return "1080 finish updated", "Picture finish reported a new checkpoint."
    if src == "wan":
        return "Camera updated", "Wan2.2 reported a new checkpoint."
    return "Production updated", text


def _activity_row(src: str, text: str, *, level: str = "info", shot_id: str = "", at: str = "") -> dict:
    title, detail = _activity_copy(src, text, level=level, shot_id=shot_id)
    take_match = re.search(r"\b(sh-[a-z0-9-]+-t\d+)\b", text.casefold())
    progress_kind = (
        "Frame"
        if src in {"sr", "finish"} or "frame" in text.casefold()
        else "Denoise"
        if "denoise" in text.casefold()
        else ""
    )
    progress_match = (
        re.search(r"(?:denoise\s*)?(\d+)\s*/\s*(\d+)", text, re.IGNORECASE)
        if progress_kind
        else None
    )
    rate_match = re.search(r"\b\d+(?:\.\d+)?(?:s/it|it/s)\b", text, re.IGNORECASE)
    eta_match = re.search(r"\b(?:\d{1,2}:)?\d{2}:\d{2}\s+left\b", text, re.IGNORECASE)
    progress: dict[str, object] = {}
    if progress_match:
        done = int(progress_match.group(1))
        total = int(progress_match.group(2))
        progress = {
            "done": done,
            "total": total,
            "label": f"{progress_kind} {done} / {total}",
            "rate": rate_match.group(0) if rate_match else "",
            "eta": eta_match.group(0) if eta_match else "",
        }
    return {
        "src": src,
        "level": level,
        "status": "working" if level == "live" else "attention" if level == "fail" else "waiting" if level == "wait" else "checkpoint",
        "text": text,
        "title": title,
        "detail": detail,
        "technical": text,
        "shot_id": shot_id,
        "take_id": take_match.group(1) if take_match else "",
        "progress": progress,
        "at": at,
    }


def _activity_lines(
    clips: list[dict],
    farm: dict,
    finish: dict,
    now: str,
    monitor: dict | None = None,
    *,
    mix: dict | None = None,
) -> list[dict]:
    """Camera report: what is rolling, what failed, what landed. Never access logs."""
    monitor = monitor or {}
    mix = mix or {}
    lines: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def add(src: str, text: str, *, level: str = "info", shot_id: str = "", at: str = "") -> None:
        msg = re.sub(r"\s+", " ", (text or "").strip())
        if not msg:
            return
        lower = msg.lower()
        structured_access = bool(
            re.search(r'"event"\s*:\s*"http\.request\.', lower)
            or (
                re.search(r'"path"\s*:\s*"/api/', lower)
                and re.search(r'"status_code"\s*:', lower)
            )
        )
        plain_access = bool(re.search(r"\b(?:get|post|put|patch|delete|options|head)\s+/api/", lower))
        if structured_access or plain_access:
            return
        key = (src, msg)
        if key in seen:
            return
        seen.add(key)
        lines.append(_activity_row(src, msg[:280], level=level, shot_id=shot_id, at=at))

    failed_ids = {clip["shot_id"] for clip in clips if clip["state"] == "failed"}
    live_ids = {clip["shot_id"] for clip in clips if clip["state"] == "live"}
    current = str(farm.get("shot_id") or "")
    finish_current = str(finish.get("current_shot_id") or "")
    busy = bool(farm.get("busy"))
    finish_busy = bool(finish.get("busy"))
    mix_busy = bool(mix.get("busy"))

    for row in monitor.get("journal") or []:
        if not isinstance(row, dict):
            continue
        src = str(row.get("src") or "run")
        level = str(row.get("level") or "info")
        shot_id = str(row.get("shot_id") or "")
        text = str(row.get("text") or "")
        if level == "fail":
            active_failure = bool(
                (shot_id and shot_id in failed_ids)
                or (src == "wan" and farm.get("error"))
                or (src in {"sr", "finish"} and finish.get("error"))
                or (src == "mix" and mix.get("error"))
            )
            add(
                src,
                _humanize_fail(text),
                level="fail" if active_failure else "info",
                shot_id=shot_id,
                at=str(row.get("at") or ""),
            )
            continue
        if level == "live":
            active_live = bool(
                (src == "wan" and busy and (not shot_id or shot_id == current))
                or (
                    src in {"sr", "finish"}
                    and finish_busy
                    and (not shot_id or shot_id == finish_current)
                )
                or (src == "mix" and mix_busy)
                or (src == "gpu" and (busy or finish_busy or mix_busy))
            )
            if not active_live:
                level = "info"
        if src == "wan" and "negative landed" in text and shot_id in live_ids:
            add(src, text, level=level, shot_id=shot_id, at=str(row.get("at") or ""))
            continue
        if src == "wan" and busy and shot_id and shot_id != current:
            continue
        add(src, text, level=level, shot_id=shot_id, at=str(row.get("at") or ""))

    shot = str(farm.get("shot_id") or "")
    generate = str(monitor.get("generate") or "").strip()
    if farm.get("busy"):
        if generate:
            add("wan", f"{shot + ' · ' if shot else ''}{generate}", level="live", shot_id=shot)
        else:
            add("wan", str(farm.get("detail") or "Generating 720p negative"), level="live", shot_id=shot)
    if finish.get("busy"):
        add(
            "sr",
            str(finish.get("detail") or "diony_sr_v1 1080 finish"),
            level="live",
            shot_id=finish_current,
        )

    heartbeat = str(monitor.get("heartbeat") or "").strip()
    if heartbeat:
        add("gpu", heartbeat, level="live" if farm.get("busy") or finish.get("busy") else "info")

    for clip in clips:
        if clip["state"] == "failed" and clip.get("note"):
            add("qc", f"{clip['shot_id']} · {_humanize_fail(clip['note'])}", level="fail", shot_id=clip["shot_id"])
        elif clip["state"] == "rolling" and clip.get("stage") != "finish":
            add("wan", f"{clip['shot_id']} · generating 720p negative", level="live", shot_id=clip["shot_id"])
        elif clip["state"] == "live":
            add("wan", f"{clip['shot_id']} · 720p negative landed", level="info", shot_id=clip["shot_id"])

    for line in str(monitor.get("log") or "").splitlines():
        blob = line.strip()
        if not blob:
            continue
        if any(mark in blob for mark in ("Error", "Traceback", "Exception")):
            continue
        add("gpu", blob, level="info")

    if not lines and now:
        add("run", now, level="wait")

    def priority(row: dict) -> int:
        technical = str(row.get("technical") or "").lower()
        if row.get("level") == "fail":
            return 0
        if current and row.get("shot_id") == current and row.get("level") == "live":
            return 1
        if finish_current and row.get("shot_id") == finish_current and row.get("level") == "live":
            return 1
        if row.get("src") == "gpu" and row.get("level") == "live":
            return 2
        if (busy or finish_busy or mix_busy) and "landed" in technical:
            return 3
        if row.get("level") == "live":
            return 4
        return 5

    recent = lines[-20:]
    ordered = sorted(enumerate(recent), key=lambda item: (priority(item[1]), -item[0]))
    return [row for _, row in ordered]


def _monitoring_summary(
    clips: list[dict],
    *,
    phase: str,
    now: str,
    failed: bool,
    monitor: dict,
    activity: list[dict],
    progress: dict,
    master_ready: bool,
    material_label: str,
) -> dict:
    counts = {
        "total": len(clips),
        "queued": sum(1 for clip in clips if clip.get("state") == "queued"),
        "working": sum(1 for clip in clips if clip.get("state") == "rolling"),
        "negatives": sum(1 for clip in clips if clip.get("state") == "live"),
        "ready": sum(1 for clip in clips if clip.get("state") == "ready"),
        "failed": sum(1 for clip in clips if clip.get("state") == "failed"),
        "stopped": sum(1 for clip in clips if clip.get("state") == "stopped"),
        "deleted": sum(1 for clip in clips if clip.get("state") == "deleted"),
    }
    current = next((clip for clip in clips if clip.get("state") == "rolling"), None)
    attention = next((clip for clip in clips if clip.get("state") == "failed"), None)
    if phase == "stopped":
        title = "Production stopped"
        detail = now
        eyebrow = "PRODUCTION · STOPPED"
        signal_progress = {}
        signal_state = "stopped"
        clip_id = take_id = ""
        stage = phase
    elif current:
        stage = str(current.get("stage") or phase)
        if stage == "finish":
            title = f"Finishing {current['shot_id']} at 1080p"
            detail = "diony_sr_v1 is building the selected clip master."
            eyebrow = "PICTURE · LIVE"
        elif str(current.get("note") or "").startswith("Staging "):
            title = f"Staging the camera for {current['shot_id']}"
            detail = str(current["note"])
            eyebrow = "CAMERA · STAGING"
        elif str(current.get("note") or "").startswith("Locking the voice"):
            title = f"Casting the voice for {current['shot_id']}"
            detail = "The line is cast and locked as a Wan2.2 speech request; Wan performs it and animates the face in one pass."
            eyebrow = "VOICE · LIVE"
        elif str(current.get("status") or "").startswith("Retake "):
            title = f"{current['status']} · {current['shot_id']} at 720p"
            detail = str(current.get("note") or "AutoQC rejected the previous take; Wan2.2 is rolling again.")
            eyebrow = "CAMERA · RETAKE"
        else:
            title = f"Building {current['shot_id']} at 720p"
            detail = "Wan2.2 is creating the camera negative on the signed GPU."
            eyebrow = "CAMERA · LIVE"
        signal_progress = current.get("progress") or {}
        if signal_progress.get("label"):
            detail = str(signal_progress["label"])
            if signal_progress.get("rate"):
                detail += f" · {signal_progress['rate']}"
            if signal_progress.get("eta"):
                detail += f" · GPU estimate {signal_progress['eta']} remaining"
        signal_state = "live"
        clip_id = current["shot_id"]
        take_id = current.get("take_id") or ""
    elif attention or failed:
        current = attention
        title = f"{attention['shot_id']} needs attention" if attention else "Production needs attention"
        detail = str(attention.get("note") if attention else now)
        eyebrow = "PRODUCTION · STOPPED"
        signal_progress = {}
        signal_state = "attention"
        clip_id = str(attention.get("shot_id") if attention else "")
        take_id = str(attention.get("take_id") if attention else "")
        stage = str(attention.get("stage") if attention else phase)
    elif phase == "mix":
        title = "Verifying the final mix"
        detail = now
        eyebrow = "SOUND · CHECKING"
        signal_progress = {}
        signal_state = "live"
        clip_id = take_id = ""
        stage = phase
    elif phase == "dry_run":
        title = "Dry run complete — no master"
        detail = "Use a signed production site to create the 720p negatives, 1080 finish, mix, and Gallery media."
        eyebrow = "DRY RUN · NO MASTER"
        signal_progress = {}
        signal_state = "waiting"
        clip_id = take_id = ""
        stage = phase
    elif phase in {"distribution", "done"} and not master_ready:
        dry_run = material_label.startswith("DRY RUN")
        title = "Dry run complete — no master" if dry_run else "Master verification is incomplete"
        detail = (
            "Use a signed production site to create the 720p negatives, 1080 finish, mix, and Gallery media."
            if dry_run
            else now
        )
        eyebrow = "DRY RUN · NO MASTER" if dry_run else "MASTER · VERIFY"
        signal_progress = {}
        signal_state = "waiting"
        clip_id = take_id = ""
        stage = phase
    elif phase in {"distribution", "done"}:
        title = "Program master is ready for review" if phase == "distribution" else "Production is complete"
        detail = now
        eyebrow = "MASTER · READY"
        signal_progress = {}
        signal_state = "ready"
        clip_id = take_id = ""
        stage = phase
    else:
        title = "Production is waiting"
        detail = now
        eyebrow = "PRODUCTION · STANDBY"
        signal_progress = {}
        signal_state = "waiting"
        clip_id = take_id = ""
        stage = phase

    gpu = monitor.get("gpu") if isinstance(monitor.get("gpu"), dict) else {}
    updated_at = str(monitor.get("observed_at") or "")
    if not updated_at and activity:
        updated_at = str(activity[-1].get("at") or "")
    return {
        "signal": {
            "state": signal_state,
            "eyebrow": eyebrow,
            "title": title,
            "detail": detail,
            "stage": stage,
            "clip_id": clip_id,
            "take_id": take_id,
            "progress": signal_progress,
            "updated_at": updated_at,
        },
        "counts": counts,
        "progress": progress,
        "connection": "live" if monitor.get("heartbeat") else "checking" if signal_state == "live" else "idle",
        "gpu": {
            "name": str(gpu.get("name") or ""),
            "util": gpu.get("util"),
            "used_mib": gpu.get("used_mib"),
            "total_mib": gpu.get("total_mib"),
            "heartbeat": str(monitor.get("heartbeat") or ""),
        },
        "poll_ms": 2500,
    }


def _step(ident: str, label: str, state: str, note: str = "") -> dict:
    return {"id": ident, "label": label, "state": state, "note": note}


def _target_peak(value: float) -> str:
    absolute = f"{abs(value):g}"
    sign = "−" if value < 0 else "+" if value > 0 else ""
    return f"{sign}{absolute} dBTP"


def production_standard(
    project: Project,
    *,
    clips: list[dict],
    camera: str,
    phase: str = "",
    masters_ready: bool | None = None,
) -> dict:
    """Authoritative Production rail; never infer master readiness in the browser."""
    picture_open = _gate_open(project, GateName.PICTURE)
    mix_open = _gate_open(project, GateName.MIX)
    failed = sum(1 for clip in clips if clip.get("state") == "failed")
    master_path = project.timeline.preview_path if project.timeline else ""
    has_program_master = bool(master_path and "master_1080" in master_path)
    if masters_ready is None:
        masters_ready = selected_masters_ready(project)
    mix_blockers = mix_evidence_blockers(project)
    mix_receipt = not mix_blockers
    master_ready = bool(
        camera == "live"
        and project.shots
        and not failed
        and not picture_open
        and not mix_open
        and has_program_master
        and masters_ready
        and mix_receipt
    )

    review = project.final_review
    review_failed = bool(
        review is not None
        and review.status.value == "fail"
        and project.timeline is not None
        and review.timeline_id == project.timeline.id
        and review.master_sha256
        and review.master_sha256 in {project.timeline.program_sha256, project.timeline.picture_sha256}
    )
    if review_failed:
        master_ready = False
    blockers: list[str] = []
    if project.production_stop is not None:
        blockers.append("Production was stopped before the 1080 master completed.")
    if review_failed and review is not None:
        blockers.append(
            "Final review failed: " + "; ".join(review.issues_found)
            + (f" Remediation: {review.remediation}." if review.remediation else "")
        )
    if failed:
        blockers.append(f"{failed} selected Take{'s' if failed != 1 else ''} failed AutoQC.")
    if not project.shots:
        blockers.append("Lock a Bible and build the rundown.")
    if picture_open:
        blockers.append("Picture finish has not locked.")
    if mix_open:
        blockers.append("Mix has not locked.")
    if not has_program_master:
        blockers.append("The 1920×1080 program master has not verified.")
    elif not masters_ready:
        blockers.append("One or more selected Takes lack a verified 1920×1080 finish.")
    blockers.extend(mix_blockers)
    if camera == "stub":
        blockers.append("Stub camera output is a dry run, never a distributable master.")

    if project.production_stop is not None:
        status = "INCOMPLETE"
    elif failed or review_failed:
        status = "QC HOLD"
    elif master_ready:
        status = "MASTER READY"
    elif camera == "stub":
        status = "PLANNING"
    elif phase == "mix" and mix_blockers:
        status = "MIX HOLD"
    elif phase == "mix":
        status = "MIXING"
    elif phase == "farm" or any(clip.get("state") in {"rolling", "live"} for clip in clips):
        status = "NEGATIVES"
    elif phase == "finish" or picture_open:
        status = "FINISHING"
    elif phase == "mix" or mix_open:
        status = "MIXING"
    elif has_program_master:
        status = "VERIFICATION NEEDED"
    else:
        status = "PLANNING"

    if project.production_stop is not None and project.production_stop.result_path:
        material_label = "INCOMPLETE 720P RESULT · NOT MASTER"
    elif project.production_stop is not None:
        material_label = "STOPPED · NO RESULT"
    elif master_ready:
        material_label = "1080 MASTER READY"
    elif camera == "stub":
        material_label = "DRY RUN · NO MASTER"
    elif phase == "mix":
        material_label = "MIX INCOMPLETE · NOT MASTER"
    elif phase == "finish":
        material_label = "FINISH IN PROGRESS · NOT MASTER"
    else:
        material_label = "720p CAMERA NEGATIVE · NOT FINAL"

    fps = "24.000" if abs(project.bible.fps - 24.0) < 0.0005 else "23.976"
    loudness = "−24 LUFS broadcast" if project.bible.loudness.value == "broadcast" else "−16 LUFS streaming"
    return {
        "negative": {
            "label": "1280×720",
            "model": "Wan2.2 A14B",
            "role": "camera negative · not a master",
        },
        "final": {
            "label": f"{project.bible.width}×{project.bible.height}",
            "aspect": "16:9",
            "fps": fps,
            "color": "Rec.709 legal",
        },
        "finish": {
            "model": "diony_sr_v1",
            "method": "temporal 720→1080",
            "lanczos_deliverable": False,
        },
        "audio": {
            "target": loudness,
            "true_peak": _target_peak(project.bible.true_peak_dbtp),
            "layout": project.bible.audio_layout,
        },
        "material_label": material_label,
        "status": status,
        "master_ready": master_ready,
        "master_path": master_path if has_program_master else "",
        "blockers": blockers,
        "final_review": (
            {
                "status": review.status.value,
                "recommended_action": review.recommended_action.value,
                "issues": list(review.issues_found),
                "offending_shot_ids": list(review.offending_shot_ids),
                "remediation": review.remediation,
                "current": bool(
                    project.timeline is not None
                    and review.timeline_id == project.timeline.id
                    and (
                        not review.master_sha256
                        or review.master_sha256
                        in {project.timeline.program_sha256, project.timeline.picture_sha256}
                    )
                ),
            }
            if review is not None
            else None
        ),
        "rundown_review": (
            {
                "verdict": project.rundown_review.verdict.value,
                "score": project.rundown_review.score,
                "violations": list(project.rundown_review.violations),
            }
            if project.rundown_review is not None
            else None
        ),
        "deliverables": [
            {"id": item.id, "kind": item.kind.value, "path": item.path, "sha256": item.sha256}
            for item in project.deliverables
        ],
    }


def run_payload(
    project: Project,
    *,
    farm: dict | None = None,
    finish: dict | None = None,
    mix: dict | None = None,
    live: bool | None = None,
) -> dict:
    """What the showrunner watches after Produce."""
    farm = farm if farm is not None else farm_status()
    finish = finish if finish is not None else finish_status()
    mix = mix if mix is not None else mix_status()
    site = site_status()
    live = live_camera() if live is None else live
    pack_on_gpu = wizard_pack_on_gpu()
    finish_live = bool(finish.get("live")) if isinstance(finish.get("live"), bool) else live_finish()
    if pack_on_gpu:
        finish_live = True
    site_gpu = signed_gpu_site() and not live
    shots = len(project.shots)
    takes = len(project.takes)
    farmed = _farmed_count(project)
    minutes = project.brief.target_minutes if project.brief else 5
    production_kind = project.brief.production_kind.value if project.brief else "program"
    sample_seconds = project.brief.sample_seconds if project.brief else None
    goal = (
        f"{sample_seconds}s sample"
        if production_kind == "sample" and sample_seconds is not None
        else allotment_label(minutes)
    )
    picture_open = _gate_open(project, GateName.PICTURE)
    mix_open = _gate_open(project, GateName.MIX)
    dist_open = _gate_open(project, GateName.DISTRIBUTION)
    stop_receipt = project.production_stop
    pause_receipt = project.production_pause

    # A worker adopted after a Producer restart may only report the current
    # one-Take queue. The locked Project rundown is the durable denominator;
    # never let a transient worker counter regress the Production board.
    farm_total = max(shots, int(farm.get("total") or 0))
    shown = len(stop_receipt.completed_take_ids) if stop_receipt is not None else farmed
    farm_incomplete = bool(
        stop_receipt is None
        and takes
        and picture_open
        and (live or pack_on_gpu)
        and farmed < shots
    )
    clips = _clip_rows(project, farm, finish)
    fail_notes = [clip["note"] for clip in clips if clip["state"] == "failed" and clip.get("note")]
    failed_clip_count = sum(1 for clip in clips if clip["state"] == "failed")
    passed_shown = max(0, shown - failed_clip_count)
    farm_error = str(farm.get("error") or "").strip()
    finish_error = str(finish.get("error") or "").strip()
    mix_error = str(mix.get("error") or "").strip()
    mix_busy = bool(mix.get("busy"))
    if farm_error and not fail_notes:
        fail_notes = [_humanize_fail(farm_error)]
    # A landed negative is not a successful farm when its selected Take failed
    # AutoQC. Count bytes for tallying, but keep the production phase on the QC
    # hold and never present finish/stitch as active.
    farm_failed = bool(fail_notes) and not farm["busy"]

    if stop_receipt is not None:
        phase = "stopped"
        waiting = None
        kept = stop_receipt.disposition.value == "keep"
        if kept and stop_receipt.result_path:
            now = (
                f"Production stopped. {shown} completed clip"
                f"{'s' if shown != 1 else ''} saved as an incomplete result."
            )
            tally_note = "incomplete result kept"
        elif kept:
            now = "Production stopped. No completed clips were available to keep."
            tally_note = "no completed clips"
        else:
            now = "Production stopped. Completed clips were deleted."
            tally_note = "completed clips deleted"
        if stop_receipt.result_error:
            now = f"Production stopped. {stop_receipt.result_error}"
            tally_note = "incomplete result needs attention"
        tally = f"{shown} / {shots}" if shots else "—"
    elif farm["busy"]:
        phase = "farm"
        waiting = None
        now = _clean_progress_detail(farm.get("detail") or "Wan2.2 720p")
        shot = str(farm.get("shot_id") or "")
        if shot and shot not in now:
            now = f"{now} · {shot}"
        progress = farm.get("progress") if isinstance(farm.get("progress"), dict) else {}
        if farm_total:
            now = f"{now} · clip {min(shown + 1, farm_total)} of {farm_total}"
        tally = f"{shown} / {farm_total}" if farm_total else "rolling"
        if progress.get("step") and progress.get("total"):
            tally_note = f"denoise {progress['step']}/{progress['total']}"
        else:
            tally_note = "Wan 720p farm"
        if pause_receipt is not None:
            now = f"Draining after {pause_receipt.after_take_id} · {now}"
            tally_note = "current Take will land, then hold"
    elif pause_receipt is not None:
        phase = "paused"
        waiting = "performance_qualification"
        remaining = len(pause_receipt.remaining_take_ids)
        now = (
            f"Production held after {pause_receipt.after_take_id}. "
            f"{remaining} selected Take{'s' if remaining != 1 else ''} remain."
        )
        tally = f"{shown} / {farm_total or shots}" if (farm_total or shots) else "—"
        tally_note = "camera queue held"
    elif finish["busy"]:
        phase = "finish"
        waiting = None
        now = finish.get("detail") or "diony_sr_v1 1080 finish"
        tally = f"{shown} / {shots}" if shots else "—"
        tally_note = "1080 finish"
    elif finish_error:
        phase = "finish"
        waiting = None
        now = f"1080 finish stopped. {finish_error}"
        tally = f"{shown} / {shots}" if shots else "—"
        tally_note = "1080 finish failed"
    elif not live and site_gpu:
        phase = "gpu"
        waiting = None
        now = gpu_block_copy()
        tally = f"{shown} / {shots}" if shots else "—"
        tally_note = "Wan is on the signed GPU"
    elif not takes:
        phase = "idle"
        waiting = None
        now = "Produce starts the run."
        tally = "—"
        tally_note = "no show yet"
    elif farm_failed:
        phase = "farm"
        waiting = None
        held = sum(1 for clip in clips if clip.get("state") == "failed")
        if not farm.get("error") and (passed_shown or held > 1):
            # The camera worked the whole rundown; a measured hold on some
            # clips is a review item, not a stopped Wan.
            now = (
                f"Camera finished · {held} clip{'s' if held != 1 else ''} on QC hold. "
                f"{fail_notes[0]}"
            )
        else:
            now = f"Wan2.2 stopped. {fail_notes[0]}"
        denominator = farm_total or shots
        tally = (
            f"{passed_shown} passed · {failed_clip_count} failed / {denominator}"
            if denominator
            else "—"
        )
        tally_note = (
            "QC hold · Take preserved"
            if any(
                clip.get("state") == "failed" and clip.get("voice_hold")
                for clip in clips
            )
            else "Wan failed"
        )
    elif farm_incomplete:
        phase = "farm"
        waiting = None
        now = f"Wan2.2 720p next · {shown} of {farm_total or shots}"
        tally = f"{shown} / {farm_total or shots}" if (farm_total or shots) else "—"
        tally_note = "Wan 720p farm"
    elif picture_open and live and not finish_live:
        phase = "finish"
        waiting = None
        now = "Wan 720p is up. 1080 finish needs diony_sr_v1 on this site."
        tally = f"{shown} / {shots}" if shots else "—"
        tally_note = "1080 pack"
    elif picture_open:
        phase = "finish"
        waiting = None
        now = "1080 finish is part of this run."
        tally = f"{shown} / {shots}" if shots else "—"
        tally_note = "diony_sr_v1"
    elif mix_open and finish_live and not selected_masters_ready(project) and not pack_on_gpu:
        phase = "finish"
        waiting = None
        now = "1080 finish is part of this run."
        tally = f"{shown} / {shots}" if shots else "—"
        tally_note = "diony_sr_v1"
    elif mix_open and mix.get("error"):
        phase = "mix"
        waiting = None
        now = f"Mix stopped. {mix['error']}"
        tally = f"{shown} / {shots}" if shots else "—"
        tally_note = "mix failed"
    elif mix_open and mix.get("busy"):
        phase = "mix"
        waiting = None
        target = "−24" if project.bible.loudness.value == "broadcast" else "−16"
        now = f"Mixing to {target} LUFS · {_target_peak(project.bible.true_peak_dbtp)}."
        tally = f"{shown} / {shots}" if shots else "—"
        tally_note = "measuring mix"
    elif mix_open and project.final_review is not None and project.final_review.status.value == "fail" and project.final_review.master_sha256 and project.timeline is not None and project.final_review.master_sha256 in {project.timeline.program_sha256, project.timeline.picture_sha256}:
        phase = "mix"
        waiting = None
        review = project.final_review
        now = "Final review failed: " + "; ".join(review.issues_found)[:240] + (
            f" · {review.remediation}" if review.remediation else " · retaking the print that failed"
        )
        tally = f"{shown} / {shots}" if shots else "—"
        tally_note = "final review"
    elif mix_open:
        phase = "mix"
        waiting = None
        evidence = mix_evidence_blockers(project)
        now = "Mix blocked: " + " ".join(evidence) if evidence else "Verifying the final mix."
        tally = f"{shown} / {shots}" if shots else "—"
        tally_note = "mix evidence needed" if evidence else "mix"
    elif dist_open:
        if production_kind == "sample" and not live and not site_gpu:
            phase = "dry_run"
            waiting = None
            now = "Sample dry run complete. No master or Gallery media was created."
            tally = f"{shown} / {shots}" if shots else "—"
            tally_note = "dry run · no master"
        else:
            phase = "distribution"
            waiting = "distribution"
            now = "Waiting on you: distribution (Gallery)."
            tally = f"{shown} / {shots}" if shots else "—"
            tally_note = "mix locked"
    else:
        phase = "done"
        waiting = None
        now = "Gates are locked."
        tally = f"{shown} / {shots}" if shots else "—"
        tally_note = "show locked"

    if stop_receipt is not None:
        farm_state = "stopped"
        farm_note = f"{shown} / {shots} completed before STOP" if shots else "stopped"
    elif farm["busy"]:
        farm_state, farm_note = "rolling", f"{shown} / {farm_total or shots} · sequential on this site"
    elif pause_receipt is not None:
        farm_state, farm_note = "paused", f"{len(pause_receipt.remaining_take_ids)} selected Takes held"
    elif farm_failed:
        farm_state, farm_note = "failed", fail_notes[0]
    elif site_gpu:
        farm_state, farm_note = "blocked", "on the signed GPU · this desk is not the camera"
    elif not live and takes:
        farm_state, farm_note = "stub", "cannot start · stub camera"
    elif shots and shown >= shots:
        farm_state, farm_note = "done", f"{shown} live 720p"
    elif takes:
        farm_state, farm_note = "next", f"{shown} / {shots}"
    else:
        farm_state, farm_note = "next", ""

    if stop_receipt is not None:
        picture_state, picture_note = "stopped", "Production stopped"
    elif pause_receipt is not None:
        picture_state, picture_note = "next", "after camera resumes"
    elif finish["busy"]:
        picture_state, picture_note = "rolling", finish.get("detail") or "diony_sr_v1"
    elif finish_error:
        picture_state, picture_note = "failed", finish_error
    elif farm_failed:
        picture_state, picture_note = "next", "blocked by AutoQC"
    elif picture_open and farm_incomplete:
        picture_state, picture_note = "next", "after Wan"
    elif picture_open and live and not finish_live:
        picture_state, picture_note = "blocked", "needs diony_sr_v1"
    elif picture_open and site_gpu and not live:
        picture_state, picture_note = "next", "after Wan"
    elif picture_open:
        picture_state, picture_note = "rolling", "1080 finish"
    else:
        picture_state, picture_note = "done", "locked"

    if stop_receipt is not None:
        mix_state, mix_note = "stopped", "Production stopped"
    elif pause_receipt is not None:
        mix_state, mix_note = "next", "after 1080 finish"
    elif finish["busy"]:
        mix_state, mix_note = "next", "after 1080 finish"
    elif finish_error:
        mix_state, mix_note = "blocked", "needs complete 1080 masters"
    elif mix_open and mix.get("error"):
        mix_state, mix_note = "failed", str(mix["error"])
    elif mix_open and mix.get("busy"):
        mix_state, mix_note = "rolling", str(mix.get("detail") or "measuring loudness")
    elif mix_open and not picture_open and mix_evidence_blockers(project):
        mix_state = "blocked"
        mix_note = mix_evidence_blockers(project)[0].removesuffix(".").lower()
    elif mix_open and not picture_open:
        mix_state, mix_note = "rolling", "verifying loudness"
    elif mix_open:
        mix_state, mix_note = "next", ""
    elif project.mix is not None and project.mix.picture_only:
        mix_state, mix_note = "done", "mute · picture-only by policy"
    else:
        mix_state, mix_note = "done", "locked"

    dist_state = (
        "stopped"
        if stop_receipt is not None
        else "next"
        if pause_receipt is not None
        else "waiting"
        if waiting == "distribution"
        else "done"
        if not dist_open
        else "next"
    )

    if stop_receipt is not None:
        stitch_state = "done" if stop_receipt.result_path else "stopped"
        stitch_note = (
            "incomplete 720p result"
            if stop_receipt.result_path
            else "deleted"
            if stop_receipt.disposition.value == "delete"
            else "no completed clips"
        )
    else:
        stitch_state = "done" if shown >= shots and shots and not farm_failed else "next"
        stitch_note = (
            f"exact {sample_seconds}s cut"
            if production_kind == "sample" and shown >= shots and shots and not farm_failed
            else "8–16 frames"
            if shown >= shots and shots and not farm_failed
            else "after Wan"
        )

    steps = [
        _step("bible", "Bible + rundown", "done" if shots else "next", f"{shots} shots · goal {goal}" if shots else ""),
        _step("farm", "Wan2.2 720p", farm_state, farm_note),
        _step(
            "stitch",
            "Time-frame assembly" if production_kind == "sample" else "Overlap stitch",
            stitch_state,
            stitch_note,
        ),
        _step("picture", "1080 finish", picture_state, picture_note),
        _step("mix", "Mix", mix_state, mix_note),
        _step("distribution", "Distribution", dist_state, "Gallery" if dist_state == "waiting" else ""),
    ]

    monitor = gpu_monitor()
    farm_view = dict(farm)
    finish_view = dict(finish)
    if stop_receipt is not None:
        farm_view.update({"busy": False, "queued": 0, "shot_id": "", "detail": ""})
        finish_view.update({"busy": False, "queued": 0, "current_take_id": "", "detail": ""})
    activity = _activity_lines(clips, farm_view, finish_view, now, monitor, mix=mix)
    stage_completed = 0
    stage_partial = 0.0
    if phase == "stopped":
        stage_completed = shown
    elif phase in {"farm", "paused"}:
        stage_completed = passed_shown if farm_failed else shown
        if phase == "farm":
            stage_partial = min(100, max(0, int((farm.get("progress") or {}).get("pct") or 0))) / 100
    elif phase == "finish":
        stage_completed = sum(1 for clip in clips if clip.get("master_ready"))
        stage_partial = min(100, max(0, int((finish.get("progress") or {}).get("pct") or 0))) / 100
    elif phase in {"mix", "distribution", "done"}:
        stage_completed = shots
    stage_progress = {
        "stage": phase,
        "completed": stage_completed,
        "total": shots,
        "pct": round(((stage_completed + stage_partial) / shots) * 100) if shots else 0,
    }
    line = connect_from_lock()
    camera = "live" if live else ("site" if site_gpu else "stub")
    stopped_payload = {
        "stopped": False,
        "disposition": "",
        "stopped_at": "",
        "completed_clip_count": 0,
        "completed_take_ids": [],
        "result_path": "",
        "result_sha256": "",
        "result_width": 0,
        "result_height": 0,
        "result_url": "",
        "download_url": "",
        "deleted_file_count": 0,
        "result_error": "",
    }
    if stop_receipt is not None:
        stopped_payload = {
            "stopped": True,
            "disposition": stop_receipt.disposition.value,
            "stopped_at": stop_receipt.stopped_at.isoformat(),
            "completed_clip_count": len(stop_receipt.completed_take_ids),
            "completed_take_ids": list(stop_receipt.completed_take_ids),
            "result_path": stop_receipt.result_path,
            "result_sha256": stop_receipt.result_sha256,
            "result_width": stop_receipt.result_width,
            "result_height": stop_receipt.result_height,
            "result_url": "/api/program/file" if stop_receipt.result_path else "",
            "download_url": "/api/program/file?download=true" if stop_receipt.result_path else "",
            "deleted_file_count": stop_receipt.deleted_file_count,
            "result_error": stop_receipt.result_error,
        }
    payload = {
        "title": project.bible.title if project.bible else "Production",
        "phase": phase,
        "now": now,
        "waiting": waiting,
        "failed": bool(
            stop_receipt.result_error
            if stop_receipt is not None
            else farm_failed or finish_error or mix_error
        ),
        "camera": camera,
        "reach": {
            "host": str(line.get("host") or ""),
            "port": str(line.get("ssh_port") or ""),
            "up": bool(live),
            "error": last_gpu_error() if site_gpu and not live else "",
        },
        "shots": shots,
        "farmed": passed_shown if farm_failed else shown,
        "failed_clips": failed_clip_count,
        "goal": goal,
        "production_kind": production_kind,
        "sample_seconds": sample_seconds,
        "tally": tally,
        "tally_note": tally_note,
        "steps": steps,
        "farm": farm_view,
        "finish": finish_view,
        "mix": mix,
        "site": site.get("label") or "",
        "sequential": bool(site.get("sequential")),
        "split": site.get("split") or "",
        "clips": clips,
        "activity": activity,
        "progress": stage_progress,
        "rolling": bool(
            stop_receipt is None and (farm.get("busy") or finish.get("busy") or mix_busy)
        ),
        "can_stop": bool(
            stop_receipt is None
            and takes
            and (picture_open or mix_open or farm.get("busy") or finish.get("busy") or mix_busy)
        ),
        "stop": stopped_payload,
        "monitor": monitor,
        "clock": production_clock(
            project,
            rolling=bool(
                stop_receipt is None and (farm.get("busy") or finish.get("busy") or mix_busy)
            ),
            complete=bool(stop_receipt is not None or (not picture_open and not mix_open)),
        ),
    }
    payload["production_standard"] = production_standard(
        project,
        clips=clips,
        camera=camera,
        phase=phase,
    )
    payload["monitoring"] = _monitoring_summary(
        clips,
        phase=phase,
        now=now,
        failed=payload["failed"],
        monitor=monitor,
        activity=activity,
        progress=stage_progress,
        master_ready=bool(payload["production_standard"]["master_ready"]),
        material_label=str(payload["production_standard"]["material_label"]),
    )
    return payload
