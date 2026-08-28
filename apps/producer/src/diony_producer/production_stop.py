"""Durable Production STOP retention and incomplete-result handling."""

from __future__ import annotations

import hashlib
from pathlib import Path

from diony_contracts.enums import (
    ArtifactKind,
    ProductionStopDisposition,
    ProjectStatus,
    QCVerdict,
)
from diony_contracts.models import ProductionStopReceipt, Project, Take
from diony_contracts.stitch import clamp_overlap
from diony_finish.pipeline import stitch_masters
from diony_producer.farm import negative_lineage_valid
from diony_producer.vault import persist_project, record_activity, vault_root


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _selected_take(project: Project, shot_id: str) -> Take | None:
    shot = project.shot(shot_id)
    if not shot.selected_take_id:
        return None
    try:
        return project.take(shot.selected_take_id)
    except KeyError:
        return None


def completed_negative_takes(project: Project) -> list[Take]:
    """QC-passed, checksum-valid negatives in rundown order."""
    completed: list[Take] = []
    for shot in project.shots:
        take = _selected_take(project, shot.id)
        if take is None or take.qc is None or take.qc.verdict != QCVerdict.PASS:
            continue
        if negative_lineage_valid(project, take, verify_hash=True):
            completed.append(take)
    return completed


def record_production_stop(
    project: Project,
    disposition: ProductionStopDisposition,
) -> ProductionStopReceipt:
    """Persist STOP before cancellation so a restart cannot resume dispatch."""
    existing = project.production_stop
    if existing is not None:
        if existing.disposition != disposition:
            raise ValueError(
                f"Production already stopped with {existing.disposition.value} selected."
            )
        return existing
    receipt = ProductionStopReceipt(
        disposition=disposition,
        completed_take_ids=[take.id for take in completed_negative_takes(project)],
    )
    project.production_stop = receipt
    project.status = ProjectStatus.STOPPED
    # A stopped run has no reviewed master; evidence and deliverables that
    # described the previous mix would misdescribe the kept or deleted result.
    project.final_review = None
    project.deliverables = []
    project.mark_production_end()
    persist_project(project)
    return receipt


def _project_media_roots(project: Project) -> tuple[Path, Path]:
    project_root = vault_root() / "projects" / project.id
    return project_root / "takes", project_root / "program"


def _unlink_tree_files(root: Path, *, keep_take_ids: set[str] | None = None) -> int:
    if not root.is_dir():
        return 0
    deleted = 0
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_file():
            if keep_take_ids is not None:
                relative = path.relative_to(root)
                take_id = relative.parts[0] if relative.parts else ""
                if take_id in keep_take_ids:
                    continue
            path.unlink(missing_ok=True)
            deleted += 1
        elif path.is_dir():
            try:
                path.rmdir()
            except OSError:
                pass
    return deleted


def _strip_deleted_artifacts(project: Project) -> None:
    prefix = f"projects/{project.id}/"
    generated = {ArtifactKind.NEGATIVE_720, ArtifactKind.FINISH_1080, ArtifactKind.PROXY}
    for take in project.takes:
        removed = [
            artifact
            for artifact in take.artifacts
            if artifact.kind in generated and artifact.path.startswith(prefix)
        ]
        if not removed:
            continue
        removed_kinds = {artifact.kind for artifact in removed}
        take.artifacts = [artifact for artifact in take.artifacts if artifact not in removed]
        if ArtifactKind.NEGATIVE_720 in removed_kinds:
            take.asset_720p_id = None
        if ArtifactKind.FINISH_1080 in removed_kinds:
            take.asset_1080p_id = None


def _delete_generated_media(project: Project, receipt: ProductionStopReceipt) -> None:
    takes_root, program_root = _project_media_roots(project)
    receipt.deleted_file_count = _unlink_tree_files(takes_root)
    receipt.deleted_file_count += _unlink_tree_files(program_root)
    _strip_deleted_artifacts(project)
    if project.timeline:
        project.timeline.preview_path = ""
        project.timeline.picture_sha256 = ""
        project.timeline.program_sha256 = ""
        project.timeline.source_artifact_sha256s = []
        project.timeline.stitched = False


def _keep_completed_result(project: Project, receipt: ProductionStopReceipt) -> None:
    completed_ids = set(receipt.completed_take_ids)
    takes_root, _ = _project_media_roots(project)
    _unlink_tree_files(takes_root, keep_take_ids=completed_ids)
    if not completed_ids:
        return
    completed = [
        take
        for shot in project.shots
        if (take := _selected_take(project, shot.id)) is not None and take.id in completed_ids
    ]
    clips: list[tuple[Path, int, int]] = []
    source_hashes: list[str] = []
    for index, take in enumerate(completed):
        shot = project.shot(take.shot_id)
        artifact = next(
            item
            for item in take.artifacts
            if item.kind == ArtifactKind.NEGATIVE_720
            and item.path == f"projects/{project.id}/takes/{take.id}/negative_720.mp4"
        )
        clips.append(
            (
                vault_root() / artifact.path,
                clamp_overlap(shot, incoming=index > 0),
                shot.duration_frames,
            )
        )
        source_hashes.append(artifact.sha256)
    timeline_id = project.timeline.id if project.timeline else f"tl-{project.id}"
    rel = f"projects/{project.id}/program/{timeline_id}/incomplete_720.mov"
    destination = vault_root() / rel
    stitch_masters(clips, destination, fps=project.bible.fps)
    if not destination.is_file() or destination.stat().st_size <= 32:
        raise RuntimeError("Incomplete result was not written")
    receipt.result_path = rel
    receipt.result_sha256 = _sha256_file(destination)
    receipt.result_width = 1280
    receipt.result_height = 720
    receipt.result_error = ""
    if project.timeline:
        project.timeline.preview_path = rel
        project.timeline.picture_sha256 = ""
        project.timeline.program_sha256 = ""
        project.timeline.source_artifact_sha256s = source_hashes
        project.timeline.stitched = True


def finalize_production_stop(project: Project) -> ProductionStopReceipt:
    """Apply the already-recorded retention choice after workers are cancelled."""
    receipt = project.production_stop
    if receipt is None:
        raise ValueError("Production STOP is not recorded")
    if receipt.disposition == ProductionStopDisposition.DELETE:
        _delete_generated_media(project, receipt)
        detail = "Production stopped. Completed clips were deleted."
    else:
        try:
            _keep_completed_result(project, receipt)
        except Exception as exc:
            receipt.result_error = str(exc).strip()[:500] or "Incomplete result could not be stitched"
            persist_project(project)
            record_activity("run", f"Production stopped · {receipt.result_error}", level="fail")
            raise
        detail = (
            f"Production stopped. {len(receipt.completed_take_ids)} completed clips saved as an incomplete result."
            if receipt.completed_take_ids
            else "Production stopped. No completed clips were available to keep."
        )
    persist_project(project)
    record_activity("run", detail)
    return receipt
