"""Post-mix program final review, subtitle sidecars, and deliverable manifest.

Blueprint 24. Before Mix locks, the program master is probed, sampled, and
measured against the Brief and Bible it was promised under. The review is
written beside the master (`final-review.json`), the authored dialogue is
delivered as `.srt` / `.vtt` sidecars, and the QC packet (review, decision
log, per-Take receipts) becomes a listed Deliverable. A `fail` review refuses
the mix lock; a `revise` review locks with the evidence on the record.
"""

from __future__ import annotations

import json
from pathlib import Path

from diony_contracts.enums import DeliverableKind, FinishIntent, QCVerdict
from diony_contracts.models import Deliverable, Project
from diony_contracts.montage import (
    MOTION_RATIO_MIN,
    AudioSpotcheck,
    DecisionCategory,
    DecisionOption,
    FinalReview,
    PromisePreservation,
    ReviewStatus,
    SubtitleCheck,
    TechnicalProbe,
    VisualSpotcheck,
    dialogue_cues,
    duration_tolerance_seconds,
    evaluate_final_review,
    render_srt,
    render_vtt,
    skipped_final_review,
)
from diony_producer.decisions import read_decision_log, record_decision
from diony_producer.gallery_join import probe_media
from diony_producer.quality import (
    DEFAULT_SHARPNESS_MIN,
    measure_frame_quality,
)
from diony_producer.vault import record_activity, vault_root

SILENT_MIX_LUFS = -50.0
FRAMES_SAMPLED = 5


def _program_dir(project: Project) -> Path | None:
    if not project.timeline or not project.timeline.preview_path:
        return None
    return (vault_root() / project.timeline.preview_path).parent


def _sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def technical_probe(master: Path, *, bible_fps: float) -> TechnicalProbe:
    if not master.is_file() or master.stat().st_size <= 32:
        return TechnicalProbe(valid_container=False, issues=["program master is missing or empty"])
    media = probe_media(master, fallback_fps=bible_fps, fallback_frames=0, fallback_width=0, fallback_height=0)
    width = int(media.get("width") or 0)
    height = int(media.get("height") or 0)
    fps = float(media.get("fps") or 0.0)
    frames = int(media.get("frames") or 0)
    codec = str(media.get("codec") or "unknown")
    issues: list[str] = []
    valid = width > 0 and height > 0 and frames > 0 and codec != "unknown"
    if valid and (width, height) != (1920, 1080):
        issues.append(f"program master is {width}×{height}, not 1920×1080")
    return TechnicalProbe(
        valid_container=valid,
        width=width,
        height=height,
        fps=round(fps, 3),
        duration_seconds=round(frames / fps, 3) if fps > 0 else 0.0,
        frames=frames,
        has_audio=bool(media.get("has_audio")),
        codec=codec,
        pix_fmt=str(media.get("pix_fmt") or ""),
        issues=issues,
    )


def visual_spotcheck(master: Path, *, frames: int, fps: float | None = None) -> VisualSpotcheck:
    receipt = measure_frame_quality(master, frame_count=max(1, frames), fps=fps)
    if not receipt.measured:
        return VisualSpotcheck(issues=[f"visual spot-check unavailable: {receipt.error}"] if receipt.error else [])
    issues: list[str] = []
    if not receipt.sharpness_ok:
        issues.append(f"a sampled program frame is soft (sharpness {receipt.sharpness_min:.1f} < {DEFAULT_SHARPNESS_MIN:g})")
    if not receipt.exposure_ok and receipt.black_frames == 0:
        issues.append(
            f"sampled exposure outside range (luma {receipt.brightness_min:.0f}–{receipt.brightness_max:.0f})"
        )
    return VisualSpotcheck(
        frames_sampled=receipt.frame_count,
        black_frames=receipt.black_frames,
        low_sharpness_frames=0 if receipt.sharpness_ok else 1,
        exposure_issues=0 if receipt.exposure_ok or receipt.black_frames else 1,
        sharpness_min=receipt.sharpness_min,
        issues=issues,
    )


def audio_spotcheck(project: Project, probe: TechnicalProbe) -> AudioSpotcheck:
    dialogue_expected = any(shot.dialogue_turns or shot.audio_line for shot in project.shots)
    mix = project.mix
    measured = mix.measured_lufs if mix is not None else None
    true_peak = mix.true_peak_dbtp if mix is not None else None
    target = -24.0 if project.bible.loudness.value == "broadcast" else -16.0
    picture_only = bool(mix is not None and mix.picture_only)
    issues: list[str] = []
    silent: bool | None = None
    clipping: bool | None = None
    if not probe.has_audio:
        issues.append("program master carries no audio stream")
        silent = True
    elif measured is not None:
        silent = measured < SILENT_MIX_LUFS and not picture_only
    if true_peak is not None:
        clipping = true_peak > float(project.bible.true_peak_dbtp)
    return AudioSpotcheck(
        dialogue_expected=dialogue_expected,
        dialogue_present=(not silent) if silent is not None and dialogue_expected else None,
        measured_lufs=measured,
        true_peak_dbtp=true_peak,
        target_lufs=target,
        silent=silent,
        clipping=clipping,
        issues=issues,
    )


def promise_preservation(project: Project, probe: TechnicalProbe) -> PromisePreservation:
    brief = project.brief
    target_seconds: float | None = None
    if brief is not None and brief.target_minutes <= 15 and brief.production_kind.value != "sample":
        target_seconds = float(brief.target_minutes) * 60.0
    elif brief is not None and brief.production_kind.value == "sample":
        target_seconds = float(brief.sample_seconds or 5)
    timeline_seconds = (
        project.timeline.total_frames / float(project.timeline.fps or 23.976) if project.timeline else 0.0
    )
    delivered = probe.duration_seconds or timeline_seconds
    duration_ok: bool | None = None
    issues: list[str] = []
    if target_seconds is not None:
        tolerance = duration_tolerance_seconds(target_seconds)
        # A rundown may grow past the goal to keep every authored line (dialogue
        # fit); overshoot is a recorded conductor note, never a promise miss.
        duration_ok = delivered >= target_seconds - tolerance
    fps_ok = abs(probe.fps - float(project.bible.fps)) < 0.01 if probe.fps else None
    mix = project.mix
    loudness_ok: bool | None = None
    if mix is not None and mix.measured_lufs is not None and not mix.picture_only:
        target = -24.0 if project.bible.loudness.value == "broadcast" else -16.0
        loudness_ok = abs(mix.measured_lufs - target) <= 1.0
    measured_motion: list[bool] = []
    settled = 0
    failed = 0
    for shot in project.shots:
        take = next((item for item in project.takes if item.id == shot.selected_take_id), None)
        if take is None or take.qc is None:
            continue
        if take.qc.settled_by_policy:
            settled += 1
        if take.qc.verdict == QCVerdict.FAIL:
            failed += 1
        if take.qc.temporal_delta_p95 is not None and take.qc.motion_ok is not None:
            measured_motion.append(bool(take.qc.motion_ok))
    motion_ratio = (sum(measured_motion) / len(measured_motion)) if measured_motion else None
    motion_ok = (motion_ratio >= MOTION_RATIO_MIN) if motion_ratio is not None else None
    if settled:
        issues.append(f"{settled} print(s) proceed by retake-cap policy (WARN)")
    return PromisePreservation(
        target_seconds=target_seconds,
        delivered_seconds=round(delivered, 3),
        duration_ok=duration_ok,
        fps_ok=fps_ok,
        loudness_ok=loudness_ok,
        motion_ratio=round(motion_ratio, 4) if motion_ratio is not None else None,
        motion_ok=motion_ok,
        settled_prints=settled,
        failed_prints=failed,
        issues=issues,
    )


def write_subtitles(project: Project, program_dir: Path) -> tuple[SubtitleCheck, list[Path]]:
    cues = dialogue_cues(project)
    expected = bool(cues)
    if not expected:
        return SubtitleCheck(expected=False, present=False, cue_count=0), []
    written: list[Path] = []
    try:
        program_dir.mkdir(parents=True, exist_ok=True)
        srt = program_dir / "master_1080.srt"
        vtt = program_dir / "master_1080.vtt"
        srt.write_text(render_srt(cues), encoding="utf-8")
        vtt.write_text(render_vtt(cues), encoding="utf-8")
        written = [srt, vtt]
    except OSError as exc:
        return SubtitleCheck(expected=True, present=False, cue_count=len(cues), issues=[f"subtitle sidecar not written: {exc}"]), []
    return SubtitleCheck(expected=True, present=True, cue_count=len(cues)), written


def _deliverable(project: Project, kind: DeliverableKind, path: Path, suffix: str) -> Deliverable:
    rel = str(path.relative_to(vault_root())) if path.is_absolute() and str(path).startswith(str(vault_root())) else str(path)
    return Deliverable(
        id=f"dlv-{project.id}-{suffix}",
        kind=kind,
        path=rel,
        sha256=_sha256_file(path) if path.is_file() else None,
        finish=FinishIntent.DELIVERABLE,
    )


def write_qc_packet(project: Project, program_dir: Path, review: FinalReview) -> Path:
    packet = {
        "version": "diony-qc-packet-v1",
        "project_id": project.id,
        "timeline_id": project.timeline.id if project.timeline else "",
        "final_review": review.model_dump(mode="json"),
        "rundown_review": project.rundown_review.model_dump(mode="json") if project.rundown_review else None,
        "decision_log": read_decision_log(project.id),
        "takes": [
            {
                "shot_id": shot.id,
                "take_id": take.id,
                "verdict": take.qc.verdict.value if take.qc else None,
                "settled_best_of": take.qc.settled_best_of if take.qc else None,
                "qc": take.qc.model_dump(mode="json") if take.qc else None,
                "generation_receipt": take.generation_receipt.model_dump(mode="json") if take.generation_receipt else None,
            }
            for shot in project.shots
            if (take := next((item for item in project.takes if item.id == shot.selected_take_id), None)) is not None
        ],
        "mix": project.mix.model_dump(mode="json") if project.mix else None,
    }
    path = program_dir / "qc-packet.json"
    path.write_text(json.dumps(packet, indent=2, sort_keys=True), encoding="utf-8")
    return path


def current_master_sha256(project: Project) -> str:
    """The mixed program's hash when known, else the picture hash, else empty."""
    timeline = project.timeline
    if timeline is None:
        return ""
    return timeline.program_sha256 or timeline.picture_sha256 or ""


def final_review_current(project: Project) -> FinalReview | None:
    """The stored review when it measured exactly the master on the timeline now."""
    review = project.final_review
    timeline = project.timeline
    if review is None or timeline is None or review.status == ReviewStatus.SKIPPED:
        return None
    digest = current_master_sha256(project)
    if not digest or review.master_sha256 != digest or review.timeline_id != timeline.id:
        return None
    return review


def final_review_refuses_current_master(project: Project) -> bool:
    review = final_review_current(project)
    return review is not None and review.status == ReviewStatus.FAIL


def run_final_review(project: Project, *, live: bool) -> FinalReview:
    """Measure the mixed program master and record the deliverables. Never raises.

    A master already measured (same timeline, same bytes) returns its stored
    review: the advance controller polls every second and must not pay a
    probe, five decodes, and a decision-log entry each time.
    """
    timeline_id = project.timeline.id if project.timeline else ""
    master_rel = project.timeline.preview_path if project.timeline else ""
    program_dir = _program_dir(project)
    if not live or program_dir is None or not master_rel:
        review = skipped_final_review(timeline_id, master_rel, "final review runs on a live 1080 program master")
        project.final_review = review
        return review
    cached = final_review_current(project)
    if cached is not None:
        return cached
    master = vault_root() / master_rel
    master_sha256 = current_master_sha256(project) or (_sha256_file(master) if master.is_file() else "")
    probe = technical_probe(master, bible_fps=float(project.bible.fps))
    visual = (
        visual_spotcheck(master, frames=probe.frames, fps=probe.fps or float(project.bible.fps))
        if probe.valid_container
        else VisualSpotcheck()
    )
    audio = audio_spotcheck(project, probe)
    promise = promise_preservation(project, probe)
    subtitles, subtitle_paths = write_subtitles(project, program_dir)
    review = evaluate_final_review(
        timeline_id=timeline_id,
        master_path=master_rel,
        master_sha256=master_sha256,
        technical=probe,
        visual=visual,
        audio=audio,
        promise=promise,
        subtitles=subtitles,
    )
    project.final_review = review
    try:
        (program_dir / "final-review.json").write_text(review.model_dump_json(indent=2), encoding="utf-8")
        packet = write_qc_packet(project, program_dir, review)
    except OSError as exc:
        record_activity("qc", f"final review could not be written: {exc}", level="warn")
        packet = None
    deliverables: list[Deliverable] = []
    if master.is_file():
        deliverables.append(_deliverable(project, DeliverableKind.PRORES_HQ, master, "master"))
    stems_dir = program_dir / "stems"
    if stems_dir.is_dir():
        stems = sorted(stems_dir.glob("program-mix-v*.wav"))
        if stems:
            deliverables.append(_deliverable(project, DeliverableKind.STEMS, stems[-1], "stems"))
    for path in subtitle_paths:
        deliverables.append(_deliverable(project, DeliverableKind.SUBTITLES, path, path.suffix.lstrip(".")))
    if packet is not None:
        deliverables.append(_deliverable(project, DeliverableKind.QC_PACKET, packet, "qc-packet"))
    project.deliverables = deliverables
    record_decision(
        project,
        stage="mix",
        category=DecisionCategory.FINAL_REVIEW,
        subject="program final review",
        options=[
            DecisionOption(id="present", label="present the master", rejected_because="" if review.status == ReviewStatus.PASS else "issues found"),
            DecisionOption(id="revise", label="lock with issues on record", rejected_because="" if review.status == ReviewStatus.REVISE else "not the measured outcome"),
            DecisionOption(id="block", label="refuse the mix lock", rejected_because="" if review.status == ReviewStatus.FAIL else "not the measured outcome"),
        ],
        selected={ReviewStatus.PASS: "present", ReviewStatus.REVISE: "revise"}.get(review.status, "block"),
        reason="; ".join(review.issues_found)[:500] or "every measured section passed",
        confidence=0.85,
    )
    record_activity(
        "qc",
        f"Final review {review.status.value}: "
        + ("; ".join(review.issues_found) if review.issues_found else "container, frames, audio, promise, and subtitles measured clean")
        + f" · {len(deliverables)} deliverable(s) listed",
        level="live" if review.status != ReviewStatus.PASS else "info",
    )
    return review


__all__ = ["final_review_current", "final_review_refuses_current_master", "run_final_review", "technical_probe", "visual_spotcheck", "audio_spotcheck", "promise_preservation", "write_subtitles"]
